import datetime as dt
import os
import re

import pytest

from outreach import db, linkedin, messages, qualify, scoring

TODAY = dt.date(2026, 11, 7)
VERBATIM = ("hey pat, {who} building something in gpu financing. i'd love to have a "
            "quick 15 minute chat to learn more about this space. i have a yc interview "
            "on monday so anything this weekend would be really appreciated.")


def test_same_sentence_rule_blocks_split_evidence(cfg):
    p = {"title": "Credit analyst.", "why_relevant": "Firm also owns GPU servers.",
         "hook": "", "segment": "bank", "source_url": "https://x.example"}
    assert qualify.qualify(p, cfg)["fit"] == "adjacent"


def test_confirmed_when_terms_share_a_sentence(cfg, lender):
    verdict = qualify.qualify(lender, cfg)
    assert verdict["fit"] == "confirmed"
    assert "GPU" in verdict["evidence"]


def test_no_source_is_blocked(cfg, lender):
    assert qualify.qualify({**lender, "source_url": ""}, cfg)["fit"] == "no_source"


@pytest.mark.parametrize("segment", ["competitor", "journalist"])
def test_do_not_contact_segments(cfg, lender, segment):
    assert qualify.qualify({**lender, "segment": segment}, cfg)["fit"] == "excluded"


def test_score_matches_plural_and_explains(cfg, lender):
    result = scoring.score_prospect({**lender, "hook": "Lends against GPUs."}, cfg)
    assert any(m["keyword"] == "gpu" for m in
               result["breakdown"]["categories"]["asset"]["matched"])
    assert 0 < result["total"] <= 100


def test_cmu_link_raises_score(cfg, lender):
    plain = scoring.score_prospect({**lender, "title": "Director"}, cfg)["total"]
    warm = scoring.score_prospect({**lender, "title": "Director", "cmu_link": 1}, cfg)["total"]
    assert warm > plain


def test_no_opener_still_drafts_cleanly(cfg, lender):
    drafts = messages.generate({**lender, "opener": ""}, cfg)
    assert drafts
    for d in drafts:
        assert "\n\n\n" not in d["body"] and "  " not in d["body"]


def test_no_unfilled_placeholders_and_note_fits(cfg, lender):
    drafts = messages.generate({**lender, "x_handle": "@pat"}, cfg, TODAY)
    assert {d["channel"] for d in drafts} == {
        "linkedin_note", "linkedin_dm", "x_dm", "email", "followup"}
    for d in drafts:
        assert not re.search(r"[{}]", d["body"])


def test_topic_order_override_then_role_then_segment(cfg):
    base = {"segment": "neocloud", "title": "Chief Financial Officer"}
    assert "your lenders" in messages.topic_for(base, cfg)
    assert messages.topic_for({**base, "title": "Head of Sales"}, cfg) == \
        cfg["templates"]["segment_topics"]["neocloud"]
    assert messages.topic_for({**base, "topic": "you buy GPUs"}, cfg) == "you buy GPUs"


def test_finance_rule_does_not_fire_for_a_lender(cfg):
    p = {"segment": "abf_specialty", "title": "Head of Asset-Based Finance"}
    assert "your lenders" not in messages.topic_for(p, cfg)


def test_all_messages_are_lower_case(cfg, lender):
    for d in messages.generate({**lender, "x_handle": "pat"}, cfg, TODAY):
        assert d["body"] == d["body"].lower() and d["subject"] == d["subject"].lower()


def test_fact_appears_in_long_messages(cfg, lender):
    email = next(d for d in messages.generate(lender, cfg) if d["channel"] == "email")
    assert "fun fact" in email["body"]


@pytest.mark.parametrize("firm", ["CoreWeave", "Quinn Emanuel"])
def test_no_fact_about_own_firm(cfg, firm):
    assert messages.fact_for({"segment": "lawyer", "firm": firm}, cfg) is None


def test_alumni_get_a_warm_intro_that_fits(cfg, lender):
    drafts = messages.generate({**lender, "cmu_link": 1}, cfg, TODAY)
    assert "tartan" in drafts[0]["body"]
    assert "tartan" not in messages.generate(lender, cfg, TODAY)[0]["body"]


def test_connection_note_has_no_booking_link(cfg, lender):
    note = messages.generate(lender, cfg, TODAY)[0]
    assert note["channel"] == "linkedin_note"
    assert "cal.com" not in note["body"] and "http" not in note["body"]


def test_alum_note_changes_only_the_intro(cfg, lender):
    c = {**cfg, "templates": {**cfg["templates"], "lowercase": True}}
    note = messages.generate({**lender, "cmu_link": 1}, c, TODAY)[0]["body"]
    assert note == VERBATIM.format(who="i'm a fellow tartan")


def test_lowercase_can_be_turned_off(cfg, lender):
    c = {**cfg, "templates": {**cfg["templates"], "lowercase": False}}
    note = messages.generate(lender, c, TODAY)[0]["body"]
    assert note.startswith("Hey Pat, I'm a Carnegie Mellon student")


def test_timing_names_the_day_then_changes(cfg):
    assert "on Monday" in messages.timing(cfg, TODAY)[0]
    assert "just had" in messages.timing(cfg, dt.date(2026, 11, 10))[0]


def test_enabled_channels_limit_the_drafts(cfg, lender):
    c = {**cfg, "templates": {**cfg["templates"], "enabled_channels": ["linkedin_note"]}}
    p = {**lender, "x_handle": "pat", "email": "a@b.example"}
    assert [d["channel"] for d in messages.generate(p, c)] == ["linkedin_note"]
    assert messages.best_channel(p, c) == "linkedin_note"


def test_note_fits_linkedin_limit(cfg, lender):
    note = messages.generate({**lender, "name": "Maximilian Long"}, cfg, TODAY)[0]
    assert note["chars"] <= 300 and not note["over"]


@pytest.mark.parametrize("found,wanted,ok", [
    ("Patrick McGregor", "Patrick McGregor", True),
    ("Patrick McGregor, PhD", "Patrick McGregor", True),
    ("Patrick (PJ) Hart", "Patrick Hart", True),
    ("Patrick Mcgregor Jr", "Pat McGregor", False),
    ("Jane Doe", "John Doe", False)])
def test_name_match_is_strict(found, wanted, ok):
    assert linkedin.same_person(found, wanted) is ok


def test_firm_term_skips_filler_words():
    assert linkedin.firm_term("Victory Park Capital (VPC)") == "victory"
    assert linkedin.firm_term("The Carlyle Group") == "carlyle"
    assert linkedin.firm_term("") == ""


@pytest.mark.parametrize("text,url,stop", [
    ("You've reached the weekly invitation limit", "https://www.linkedin.com/in/a", True),
    ("hello", "https://www.linkedin.com/checkpoint/challenge/x", True),
    ("Invitation sent", "https://www.linkedin.com/in/a", False)])
def test_stop_conditions(text, url, stop):
    assert bool(linkedin.stop_reason(text, url)) is stop


def test_skipped_people_are_not_retried(tmp_path, monkeypatch):
    log = tmp_path / "log.csv"
    log.write_text("time,name,firm,result,detail,url\n"
                   "t,Sent One,F,sent,ok,u\nt,Skip One,F,skipped,no match,\n")
    monkeypatch.setattr(linkedin, "LOG_PATH", str(log))
    assert linkedin.skipped_names() == {"Skip One"}


def test_keep_going_stops_at_the_deadline():
    assert linkedin.keep_going(dt.datetime(2020, 1, 1)) == 0


def test_seconds_to_midnight():
    assert linkedin.seconds_to_midnight(dt.datetime(2026, 11, 7, 23, 0)) == 3660


def test_no_x_draft_without_handle(cfg, lender):
    assert "x_dm" not in {d["channel"] for d in messages.generate(lender, cfg)}


def test_messages_do_not_pitch_or_claim_traction(cfg, lender):
    text = " ".join(d["body"] for d in messages.generate(lender, cfg)).lower()
    for claim in ("yc-backed", "backed by y", "our customers", "our clients", "pilot",
                  "we offer", "our product", "demo"):
        assert claim not in text


def test_reimport_keeps_pipeline_state(tmp_path, lender):
    path = str(tmp_path / "t.db")
    db.init_db(path)
    with db.connect(path) as conn:
        pid = db.upsert_prospect(conn, lender)
        db.set_stage(conn, pid, "sent", "email")
        db.set_notes(conn, pid, "good call")
        again = db.upsert_prospect(conn, {**lender, "title": "Partner", "hook": ""})
        row = db.get_prospect(conn, pid)
    assert again == pid
    assert (row["stage"], row["notes"], row["title"]) == ("sent", "good call", "Partner")
    assert row["hook"] == lender["hook"]


def test_bad_stage_is_rejected(tmp_path, lender):
    path = str(tmp_path / "t.db")
    db.init_db(path)
    with db.connect(path) as conn:
        pid = db.upsert_prospect(conn, lender)
        with pytest.raises(ValueError):
            db.set_stage(conn, pid, "nonsense")


def test_followup_due_only_once(tmp_path, lender):
    path = str(tmp_path / "t.db")
    db.init_db(path)
    with db.connect(path) as conn:
        pid = db.upsert_prospect(conn, lender)
        db.save_score(conn, pid, 80, {})
        db.set_stage(conn, pid, "sent", "email")
        assert [r["id"] for r in db.followups_due(conn, 0)] == [pid]
        db.mark_followed_up(conn, pid)
        assert db.followups_due(conn, 0) == []


def test_run_scores_without_identity(tmp_path, monkeypatch, lender):
    import cli
    from outreach import config
    path = str(tmp_path / "t.db")
    monkeypatch.setattr(db, "DB_PATH", path)
    monkeypatch.setattr(config, "LOCAL_PATH", str(tmp_path / "absent.yaml"))
    config.load_config.cache_clear()
    db.init_db(path)
    with db.connect(path) as conn:
        pid = db.upsert_prospect(conn, lender)
    real = db.connect
    monkeypatch.setattr(db, "connect", lambda p=None: real(path))
    cli.cmd_run(None)
    config.load_config.cache_clear()
    with real(path) as conn:
        assert db.get_prospect(conn, pid)["score"] is not None


def test_alumni_rank_first(tmp_path, lender):
    path = str(tmp_path / "t.db")
    db.init_db(path)
    with db.connect(path) as conn:
        top = db.upsert_prospect(conn, {**lender, "name": "Top Scorer"})
        alum = db.upsert_prospect(conn, {**lender, "name": "An Alum", "cmu_link": True,
                                         "opener": ""})
        db.save_score(conn, top, 95, {})
        db.save_score(conn, alum, 40, {})
        assert [r["id"] for r in db.ranked(conn)] == [alum, top]


# X research and YC wording

def _user(handle, bio, name="Sam Lee", followers=500):
    return {"id": handle, "username": handle, "name": name, "description": bio,
            "public_metrics": {"followers_count": followers}}


def _x_cfg():
    return {"page_size": 25, "user_queries": {"insurer": ["q"]}, "post_queries": {}}


def _no_cache(tmp_path):
    return str(tmp_path / "cache.json")


@pytest.mark.parametrize("bio,reason", [
    ("Group Partner at Y Combinator", "YC partner"),
    ("Visiting Group Partner, YC", "YC partner"),
    ("YC Group Partner. Founder of Foo.", "YC partner"),
    ("YC partner", "YC partner"),
    ("partner @ycombinator", "YC partner"),
    ("Former partner at YC", "YC partner"),
    ("Software engineer at Y Combinator", "works at YC itself"),
    ("working @ycombinator", "works at YC itself"),
    ("tech reporter at a paper", "press"),
])
def test_block_reason_stops_yc_itself_and_press(bio, reason):
    from outreach import x_api
    assert x_api.block_reason(_user("a", bio), set(), set()) == reason


@pytest.mark.parametrize("bio", [
    "CEO of Foo (YC W23)",
    "Founder at YC-backed Foo",
    "Engineer at YC S24 startup",
    "YC W21 founder, partner at Acme Capital",
    "Y Combinator alum. Partner at Bar Ventures.",
    "Partner at Acme Credit. Co-founder, Foo (YC S19)",
    "Partner at a private credit fund",
    "Backed by @YCombinator and others",
    "@ycombinator S26. Building inference chips",
])
def test_block_reason_allows_yc_company_people(bio):
    from outreach import x_api
    assert x_api.block_reason(_user("a", bio), set(), set()) is None


def test_yc_company_person_ranks_as_a_link():
    from outreach import x_api
    assert x_api.to_candidate(_user("a", "CEO of Foo (YC W23)"), "founder")["yc_company"]


def test_block_reason_stops_known_people():
    from outreach import x_api
    assert x_api.block_reason(_user("known", "credit"), {"known"}, set())
    assert x_api.block_reason(_user("z", "credit"), set(), {"sam lee"})


def test_run_search_ranks_links_first_and_merges(tmp_path):
    from outreach import x_api
    payload = {"data": [_user("plain", "credit underwriter", "Pat A", 9000),
                        _user("alum", "CMU Tepper grad, private credit", "Jo B"),
                        _user("staff", "partner at YC", "Kim C")]}
    client = x_api.Client("t", 100, _no_cache(tmp_path), fetch=lambda url: payload)
    result = x_api.run_search(client, _x_cfg(), set(), set())
    assert [p["x_handle"] for p in result["candidates"]] == ["alum", "plain"]
    assert result["candidates"][0]["cmu_link"] is True
    assert [b["x_handle"] for b in result["blocked"]] == ["staff"]


def test_client_cache_stops_a_second_charge(tmp_path):
    from outreach import x_api
    calls = []

    def fetch(url):
        calls.append(url)
        return {"data": [_user("a", "bio")]}

    path = _no_cache(tmp_path)
    x_api.Client("t", 100, path, fetch=fetch).search_users("q", 25)
    again = x_api.Client("t", 100, path, fetch=fetch)
    again.search_users("q", 25)
    assert len(calls) == 1 and again.used == 0


def test_client_stops_at_the_limit_and_keeps_results(tmp_path):
    from outreach import x_api

    def fetch(url):
        return {"data": [_user("h" + str(abs(hash(url))), "bio")]}

    cfg = {"page_size": 25, "user_queries": {"insurer": ["a", "b", "c"]}, "post_queries": {}}
    client = x_api.Client("t", 1, _no_cache(tmp_path), fetch=fetch)
    result = x_api.run_search(client, cfg, set(), set())
    assert len(result["candidates"]) == 1 and "limit" in result["error"]


def test_x_search_never_writes_the_database(tmp_path, monkeypatch):
    import cli
    from outreach import db, x_api
    path = str(tmp_path / "t.db")
    monkeypatch.setattr(db, "DB_PATH", path)
    db.init_db(path)
    monkeypatch.setenv("X_BEARER_TOKEN", "t")
    monkeypatch.setattr(x_api, "ENV_PATH", str(tmp_path / "none.env"))
    monkeypatch.setattr(x_api, "OUT_PATH", str(tmp_path / "out.json"))
    monkeypatch.setattr(x_api.Client, "_http", lambda self, url: {"data": [_user("a", "bio")]})
    monkeypatch.setattr(x_api, "CACHE_PATH", _no_cache(tmp_path))
    assert cli.main(["x-search"]) == 0
    assert (tmp_path / "out.json").exists()    # The real review file stays untouched.
    with db.connect(path) as conn:
        assert db.all_prospects(conn) == []


def test_x_search_needs_a_token(tmp_path, monkeypatch, capsys):
    import cli
    from outreach import x_api
    monkeypatch.setattr(x_api, "ENV_PATH", str(tmp_path / "none.env"))
    monkeypatch.delenv("X_BEARER_TOKEN", raising=False)
    assert cli.main(["x-search"]) == 1
    assert "X_BEARER_TOKEN" in capsys.readouterr().out


def test_oauth1_signature_matches_the_published_example():
    from outreach import x_api
    params = {
        "include_entities": "true",
        "oauth_consumer_key": "xvz1evFS4wEEPTGEFPHBog",
        "oauth_nonce": "kYjzVBB8Y0ZFabxSWbWovY3uYSQ2pTgmZeNu2VS4cg",
        "oauth_signature_method": "HMAC-SHA1",
        "oauth_timestamp": "1318622958",
        "oauth_token": "370773112-GmHxMAgYyLbNEtIKZeRNFsMKPR9EyMZeS9weJAEb",
        "oauth_version": "1.0",
        "status": "Hello Ladies + Gentlemen, a signed OAuth request!",
    }
    assert x_api.oauth1_signature(
        "POST", "https://api.twitter.com/1.1/statuses/update.json", params,
        "kAcSOqF21Fu85e7zjz7ZN2U4ZRhfV3WpwPAoE3Z7kBw",
        "LswwdoUaIvS8ltyTt5jkRh4J50vUPVVHtR2YPi5kE") == "hCtSmYh+iHYCEqBWrE7C7hYmtUk="


def test_a_refused_user_search_still_runs_post_search(tmp_path):
    from outreach import x_api

    def fetch(url):
        if "/users/search" in url:
            raise x_api.XError("X API returned 403")
        return {"data": [{"id": "1", "author_id": "u", "text": "gpu loans"}],
                "includes": {"users": [{**_user("poster", "credit"), "id": "u"}]}}

    cfg = {"page_size": 25, "user_queries": {"insurer": ["q"]},
           "post_queries": {"insurer": ["p"]}}
    client = x_api.Client("t", 100, _no_cache(tmp_path), fetch=fetch)
    result = x_api.run_search(client, cfg, set(), set())
    assert [p["x_handle"] for p in result["candidates"]] == ["poster"]
    assert "User search" in result["error"]


def test_user_search_is_signed_and_post_search_uses_the_bearer_token(tmp_path, monkeypatch):
    from outreach import x_api
    seen = {}

    class Reply:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return b'{"data": []}'

    def opener(req, timeout):
        seen[req.full_url.split("?")[0].rsplit("/", 1)[-1]] = req.get_header("Authorization")
        return Reply()

    monkeypatch.setattr(x_api.urllib.request, "urlopen", opener)
    keys = {k: "v" for k in x_api.OAUTH_ENV}
    client = x_api.Client("tok", 100, _no_cache(tmp_path), oauth=keys)
    client.search_users("private credit", 25)
    client.search_posts("gpu", 25)
    assert seen["search"].startswith("OAuth ") and 'oauth_signature="' in seen["search"]
    assert seen["recent"] == "Bearer tok"


def test_a_post_found_by_two_queries_is_kept_once(tmp_path):
    from outreach import x_api

    def fetch(url):
        return {"data": [{"id": "1", "author_id": "u", "text": "gpu loans"}],
                "includes": {"users": [{**_user("poster", "credit"), "id": "u"}]}}

    cfg = {"page_size": 25, "user_queries": {}, "post_queries": {"insurer": ["a", "b"]}}
    client = x_api.Client("t", 100, _no_cache(tmp_path), fetch=fetch)
    person = x_api.run_search(client, cfg, set(), set())["candidates"][0]
    assert len(person["posts"]) == 1 and len(person["matched"]) == 2


def _broad_cfg(pages=3):
    return {"page_size": 25, "user_queries": {}, "post_queries": {},
            "broad": {"page_size": 50, "max_pages": pages, "queries": ["CMU", "YC W24"],
                      "keep_terms": ["credit", "\\bgpu"]}}


def test_broad_search_keeps_only_relevant_bios_and_follows_pages(tmp_path):
    from outreach import x_api
    calls = []

    def fetch(url):
        calls.append(url)
        if "next_token=p2" in url:
            return {"data": [_user("second", "GPU cloud, CMU alum", "Bo D")]}
        if "query=CMU" in url:
            return {"data": [_user("fit", "CMU, private credit", "Al E"),
                             _user("off", "CMU cat photos", "Cy F")],
                    "meta": {"next_token": "p2"}}
        return {"data": []}

    client = x_api.Client("t", 100, _no_cache(tmp_path), fetch=fetch)
    result = x_api.run_search(client, _broad_cfg(), set(), set())
    assert {p["x_handle"] for p in result["candidates"]} == {"fit", "second"}
    assert result["candidates"][0]["segment"] == "unsorted"
    assert len(calls) == 3                 # Empty queries get no second page.


def test_broad_search_gives_each_query_a_first_page_before_a_second(tmp_path):
    from outreach import x_api
    calls = []

    def fetch(url):
        calls.append(url)
        return {"data": [_user(str(len(calls)), "x")], "meta": {"next_token": f"n{len(calls)}"}}

    client = x_api.Client("t", 100, _no_cache(tmp_path), fetch=fetch)
    x_api.run_search(client, _broad_cfg(2), set(), set())
    assert ["next_token" in u for u in calls] == [False, False, True, True]


def test_broad_search_finds_and_blocks_a_real_yc_partner(tmp_path):
    from outreach import x_api
    payload = {"data": [_user("gp", "Group Partner at Y Combinator, credit nerd", "Di G")]}
    client = x_api.Client("t", 100, _no_cache(tmp_path), fetch=lambda u: payload)
    result = x_api.run_search(client, _broad_cfg(1), set(), set())
    assert result["candidates"] == [] and result["blocked"][0]["reason"] == "YC partner"


def test_load_env_reads_the_private_file_and_a_shell_value_wins(tmp_path, monkeypatch):
    from outreach import x_api
    path = tmp_path / "x.env"
    path.write_text("X_API_KEY=abc=def\nX_API_SECRET=\nOTHER=1\nX_ACCESS_TOKEN=file\n")
    monkeypatch.delenv("X_API_KEY", raising=False)
    monkeypatch.delenv("X_API_SECRET", raising=False)
    monkeypatch.delenv("OTHER", raising=False)
    monkeypatch.setenv("X_ACCESS_TOKEN", "shell")
    x_api.load_env(str(path))
    assert os.environ["X_API_KEY"] == "abc=def"
    assert "X_API_SECRET" not in os.environ and "OTHER" not in os.environ
    assert os.environ["X_ACCESS_TOKEN"] == "shell"
    monkeypatch.delenv("X_API_KEY")


# Backup X search through the browser

class FakePage:
    """Stands in for a Playwright page and records what the client does."""

    def __init__(self, results, body="home", url="https://x.com/search"):
        self.results, self.body, self.url, self.opened = results, body, url, []
        self.mouse = type("M", (), {"wheel": lambda *a: None})()

    def goto(self, url, wait_until=None):
        self.opened.append(url)

    def wait_for_selector(self, selector, timeout=None):
        return None

    def inner_text(self, selector):
        return self.body

    def evaluate(self, script):
        return self.results(script)


_BROWSER = {"max_searches": 5, "max_profiles": 5, "delay_seconds": 0,
            "scroll_seconds": 0, "scrolls": 1}


def _browser_client(page, tmp_path, **over):
    from outreach import x_browser
    return x_browser.BrowserClient(page, {**_BROWSER, **over}, _no_cache(tmp_path),
                                   sleep=lambda s: None)


def test_parse_user_cell_reads_the_live_row_layout():
    from outreach import x_browser
    user = x_browser.parse_user_cell({"href": "/konyk", "text": (
        "Kony | Hiring\n@konyk\nFollow\nBuilding \n@gaib_ai\n, GPU financing Investor: \n@a")})
    assert user["name"] == "Kony | Hiring"
    assert user["description"] == "Building @gaib_ai , GPU financing Investor: @a"
    assert "public_metrics" not in user
    assert x_browser.parse_user_cell({"href": "/search?q=a", "text": ""}) is None
    assert x_browser.parse_user_cell({"href": "/v", "text": "Victor\n@v\nFollow"})["description"] == ""


def test_browser_user_search_drops_empty_bios(tmp_path):
    rows = [{"href": "/v", "text": "Victor\n@v\nFollow"},
            {"href": "/b", "text": "Bo\n@b\nFollow\nCredit investor"}]
    client = _browser_client(FakePage(lambda s: rows), tmp_path)
    assert [u["username"] for u in client.search_users("q", 2)] == ["b"]


@pytest.mark.parametrize("text,count", [("1,234 Followers", 1234), ("12.3K Followers", 12300),
                                        ("2M Followers", 2000000), ("", 0)])
def test_parse_count(text, count):
    from outreach import x_browser
    assert x_browser.parse_count(text) == count


def test_browser_rows_pass_the_follower_filter_and_the_same_blocks(tmp_path):
    from outreach import x_api, x_browser
    rows = [{"href": "/keep", "text": "Al B\n@keep\nFollow\nCMU, private credit"},
            {"href": "/gp", "text": "Di G\n@gp\nFollow\nGroup Partner at Y Combinator, credit"}]
    client = _browser_client(FakePage(lambda s: rows), tmp_path)
    cfg = {"page_size": 25, "user_queries": {"insurer": ["q"]}, "post_queries": {}}
    result = x_api.run_search(client, cfg, set(), set(), min_followers=50)
    assert [p["x_handle"] for p in result["candidates"]] == ["keep"]
    assert result["blocked"][0]["reason"] == "YC partner"


def test_post_authors_get_a_profile_check_before_they_count(tmp_path):
    from outreach import x_api, x_browser

    def results(script):
        if script == x_browser.POSTS_JS:
            return [{"href": "/ok", "name": "Ok", "text": "gpu loans", "url": "/ok/status/1",
                     "date": "2026-11-06T01:00:00Z"},
                    {"href": "/gp", "name": "Gp", "text": "gpu", "url": "/gp/status/2", "date": ""}]
        bio = "Partner at YC" if page.opened[-1].endswith("/gp") else "credit investor"
        return {"name": "Name", "bio": bio, "location": "", "followers": "900 Followers"}

    page = FakePage(results)
    client = _browser_client(page, tmp_path)
    cfg = {"page_size": 2, "user_queries": {}, "post_queries": {"insurer": ["q"]}}
    result = x_api.run_search(client, cfg, set(), set(), min_followers=50)
    assert [p["x_handle"] for p in result["candidates"]] == ["ok"]
    assert result["candidates"][0]["posts"][0]["url"] == "https://x.com/ok/status/1"
    assert [b["x_handle"] for b in result["blocked"]] == ["gp"]


def test_no_profile_means_no_lead(tmp_path):
    from outreach import x_browser
    page = FakePage(lambda s: [{"href": "/a", "text": "t", "url": "/a/status/1", "date": ""}])
    client = _browser_client(page, tmp_path, max_profiles=0)
    assert client.search_posts("q", 1) == []


@pytest.mark.parametrize("url,body", [
    ("https://x.com/i/flow/login", "sign in"),
    ("https://x.com/search", "Rate limit exceeded."),
    ("https://x.com/search", "Your account has been locked."),
    ("https://x.com/i/jf/onboarding/web?mode=login", "code"),
    ("https://x.com/search", "We sent you a security code"),
])
def test_a_warning_stops_every_later_search(tmp_path, url, body):
    from outreach import x_api
    page = FakePage(lambda s: [], body=body, url=url)
    client = _browser_client(page, tmp_path)
    cfg = {"page_size": 2, "user_queries": {"insurer": ["a", "b"]},
           "post_queries": {"insurer": ["c"]}}
    result = x_api.run_search(client, cfg, set(), set())
    assert len(page.opened) == 1 and "X showed" in result["error"]


def test_browser_cache_stops_a_second_visit_and_the_limit_holds(tmp_path):
    from outreach import x_api
    page = FakePage(lambda s: [], body='No results for "q"')
    client = _browser_client(page, tmp_path, max_searches=1)
    client.search_users("q", 1)
    client.search_users("q", 1)
    assert len(page.opened) == 1
    with pytest.raises(x_api.XError, match="limit"):
        client.search_users("other", 1)


def test_browser_client_has_no_write_actions():
    from outreach import x_browser
    source = open(x_browser.__file__, encoding="utf-8").read()
    assert ".click(" not in source and ".fill(" not in source and ".type(" not in source


def test_an_unexplained_empty_page_stops_and_is_not_cached(tmp_path):
    from outreach import x_api
    client = _browser_client(FakePage(lambda s: [], body="home"), tmp_path)
    with pytest.raises(x_api.XError, match="no rows"):
        client.search_users("q", 1)
    assert client.cache == {} and client.stopped


def test_logged_in_needs_the_session_cookie():
    from outreach import x_browser
    assert x_browser.logged_in([{"name": "auth_token", "value": "a"}])
    assert not x_browser.logged_in([{"name": "guest_id", "value": "a"}])


def test_browser_search_stops_before_any_page_when_logged_out(monkeypatch, capsys, tmp_path):
    import cli
    from outreach import db, x_api, x_browser
    # Use an empty review file. The real one changes as the founder's searches run.
    monkeypatch.setattr(x_browser, "OUT_PATH", str(tmp_path / "browser.json"))

    def refuse(*args):
        raise x_api.XError("Chrome is not logged in to X.")

    monkeypatch.setattr(x_browser, "run_browser_search", refuse)
    monkeypatch.setattr(x_api, "write_review_file", lambda *a: pytest.fail("wrote the file"))
    assert cli.main(["x-search", "--browser", "--only", "q"]) == 1
    assert "not logged in" in capsys.readouterr().out


# X search cost controls and specificity

def _rel(text):
    return "gpu" in text.lower() and "loan" in text.lower()


def _posts_payload(n=2):
    return {"data": [{"id": str(i), "author_id": "u", "text": "gpu loans"} for i in range(n)],
            "includes": {"users": [{**_user("poster", "gpu loans"), "id": "u"}]},
            "meta": {"newest_id": "99"}}


def test_budget_stops_before_a_page_could_pass_it(tmp_path):
    from outreach import x_api
    calls = []

    def fetch(url):
        calls.append(url)
        return {"data": [_user("a", "bio")] * 25}

    client = x_api.Client("t", 10**6, _no_cache(tmp_path), fetch=fetch, max_usd=0.30)
    client.search_users("a", 25)             # 25 x $0.01 = $0.25.
    assert client.spent == pytest.approx(0.25)
    with pytest.raises(x_api.XError, match="budget"):
        client.search_users("b", 25)          # Another $0.25 would pass $0.30.
    assert len(calls) == 1


def test_post_pages_count_post_and_author_prices(tmp_path):
    from outreach import x_api
    client = x_api.Client("t", 100, _no_cache(tmp_path), fetch=lambda u: _posts_payload(4))
    client.search_posts("q", 10)
    assert client.spent == pytest.approx(4 * 0.005 + 0.01)


def test_post_search_asks_only_for_newer_posts(tmp_path):
    from outreach import x_api
    calls = []

    def fetch(url):
        calls.append(url)
        return _posts_payload()

    path = _no_cache(tmp_path)
    x_api.Client("t", 100, path, fetch=fetch).search_posts("q", 10)
    x_api.Client("t", 100, path, fetch=fetch).search_posts("q", 10)
    assert "since_id" not in calls[0] and "since_id=99" in calls[1]


def test_a_post_query_waits_for_its_daily_gap(tmp_path):
    from outreach import x_api
    calls = []

    def fetch(url):
        calls.append(url)
        return _posts_payload()

    path = _no_cache(tmp_path)
    x_api.Client("t", 100, path, fetch=fetch, min_gap_hours=24).search_posts("q", 10)
    again = x_api.Client("t", 100, path, fetch=fetch, min_gap_hours=24)
    assert again.search_posts("q", 10) == [] and len(calls) == 1


def test_stored_people_and_vague_people_are_not_added(tmp_path):
    from outreach import x_api
    payload = {"data": [_user("stored", "gpu loans", "A B"), _user("vague", "private credit", "C D"),
                        _user("fit", "I run gpu loans", "E F")]}
    client = x_api.Client("t", 100, _no_cache(tmp_path), fetch=lambda u: payload)
    cfg = {"page_size": 10, "user_queries": {"bank": ["q"]}, "post_queries": {}}
    result = x_api.run_search(client, cfg, set(), set(), 0, {"stored"}, _rel)
    assert [p["x_handle"] for p in result["candidates"]] == ["fit"]


def test_real_filter_needs_gpu_and_finance_in_one_sentence(cfg):
    from outreach import qualify
    hit = lambda t: qualify.evidence({"why_relevant": t}, cfg) is not None
    assert hit("We developed insurance to support asset based lending on GPUs.")
    assert hit("Building AI infra & GPU financing")
    assert not hit("Private credit investor")
    assert not hit("Partner, private equity and finance")
    assert not hit("GPU engineer. I like cats.")


def test_config_queries_are_specific(cfg):
    x = cfg["x_search"]
    assert x["max_usd"] <= 10 and "broad" not in x
    for queries in x["post_queries"].values():
        for q in queries:
            assert re.search(r"gpu|neocloud|compute", q, re.I)
    for queries in x["user_queries"].values():
        assert all(re.search(r"gpu|compute|neocloud|ai infrastructure", q, re.I) for q in queries)


def test_prune_and_merge_keep_people_with_a_relevant_post(tmp_path):
    from outreach import x_api
    path = str(tmp_path / "list.json")
    keep = {"x_handle": "cat", "why_relevant": "dad", "posts": [{"text": "gpu loans insured"}]}
    drop = {"x_handle": "off", "why_relevant": "finance", "posts": []}
    x_api.write_review_file({"candidates": [keep, drop], "blocked": [], "error": None}, path)
    stored = x_api.prune_review_file(_rel, path)
    assert [p["x_handle"] for p in stored["candidates"]] == ["cat"]
    assert x_api.seen_handles([path]) == {"cat"}
    merged = x_api.merge_results(stored, {"candidates": [keep, {"x_handle": "new"}],
                                          "blocked": [], "error": None})
    assert [p["x_handle"] for p in merged["candidates"]] == ["cat", "new"]


def test_browser_skips_profile_visits_for_stored_and_vague_posts(tmp_path):
    from outreach import x_browser
    rows = [{"href": "/old", "text": "gpu loans", "url": "/old/status/1", "date": ""},
            {"href": "/off", "text": "stocks", "url": "/off/status/2", "date": ""}]
    page = FakePage(lambda s: rows)
    client = _browser_client(page, tmp_path)
    client.skip, client.relevant = {"old"}, _rel
    assert client.search_posts("q", 2) == [] and client.profiles == 0


def test_top_tab_leaves_out_the_filter_and_cache_keys_differ(tmp_path):
    rows = [{"href": "/a", "text": "t", "url": "/a/status/1", "date": ""}]
    page = FakePage(lambda s: rows, body="x")
    client = _browser_client(page, tmp_path, post_tab="top", max_profiles=0)
    client.search_posts("q", 1)
    assert "f=" not in page.opened[0] and "posts|top|q" in client.cache


def test_browser_queries_file_replaces_the_config_queries(tmp_path, monkeypatch):
    import cli
    from outreach import db, x_api, x_browser
    path = str(tmp_path / "t.db")
    monkeypatch.setattr(db, "DB_PATH", path)
    db.init_db(path)
    monkeypatch.setattr(x_browser, "OUT_PATH", str(tmp_path / "b.json"))
    monkeypatch.setattr(x_api, "OUT_PATH", str(tmp_path / "a.json"))
    seen = {}

    def fake(cfg, handles, names, skip, relevant):
        seen.update(cfg)
        return {"candidates": [], "blocked": [], "error": None}, type(
            "C", (), {"used": 0, "profiles": 0})()

    monkeypatch.setattr(x_browser, "run_browser_search", fake)
    q = tmp_path / "r.yaml"
    q.write_text("post_queries:\n  insurer: ['gpu insurance']\nbrowser:\n  post_tab: top\n")
    assert cli.main(["x-search", "--browser", "--queries", str(q)]) == 0
    assert seen["post_queries"] == {"insurer": ["gpu insurance"]}
    assert seen["user_queries"] == {} and seen["browser"]["post_tab"] == "top"


class DialogBox:
    def __init__(self, text, overlay=False, visible=True):
        self.text, self.overlay, self.visible = text, overlay, visible

    def is_visible(self):
        return self.visible

    def inner_text(self):
        return self.text

    def evaluate(self, _script):
        return self.overlay


class DialogPage:
    url = "https://www.linkedin.com/in/someone/"

    def __init__(self, boxes, alerts=()):
        self.boxes, self.alerts = list(boxes), list(alerts)

    def query_selector_all(self, selector):
        return self.alerts if selector == '[role="alert"]' else self.boxes


def test_chat_overlay_is_not_the_invite_box():
    from outreach import linkedin
    chat = DialogBox("Zeeshan Khan\nClose your conversation with Zeeshan Khan and Jonathan Gong")
    overlay = DialogBox("no marker text", overlay=True)
    invite = DialogBox("Add a note to your invitation")
    assert linkedin._dialog(DialogPage([chat, overlay])) is None
    assert linkedin._dialog(DialogPage([chat, invite])) is invite


def test_chat_text_does_not_trigger_a_stop_but_an_alert_does():
    import pytest
    from outreach import linkedin
    word = next(iter(linkedin.STOP_TEXT)) if hasattr(linkedin, "STOP_TEXT") else "weekly invitation limit"
    chat = DialogBox(f"Close your conversation with A. They wrote: {word}")
    linkedin._check(DialogPage([chat]))
    with pytest.raises(linkedin.Stop):
        linkedin._check(DialogPage([chat], [DialogBox(word)]))


# --- X DM sender -------------------------------------------------------------

def _dm_cfg():
    return {"sender": {"interview_date": "2026-11-09"}}


def test_dm_text_uses_founder_format_and_tartan_line():
    import datetime as _dt
    from outreach import x_dm
    day = _dt.date(2026, 11, 7)
    plain = x_dm.dm_text({"name": "Alex Goston", "cmu_link": "0"}, _dm_cfg(), day)
    assert plain == ("hey alex, i'm a carnegie mellon student building something in gpu "
                     "financing. i'd love to have a quick 15 minute chat to learn more about "
                     "this space. i have a yc interview on monday so anything this weekend "
                     "would be really appreciated.")
    alum = x_dm.dm_text({"name": "Iris Tang", "cmu_link": "1"}, _dm_cfg(), day)
    assert alum.startswith("hey iris, i'm also a fellow tartan building something")
    assert x_dm.dm_text({"name": "Haircut", "greeting": "there"}, _dm_cfg(), day).startswith(
        "hey there,")


class _FakeHTTP:
    def __init__(self, sends):
        self.sends = list(sends)
        self.posts = []

    def __call__(self, method, url, headers, body=None):
        if "/users/by/username/" in url:
            return 200, {"data": {"id": "id-" + url.rsplit("/", 1)[-1]}}
        self.posts.append(url)
        return self.sends.pop(0)


def _dm_run(tmp_path, monkeypatch, sends, recipients):
    from outreach import x_dm
    monkeypatch.setattr(x_dm, "dnc_names", lambda: {"blocked person"})
    env = tmp_path / "x.env"
    env.write_text("X_ACCESS_TOKEN=t\n")
    http = _FakeHTTP(sends)
    client = x_dm.DMClient(http=http, env_path=str(env))
    log = str(tmp_path / "log.jsonl")
    result = x_dm.run(recipients, _dm_cfg(), send=True, client=client,
                      sleep=lambda s: None, log_path=log)
    return result, http, log


def test_dm_run_sends_logs_and_never_repeats(tmp_path, monkeypatch):
    from outreach import x_dm
    people = [{"handle": "a", "name": "Ann A"}, {"handle": "b", "name": "Blocked Person"}]
    result, http, log = _dm_run(tmp_path, monkeypatch, [(201, {"data": {"dm_event_id": "1"}})],
                                people)
    assert result["sent"] == 1 and len(http.posts) == 1
    assert x_dm.sent_handles(log) == {"a"}
    result2, http2, _ = _dm_run(tmp_path, monkeypatch, [], people[:1])
    assert result2["sent"] == 0 and http2.posts == []


def test_dm_run_stops_on_first_403_before_any_success(tmp_path, monkeypatch):
    people = [{"handle": "a", "name": "A"}, {"handle": "b", "name": "B"}]
    result, http, _ = _dm_run(tmp_path, monkeypatch, [(403, {"detail": "no dm scope"})], people)
    assert result["stopped"] and len(http.posts) == 1


def test_dm_run_skips_closed_inbox_after_a_success(tmp_path, monkeypatch):
    people = [{"handle": c, "name": c} for c in "abc"]
    sends = [(201, {}), (403, {"detail": "closed"}), (201, {})]
    result, http, _ = _dm_run(tmp_path, monkeypatch, sends, people)
    assert result == {"sent": 2, "closed": 1, "stopped": None}


def test_dm_run_stops_on_rate_limit(tmp_path, monkeypatch):
    people = [{"handle": c, "name": c} for c in "ab"]
    result, http, _ = _dm_run(tmp_path, monkeypatch, [(201, {}), (429, {"title": "Too Many"})],
                              people)
    assert result["sent"] == 1 and "429" in result["stopped"]


def test_dm_run_treats_permission_text_as_closed_inbox(tmp_path, monkeypatch):
    people = [{"handle": c, "name": c} for c in "ab"]
    closed = (403, {"title": "Forbidden",
                    "detail": "You do not have permission to DM one or more participants."})
    result, http, _ = _dm_run(tmp_path, monkeypatch, [closed, (201, {})], people)
    assert result == {"sent": 1, "closed": 1, "stopped": None}


def test_graph_bio_filter_keeps_finance_people_and_drops_promoters():
    from outreach import x_graph
    assert x_graph.relevant_bio("Private credit @ Fund. Lending to data centers and GPUs")
    assert x_graph.relevant_bio("Head of compute financing, ex-Goldman")
    assert not x_graph.relevant_bio("GPU enjoyer. Gamer.")
    assert not x_graph.relevant_bio("Credit analyst at a bank")
    assert x_graph.tier("Credit analyst at a bank") == "credit"
    assert x_graph.tier("Lending @galaxyhq") == "credit"
    assert x_graph.tier("MD @ AshCroft Compute | Compute Broker & Deal Intelligence") == "strong"
    assert x_graph.tier("20y Trader, Credit HF PM. IG/HY, Private Credit.") == "credit"
    assert x_graph.tier("engineer") is None
    assert not x_graph.relevant_bio("$NVDA stocks trader. GPU lending bull. NFA")


def test_graph_run_skips_known_and_blocks_partners():
    from outreach import x_graph

    class Fake:
        def user_list(self, handle, kind, scrolls):
            return [{"username": "old", "name": "Old", "description": "GPU lending credit"},
                    {"username": "new", "name": "New Person",
                     "description": "Structured credit for GPU and data center deals"},
                    {"username": "toy", "name": "Toy", "description": "I like GPUs"}]

    out = x_graph.run(Fake(), [{"handle": "seed"}], set(), set(), {"old"})
    assert [p["x_handle"] for p in out["candidates"]] == ["new"]
    assert out["candidates"][0]["matched"] == ["following of @seed"]


# --- Shared queue -----------------------------------------------------------

@pytest.fixture()
def shared_url(tmp_path):
    return f"sqlite:///{tmp_path / 'shared.db'}"


def test_only_one_sender_wins_a_claim(shared_url, lender):
    from outreach import shared
    with shared.connect(shared_url) as conn:
        assert shared.claim(conn, "a", lender) is None
        assert shared.claim(conn, "b", lender) == "a"
        assert shared.claim(conn, "a", lender) is None     # A retry by the owner wins.


def test_profile_url_blocks_a_different_firm_spelling(shared_url, lender):
    from outreach import shared
    url = "https://www.linkedin.com/in/Pat-Example/?trk=x"
    with shared.connect(shared_url) as conn:
        assert shared.claim(conn, "a", lender, url) is None
        other = {**lender, "firm": "Example Credit Partners LLC"}
        assert shared.claim(conn, "b", other) is None
        assert shared.claim(conn, "b", other, "https://linkedin.com/in/pat-example") == "a"


def test_release_frees_only_an_unsent_claim(shared_url, lender):
    from outreach import shared
    with shared.connect(shared_url) as conn:
        shared.claim(conn, "a", lender)
        shared.release(conn, "a", lender)
        assert shared.claim(conn, "b", lender) is None
        shared.mark_sent(conn, "b", lender)
        shared.release(conn, "b", lender)
        assert shared.claim(conn, "a", lender) == "b"


def test_push_claims_contacted_people_and_pull_returns_the_queue(shared_url, lender):
    from outreach import shared
    sent = {**lender, "dedupe_key": "pat example|example credit", "stage": "sent"}
    fresh = {**lender, "name": "Sam New", "dedupe_key": "sam new|example credit",
             "stage": "new"}
    log = [{"name": "Other Name", "firm": "X", "result": "sent",
            "url": "https://www.linkedin.com/in/other-name/"}]
    with shared.connect(shared_url) as conn:
        shared.push(conn, "a", [sent, fresh], log)
        assert {p["name"] for p in shared.pull(conn)} == {"Pat Example", "Sam New"}
        taken = shared.taken_by_others(conn, "b")
    assert "name:pat example|example credit" in taken
    assert "name:sam new|example credit" not in taken
    assert "url:/in/other-name" in taken


def test_targets_skip_people_that_another_sender_holds(tmp_path, monkeypatch, shared_url, lender):
    from outreach import config, shared
    path = str(tmp_path / "t.db")
    monkeypatch.setattr(db, "DB_PATH", path)
    monkeypatch.setattr(linkedin, "LOG_PATH", str(tmp_path / "log.csv"))
    db.init_db(path)
    with db.connect(path) as conn:
        for name in ("Pat Example", "Sam New"):
            pid = db.upsert_prospect(conn, {**lender, "name": name})
            db.set_fit(conn, pid, "confirmed")
            db.save_score(conn, pid, 50, {})
    base = dict(config.load_config())
    monkeypatch.setattr(linkedin, "load_config",
                        lambda: {**base, "shared": {"database_url": shared_url, "sender": "b"}})
    with shared.connect(shared_url) as conn:
        shared.mark_sent(conn, "a", lender)
    assert [r["name"] for r in linkedin.targets()] == ["Sam New"]


def test_a_missing_sender_name_stops(shared_url):
    from outreach import shared
    with pytest.raises(shared.SharedError):
        shared.settings({"shared": {"database_url": shared_url, "sender": ""}})
    assert shared.settings({"shared": {"database_url": ""}}) is None


def test_intro_can_change_for_another_sender(cfg, lender):
    cfg["sender"] = {**cfg["sender"], "intros": {"short_intro": "I'm a Stanford student"}}
    note = messages.generate(lender, cfg)[0]["body"]
    assert "i'm a stanford student building" in note


def test_sender_claims_releases_on_skip_and_respects_the_other(monkeypatch, shared_url, cfg, lender):
    from outreach import shared
    sh = {"url": shared_url, "sender": "a"}
    url = "https://www.linkedin.com/in/pat-example/"
    monkeypatch.setattr(linkedin, "_find_profile", lambda page, p: url)

    def no_button(page, p, u):
        raise linkedin.Skip("no Connect or More button")
    monkeypatch.setattr(linkedin, "_open_invite", no_button)
    with pytest.raises(linkedin.Skip):
        linkedin._send_one(None, lender, cfg, False, sh)
    with shared.connect(shared_url) as conn:            # The skip freed both keys.
        assert shared.taken_by_others(conn, "b") == set()
        shared.mark_sent(conn, "b", lender, url)
    with pytest.raises(linkedin.Taken):
        linkedin._send_one(None, lender, cfg, False, sh)


# --- LinkedIn alumni search -------------------------------------------------

CARD_TEXT = ("Jonty Nobbs\n \n2nd degree connection\n· 2nd\n"
             "Private Credit Associate at BlackRock\nRyan G and 1 other mutual connection\nConnect")


def test_parse_card_reads_name_and_headline():
    from outreach import linkedin_search as ls
    assert ls.parse_card(CARD_TEXT) == {"name": "Jonty Nobbs",
                                        "headline": "Private Credit Associate at BlackRock"}
    assert ls.parse_card("LinkedIn Member\nPrivate Credit") is None


def test_firm_comes_from_the_headline_not_the_search_term(cfg):
    from outreach import linkedin_search as ls
    firms = cfg["alumni_auto"]["gpu_firms"]
    assert ls.firm_from("Capital Markets at Crusoe | CMU '15", firms) == "Crusoe"
    assert ls.firm_from("GPU Financing @Magnetar", firms) == "Magnetar"
    assert ls.firm_from("Treasury, CoreWeave", firms) == "Coreweave"
    assert ls.firm_from("Credit analyst", firms) == ""


@pytest.mark.parametrize("headline,keep", [
    ("Private Credit Associate at BlackRock", False),        # Finance only.
    ("Structured Finance @ Deutsche Bank", False),
    ("Software Engineer at CoreWeave", False),              # GPU only.
    ("Software Engineer, AWS Lambda at Amazon", False),
    ("Head of Capital Markets at Crusoe", True),
    ("Director, Data Center Finance at Blackstone", True),
    ("VP, GPU Financing at Magnetar", True),
    ("Treasury at NVIDIA", True),
    ("MBA student | AI infrastructure investing", False)])
def test_headline_needs_a_gpu_link_and_a_finance_word(cfg, headline, keep):
    from outreach import linkedin_search as ls
    assert ls.fits(headline, cfg["alumni_auto"]) is keep


@pytest.mark.parametrize("headline,firm,segment", [
    ("Debt Financing at CoreWeave", "CoreWeave", "neocloud"),
    ("Analyst, Structured Finance, data centers", "Moody's Ratings", "rating_agency"),
    ("Director, GPU ABS", "Guggenheim", "abf_specialty"),
    ("GPU Financing", "Magnetar", "private_credit")])
def test_segment_rules(cfg, headline, firm, segment):
    from outreach import linkedin_search as ls
    assert ls.segment_for(headline, firm, cfg["alumni_auto"]) == segment


def test_the_search_term_never_makes_a_person_confirmed(cfg):
    from outreach import linkedin_search as ls
    card = {"name": "Kim Lee", "headline": "Treasury at NVIDIA"}
    person = ls.to_prospect(card, "https://www.linkedin.com/in/kim", "GPU financing", cfg)
    assert "GPU financing" not in person["why_relevant"]
    assert person["firm"] == "NVIDIA" and person["segment"] == "neocloud"


def test_a_search_term_runs_for_one_sender_only(shared_url):
    from outreach import shared
    with shared.connect(shared_url) as conn:
        assert shared.claim_search(conn, "a", "alumni:gpu", 14) is None
        assert shared.claim_search(conn, "b", "alumni:gpu", 14) == "a"
        assert shared.claim_search(conn, "a", "alumni:gpu", 14) == "a"   # No rerun either.
        assert shared.claim_search(conn, "b", "alumni:gpu", -1) is None   # After the gap, yes.
        shared.release_search(conn, "b", "alumni:gpu")
        assert shared.claim_search(conn, "a", "alumni:gpu", 14) is None


def _search_setup(tmp_path, monkeypatch, cfg, shared_url, sender):
    from outreach import linkedin_search as ls
    path = str(tmp_path / f"{sender}.db")
    monkeypatch.setattr(db, "DB_PATH", path)
    db.init_db(path)
    run_cfg = {**cfg, "shared": {"database_url": shared_url, "sender": sender},
               "alumni_auto": {**cfg["alumni_auto"], "delay_seconds": 0,
                               "terms": ["private credit", "gpu"]}}
    monkeypatch.setattr(ls.time, "sleep", lambda s: None)
    return run_cfg, path


def test_two_senders_split_the_terms_and_never_add_a_person_twice(
        tmp_path, monkeypatch, cfg, shared_url):
    from outreach import linkedin_search as ls, shared
    calls = []
    same = ("https://www.linkedin.com/in/jonty-nobbs",
            "Jonty Nobbs\n2nd degree connection\nGPU Credit Associate at BlackRock\nConnect")

    def fake(page, term, auto):
        calls.append(term)
        return [same] if term == "private credit" else [same, (
            "https://www.linkedin.com/in/sam-gpu",
            "Sam Gpu\n2nd degree connection\nGPU Financing Lead at Lambda\nConnect")]
    monkeypatch.setattr(ls, "_search_term", fake)

    cfg_a, path_a = _search_setup(tmp_path, monkeypatch, cfg, shared_url, "a")
    assert ls.run(None, 1, cfg_a) == {"terms": 1, "added": 1, "skipped_terms": 0}
    cfg_b, path_b = _search_setup(tmp_path, monkeypatch, cfg, shared_url, "b")
    assert ls.run(None, None, cfg_b) == {"terms": 1, "added": 1, "skipped_terms": 1}
    assert calls == ["private credit", "gpu"]            # Each term ran once.
    with shared.connect(shared_url) as conn:
        assert sorted(p["name"] for p in shared.pull(conn)) == ["Jonty Nobbs", "Sam Gpu"]
    with db.connect(path_b) as conn:
        row = dict(db.all_prospects(conn)[0])
    assert row["name"] == "Sam Gpu" and row["cmu_link"] == 1 and row["fit"] == "confirmed"


def test_a_warning_frees_the_search_term(tmp_path, monkeypatch, cfg, shared_url):
    from outreach import linkedin_search as ls, shared

    def warn(page, term, auto):
        raise linkedin.Stop("LinkedIn text: unusual activity")
    monkeypatch.setattr(ls, "_search_term", warn)
    cfg_a, _ = _search_setup(tmp_path, monkeypatch, cfg, shared_url, "a")
    with pytest.raises(linkedin.Stop):
        ls.run(None, 1, cfg_a)
    with shared.connect(shared_url) as conn:
        assert shared.claim_search(conn, "b", "alumni:private credit", 14) is None


def test_daily_search_limit(tmp_path, monkeypatch, cfg, shared_url):
    from outreach import linkedin_search as ls
    monkeypatch.setattr(ls, "_search_term", lambda page, term, auto: [])
    cfg_a, _ = _search_setup(tmp_path, monkeypatch, cfg, shared_url, "a")
    cfg_a["alumni_auto"]["daily_searches"] = 1
    assert ls.run(None, None, cfg_a)["terms"] == 1
    assert ls.run(None, None, cfg_a)["terms"] == 0


def test_sender_uses_the_url_only_for_people_found_on_linkedin(lender):
    url = "https://www.linkedin.com/in/pat-example"
    assert linkedin.profile_url({**lender, "linkedin_url": url, "source_url": url}) == url
    assert linkedin.profile_url({**lender, "linkedin_url": url}) is None
