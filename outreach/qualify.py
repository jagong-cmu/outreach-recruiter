"""Decide if a prospect is safe and useful to contact.

Same idea as the old student check: two facts only count when they occur in the
SAME statement. "GPU" in a firm's news and "credit" in a person's title can come
from different things. So this module splits the person's own text into sentences
and requires an asset term AND a finance or operations term in one sentence.

  confirmed  an asset term and a role term occur in one sentence
  adjacent   the source exists, but no one sentence has both terms
  no_source  no source URL, so the record cannot be checked
  excluded   the segment is on the do-not-contact list in config.yaml
             (for example direct competitors and journalists)

Only confirmed and adjacent prospects are scored and receive messages.
"""
from __future__ import annotations

import re
from typing import Optional

from .config import load_config

PASSING = ("confirmed", "adjacent")

_SENTENCE = re.compile(r"(?<=[.!?;])\s+|\n+")


def _term(term: str) -> re.Pattern:
    return re.compile(r"\b" + re.escape(term.lower()) + r"s?\b", re.IGNORECASE)


def evidence(prospect: dict, cfg: dict) -> Optional[str]:
    """Return the first sentence that holds both an asset and a role term."""
    q = cfg["qualify"]
    text = "\n".join(prospect.get(k) or "" for k in ("title", "why_relevant", "hook"))
    for sentence in _SENTENCE.split(text):
        has_asset = any(_term(t).search(sentence) for t in q["asset_terms"])
        has_role = any(_term(t).search(sentence) for t in q["role_terms"])
        if has_asset and has_role:
            return sentence.strip()
    return None


def qualify(prospect: dict, config: Optional[dict] = None) -> dict:
    """Return {fit, evidence, reason} for one prospect."""
    cfg = config or load_config()
    if prospect.get("segment") in cfg["qualify"]["do_not_contact"]:
        return {"fit": "excluded", "evidence": None,
                "reason": f"segment '{prospect.get('segment')}' is on the do-not-contact list"}
    if not (prospect.get("source_url") or "").startswith("http"):
        return {"fit": "no_source", "evidence": None,
                "reason": "no source URL, so nobody can check this record"}
    found = evidence(prospect, cfg)
    if found:
        return {"fit": "confirmed", "evidence": found, "reason": None}
    return {"fit": "adjacent", "evidence": None,
            "reason": "the source exists, but no sentence links this person to GPU finance"}
