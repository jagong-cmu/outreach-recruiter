"""ATS scoring engine.

Produces a transparent 0–100 match score for each candidate, weighted toward
outreach/community work and marketing/social-media experience (configurable in
config.yaml). "Transparent" is the key design goal: every score ships with the
exact keywords that earned it, so you can trust and tune the results.
"""
from __future__ import annotations

import re
from typing import Optional

from .config import load_config


def _compile(keyword: str) -> re.Pattern:
    """Word-boundary, case-insensitive matcher for a keyword or phrase."""
    return re.compile(r"\b" + re.escape(keyword.lower()) + r"\b", re.IGNORECASE)


def score_text(text: str, config: Optional[dict] = None) -> dict:
    """Score a blob of candidate text. Returns {total, breakdown}.

    breakdown = {
        "categories": {
            "outreach": {"score": int, "cap": int, "matched": [{kw, points}]},
            ...
        },
        "bonuses": [{"name": str, "points": int, "matched": [kw]}],
    }
    """
    cfg = (config or load_config())["scoring"]
    text = text or ""

    breakdown: dict = {"categories": {}, "bonuses": []}
    total = 0

    # ── Category keyword matching ────────────────────────────────────────────
    for name, cat in cfg["categories"].items():
        cap = cat["weight"]
        matched = []
        raw = 0
        for kw, pts in cat["keywords"].items():
            if _compile(kw).search(text):
                matched.append({"keyword": kw, "points": pts})
                raw += pts
        cat_score = min(cap, raw)
        breakdown["categories"][name] = {
            "score": cat_score,
            "cap": cap,
            "raw": raw,
            "matched": sorted(matched, key=lambda m: -m["points"]),
        }
        total += cat_score

    # ── Additive bonuses ─────────────────────────────────────────────────────
    for name, bonus in cfg.get("bonuses", {}).items():
        hits = [kw for kw in bonus["keywords"] if _compile(kw).search(text)]
        if hits:
            breakdown["bonuses"].append(
                {"name": name, "points": bonus["points"], "matched": hits}
            )
            total += bonus["points"]

    total = max(0, min(100, total))
    breakdown["total"] = total
    return {"total": total, "breakdown": breakdown}


def candidate_text(candidate: dict) -> str:
    """Combine the searchable fields of a candidate into one blob."""
    parts = [
        candidate.get("name", ""),
        candidate.get("headline", ""),
        candidate.get("raw_text", ""),
    ]
    return "\n".join(p for p in parts if p)


def matches_target(candidate: dict, config: Optional[dict] = None) -> bool:
    """True if the candidate fits the target filter (grad year + school)."""
    cfg = (config or load_config())["target"]
    if candidate.get("grad_year") != cfg["grad_year"]:
        return False
    school = (candidate.get("school") or "") + " " + (candidate.get("headline") or "")
    school = school.lower()
    return any(k.lower() in school for k in cfg["school_keywords"])
