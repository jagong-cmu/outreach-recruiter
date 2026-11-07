import re

import pytest

from outreach import db, messages, qualify, scoring


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


def test_no_opener_means_no_draft(cfg, lender):
    assert messages.generate({**lender, "opener": ""}, cfg) == []


def test_no_unfilled_placeholders_and_note_fits(cfg, lender):
    drafts = messages.generate({**lender, "x_handle": "@pat"}, cfg)
    assert {d["channel"] for d in drafts} == {
        "linkedin_note", "linkedin_dm", "x_dm", "email", "followup"}
    for d in drafts:
        assert not re.search(r"[{}]", d["body"])
    note = next(d for d in drafts if d["channel"] == "linkedin_note")
    assert note["chars"] <= 200


def test_no_x_draft_without_handle(cfg, lender):
    assert "x_dm" not in {d["channel"] for d in messages.generate(lender, cfg)}


def test_messages_do_not_claim_traction(cfg, lender):
    text = " ".join(d["body"] for d in messages.generate(lender, cfg)).lower()
    for claim in ("yc-backed", "backed by y", "our customers", "our clients", "pilot"):
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


def test_run_scores_but_does_not_draft_without_identity(tmp_path, monkeypatch, lender):
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
        assert db.get_messages(conn, pid) == []
