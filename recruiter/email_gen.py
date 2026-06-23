"""Generate personalized email drafts. Drafts are saved for review, not sent."""
from __future__ import annotations

import json
from typing import Optional

from .config import load_config


def _first_name(full_name: str) -> str:
    return (full_name or "there").strip().split()[0] if full_name else "there"


def _highlights_phrase(breakdown: Optional[dict]) -> str:
    """Turn the top matched keywords into a natural clause for the email.

    e.g. " — especially your experience with community outreach and social media"
    Returns "" if we have nothing specific, so the sentence still reads well.
    """
    if not breakdown:
        return ""
    matched = []
    for cat in breakdown.get("categories", {}).values():
        matched.extend(m["keyword"] for m in cat.get("matched", []))
    # de-dupe preserving order, take the 3 strongest signals
    seen, top = set(), []
    for kw in matched:
        if kw not in seen:
            seen.add(kw)
            top.append(kw)
        if len(top) == 3:
            break
    if not top:
        return ""
    if len(top) == 1:
        joined = top[0]
    else:
        joined = ", ".join(top[:-1]) + " and " + top[-1]
    return f" — especially your experience with {joined}"


def generate(candidate: dict, config: Optional[dict] = None) -> dict:
    """Return {subject, body} for one candidate."""
    cfg = config or load_config()
    club = cfg["club"]
    tpl = cfg["email"]

    breakdown = candidate.get("breakdown")
    if isinstance(breakdown, str):
        try:
            breakdown = json.loads(breakdown)
        except (ValueError, TypeError):
            breakdown = None

    fields = {
        "first_name": _first_name(candidate.get("name", "")),
        "org": club["org"],
        "pitch": club["pitch"].strip(),
        "highlights": _highlights_phrase(breakdown),
        "sender_name": club["sender_name"],
    }
    return {
        "subject": tpl["subject"].format(**fields),
        "body": tpl["body"].format(**fields),
    }
