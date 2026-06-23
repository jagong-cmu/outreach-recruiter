"""Verify that a candidate is genuinely an incoming CMU Class of 2030 student.

WHY THIS EXISTS
───────────────
The naive approach — "does 'Carnegie Mellon' appear on the page?" and "does
'2030' appear on the page?" — produces false positives. A LinkedIn profile page
includes "People also viewed", suggested profiles, and the person's feed, so the
school and the year can come from two *different* people and still both "match".

This module fixes that by requiring the two facts to co-occur in the SAME
education entry. Evidence is only trusted from places that describe the person
themselves:

  1. a structured Education entry (strongest), or
  2. the person's own headline (medium).

Anything found only in loose page text is ignored. The result is a status:

  verified   — an education entry lists CMU AND an expected grad year of 2030
  headline   — the person's headline states CMU + 2030 (e.g. "CMU '30")
  provided   — non-LinkedIn source (CSV/form) supplied the fields; nothing to
               independently check against
  mismatch   — found CMU but a different year, or class-of-2030 at another school
               (these are exactly the false positives we want to catch)
  unverified — no usable self-evidence of CMU class of 2030
"""
from __future__ import annotations

import re
from typing import Optional

from .config import load_config

PASSING = ("verified", "headline", "provided")

_YEAR = re.compile(r"\b(20\d{2})\b")
_APOS_YEAR = re.compile(r"['’](\d{2})\b")          # 'CMU '30' -> 2030
_CLASS_OF = re.compile(r"class of\s*'?(\d{2,4})", re.IGNORECASE)


def _entries(text: Optional[str]) -> list[str]:
    return [ln.strip() for ln in (text or "").splitlines() if ln.strip()]


def _has_school(text: str, keywords: list[str]) -> bool:
    """Match a school keyword. 'cmu' must be a whole word (so it can't fire on
    substrings); longer phrases match as substrings."""
    low = text.lower()
    for kw in keywords:
        kw = kw.lower().strip()
        if kw == "cmu":
            if re.search(r"\bcmu\b", low):
                return True
        elif kw and kw in low:
            return True
    return False


def _expected_grad_year(text: str) -> Optional[int]:
    """Best guess at the graduation year stated in one entry/headline.

    Priority: explicit "Class of YYYY" > apostrophe form ('30) > the latest
    4-digit year in a date range ("2026 - 2030" -> 2030)."""
    m = _CLASS_OF.search(text)
    if m:
        v = m.group(1)
        return int(v) if len(v) == 4 else 2000 + int(v)
    m = _APOS_YEAR.search(text)
    if m:
        return 2000 + int(m.group(1))
    years = [int(y) for y in _YEAR.findall(text)]
    return max(years) if years else None


def verify_candidate(candidate: dict, config: Optional[dict] = None) -> dict:
    """Return {status, source, grad_year, school, evidence, reason}."""
    cfg = (config or load_config())["target"]
    target_year = cfg["grad_year"]
    keywords = cfg["school_keywords"]

    edu_entries = _entries(candidate.get("education"))

    # 1) Structured education — strongest signal. Both facts must co-occur.
    for entry in edu_entries:
        if _has_school(entry, keywords):
            yr = _expected_grad_year(entry)
            if yr == target_year:
                return _result("verified", "education",
                               target_year, "Carnegie Mellon University", entry)
            if yr:
                return _result("mismatch", "education", yr,
                               "Carnegie Mellon University", entry,
                               reason=f"CMU found, but graduation year is {yr}, "
                                      f"not {target_year}")

    # An education entry at the right year but the WRONG school (e.g. Stanford '30)
    for entry in edu_entries:
        if _expected_grad_year(entry) == target_year and not _has_school(entry, keywords):
            return _result("mismatch", "education", target_year, None, entry,
                           reason=f"Class of {target_year} found, but at a "
                                  f"different school than CMU")

    # 2) Headline — the person's own self-description.
    head = candidate.get("headline") or ""
    if _has_school(head, keywords):
        yr = _expected_grad_year(head)
        if yr == target_year:
            return _result("headline", "headline",
                           target_year, "Carnegie Mellon University", head)
        if yr:
            return _result("mismatch", "headline", yr,
                           "Carnegie Mellon University", head,
                           reason=f"headline says class of {yr}, not {target_year}")

    # 3) Non-LinkedIn imports we can't independently verify — trust the fields.
    if candidate.get("source") in ("csv", "form", "sample") and candidate.get("grad_year"):
        ok = (candidate.get("grad_year") == target_year)
        status = "provided" if ok else "mismatch"
        return _result(status, candidate.get("source"),
                       candidate.get("grad_year"), candidate.get("school"),
                       "supplied by import",
                       reason=None if ok else f"imported grad_year is "
                              f"{candidate.get('grad_year')}, not {target_year}")

    # 4) Nothing trustworthy found.
    return _result("unverified", "none", None, None, None,
                   reason="no CMU class-of-2030 evidence found on the profile")


def _result(status, source, grad_year, school, evidence, reason=None) -> dict:
    return {
        "status": status, "source": source, "grad_year": grad_year,
        "school": school, "evidence": evidence, "reason": reason,
    }


def passes(candidate: dict, config: Optional[dict] = None) -> bool:
    return verify_candidate(candidate, config)["status"] in PASSING
