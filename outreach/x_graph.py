"""Find more people through the follow lists of people already found. Read-only.

People in GPU finance follow each other. A follow list shows each bio, so one page
gives many candidates. A bio passes when it names a finance or risk role and an
asset of this space (GPUs, compute, data centers, AI infrastructure). These people
go to their own review file, because the post search filter is stricter.
"""
from __future__ import annotations

import json
import os
import re
from typing import Optional

from .config import ROOT
from . import x_api, x_browser

OUT_PATH = os.path.join(ROOT, "data", "x_candidates_graph.json")

# A credit, risk or valuation job. Generic words like "finance" alone do not count.
ROLE = re.compile(
    r"\b(credit|lend(?:er|ers|ing)|loans?|debt|structured|securiti[sz]\w*|abs|clos?|"
    r"underwrit\w*|private credit|project finance|infra(?:structure)? (?:finance|investing|"
    r"investor|investments?|credit|debt|funds?|equity)|leasing|lessor|equipment financ\w*|"
    r"capital markets|dcm|leveraged finance|levfin|high yield|hy|fixed income|"
    r"insur\w*|reinsur\w*|actuar\w*|underwriter|rating agency|moody'?s|fitch|kbra|"
    r"apprais\w*|valuations?|residual value|distressed|restructuring|special sit\w*|"
    r"asset[- ]based|abl|specialty finance|cfo|treasurer)\b", re.IGNORECASE)
ASSET = re.compile(
    r"\b(gpus?|compute|neoclouds?|data ?cent(?:er|re)s?|datacent(?:er|re)s?|"
    r"ai infra\w*|digital infra\w*|semis|semiconductors?|accelerators?|hpc|"
    r"coreweave|nvidia|hyperscale\w*)\b", re.IGNORECASE)
# Terms that name this market by themselves.
MARKET = re.compile(
    r"gpu[- ]?(?:financ|lend|loan|backed|debt|derivativ|futures|broker|leas)|"
    r"compute (?:financ|market|broker|derivativ|futures|lend|credit|exchange)|"
    r"(?:financ|lend)\w* (?:gpus|compute|ai infra\w*|data cent\w*)", re.IGNORECASE)
# Retail promoters and token accounts. A professional credit trader is not noise.
NOISE = re.compile(
    r"\b(nfa|not (?:financial|investment) advice|dyor|memecoins?|airdrops?|degen|"
    r"signals|shill\w*|giveaway|ambassador|kol|join my|free group|100x|10x)\b",
    re.IGNORECASE)


def tier(bio: str) -> Optional[str]:
    """"strong": a finance role tied to this market. "credit": a credit or risk
    professional found through a GPU finance person's list. None: drop."""
    bio = bio or ""
    if NOISE.search(bio):
        return None
    if MARKET.search(bio) or (ROLE.search(bio) and ASSET.search(bio)):
        return "strong"
    if ROLE.search(bio):
        return "credit"
    return None


def relevant_bio(bio: str) -> bool:
    return tier(bio) == "strong"


def segment_for(bio: str) -> str:
    text = (bio or "").lower()
    for seg, words in (("insurer", ("insur", "reinsur", "actuar")),
                       ("rating_agency", ("rating", "moody", "fitch", "kbra")),
                       ("appraiser", ("apprais", "valuation", "residual")),
                       ("lawyer", ("attorney", "lawyer", "counsel", "law firm")),
                       ("private_credit", ("credit", "lend", "debt", "loan", "abs",
                                           "securiti", "underwrit", "leas")),
                       ("neocloud", ("neocloud", "gpu cloud", "data center", "datacenter"))):
        if any(w in text for w in words):
            return seg
    return "abf_specialty"


def load(path: str) -> dict:
    if not os.path.exists(path):
        return {"candidates": [], "blocked": [], "error": None}
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def run(client, seeds: list[dict], handles: set[str], names: set[str],
        skip: set[str]) -> dict:
    """Read each seed's list. Return {candidates, blocked, error} for new people."""
    found: dict[str, dict] = {}
    blocked: dict[str, dict] = {}
    error: Optional[str] = None
    for seed in seeds:
        try:
            users = client.user_list(seed["handle"], seed.get("kind", "following"),
                                     seed.get("scrolls", 12))
        except x_api.XError as err:
            error = str(err)
            break
        for user in users:
            key = user["username"].lower()
            level = tier(user.get("description", ""))
            if key in skip or not level:
                continue
            reason = x_api.block_reason(user, handles, names)
            if reason:
                blocked.setdefault(key, {"x_handle": user["username"], "name": user.get("name"),
                                         "reason": reason, "bio": user.get("description") or ""})
                continue
            person = found.setdefault(key, x_api.to_candidate(
                user, segment_for(user.get("description", ""))))
            person["confidence"] = "medium" if level == "strong" else person.get(
                "confidence", "low")
            person["tier"] = "strong" if level == "strong" or person.get(
                "tier") == "strong" else "credit"
            tag = f"{seed.get('kind', 'following')} of @{seed['handle']}"
            if tag not in person["matched"]:
                person["matched"].append(tag)
    return {"candidates": list(found.values()), "blocked": list(blocked.values()),
            "error": error}


def run_browser(cfg: dict, seeds: list[dict], handles: set[str], names: set[str],
                skip: set[str]) -> tuple[dict, object]:
    """Open one new tab in the logged-in Chrome, read the lists, close only that tab."""
    from playwright.sync_api import sync_playwright

    from .config import load_config

    with sync_playwright() as p:
        ctx = p.chromium.connect_over_cdp(load_config()["linkedin"]["cdp_url"]).contexts[0]
        if not x_browser.logged_in(ctx.cookies("https://x.com")):
            raise x_api.XError("Chrome is not logged in to X. Log in by hand in that Chrome window.")
        page = ctx.new_page()
        client = x_browser.BrowserClient(page, cfg)
        try:
            page.goto("https://x.com/home", wait_until="domcontentloaded")
            page.wait_for_timeout(4_000)
            client.check()
            result = run(client, seeds, handles, names, skip)
        except x_api.XError as err:
            result = {"candidates": [], "blocked": [], "error": str(err)}
        finally:
            page.close()
    return result, client
