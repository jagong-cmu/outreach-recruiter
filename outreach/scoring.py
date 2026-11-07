"""Transparent 0-100 score for who to contact first.

The score answers one question: how much can this person teach us before Monday?
Every score ships with the exact signals that earned it, so you can tune it.
"""
from __future__ import annotations

import re
from typing import Optional

from .config import load_config


def _compile(keyword: str) -> re.Pattern:
    """Word-boundary matcher. It also accepts a plural ("GPU" finds "GPUs")."""
    return re.compile(r"\b" + re.escape(keyword.lower()) + r"s?\b", re.IGNORECASE)


def prospect_text(p: dict) -> str:
    return "\n".join(p.get(k) or "" for k in ("title", "why_relevant", "hook"))


def score_prospect(p: dict, config: Optional[dict] = None) -> dict:
    """Return {total, breakdown}."""
    cfg = (config or load_config())["scoring"]
    text = prospect_text(p)
    breakdown: dict = {"categories": {}, "bonuses": []}

    weight = cfg["segment_weight"]
    points = min(weight, cfg["segments"].get(p.get("segment"), 0))
    breakdown["categories"]["segment"] = {
        "score": points, "cap": weight,
        "matched": [{"keyword": p.get("segment") or "unknown", "points": points}],
    }
    total = points

    for name, cat in cfg["categories"].items():
        matched = [{"keyword": kw, "points": pts}
                   for kw, pts in cat["keywords"].items() if _compile(kw).search(text)]
        cat_score = min(cat["weight"], sum(m["points"] for m in matched))
        breakdown["categories"][name] = {
            "score": cat_score, "cap": cat["weight"],
            "matched": sorted(matched, key=lambda m: -m["points"]),
        }
        total += cat_score

    bonus = cfg["bonuses"]
    if p.get("cmu_link"):
        breakdown["bonuses"].append({"name": "CMU link", "points": bonus["cmu_link"]})
        total += bonus["cmu_link"]
    if p.get("x_handle"):
        breakdown["bonuses"].append({"name": "on X", "points": bonus["on_x"]})
        total += bonus["on_x"]
    if p.get("email"):
        breakdown["bonuses"].append({"name": "public email", "points": bonus["public_email"]})
        total += bonus["public_email"]
    if p.get("confidence") == "high":
        breakdown["bonuses"].append({"name": "role confirmed", "points": bonus["role_confirmed"]})
        total += bonus["role_confirmed"]

    total = max(0, min(100, total))
    breakdown["total"] = total
    return {"total": total, "breakdown": breakdown}
