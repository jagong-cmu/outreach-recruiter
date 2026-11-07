"""Generate per-channel drafts. This module never sends anything.

A draft needs a specific opener. Without one, generate() returns nothing, because
a generic cold message gets no reply and burns a contact you only get once.
"""
from __future__ import annotations

from typing import Optional
from urllib.parse import quote_plus

from .config import load_config

CHANNEL_LABELS = {
    "linkedin_note": "LinkedIn connection note",
    "linkedin_dm": "LinkedIn message (after accept) or InMail",
    "x_dm": "X DM",
    "email": "Email",
    "followup": "Follow-up (same channel)",
}


def first_name(full_name: str) -> str:
    parts = (full_name or "").strip().split()
    return parts[0] if parts else "there"


def group_for(segment: str, cfg: dict) -> str:
    for group, segments in cfg["templates"]["groups"].items():
        if segment in segments:
            return group
    return "default"


def _render(text: str, fields: dict) -> str:
    return " ".join(text.split("  ")).format(**fields).strip()


def generate(prospect: dict, config: Optional[dict] = None) -> list[dict]:
    """Return [{channel, subject, body, chars, limit, over}] for one prospect."""
    cfg = config or load_config()
    opener = (prospect.get("opener") or "").strip()
    if not opener:
        return []
    sender = cfg["sender"]
    group = group_for(prospect.get("segment"), cfg)
    ask = cfg["templates"]["asks"].get(group, cfg["templates"]["asks"]["default"])
    fields = {
        "first_name": first_name(prospect.get("name", "")),
        "opener": opener,
        "ask": ask,
        "firm": prospect.get("firm") or "your firm",
        "sender_name": sender.get("sender_name") or "",
        "company_name": sender.get("company_name") or "",
        "one_liner": (sender.get("one_liner") or "").strip(),
        "reason_now": (sender.get("reason_now") or "").strip(),
        "call_ask": (sender.get("call_ask") or "").strip(),
        "signoff": (sender.get("signoff") or sender.get("sender_name") or "").strip(),
    }
    limits = cfg["limits"]
    out = []
    for channel, tpl in cfg["templates"]["channels"].items():
        if channel == "x_dm" and not prospect.get("x_handle"):
            continue
        subject = _render(tpl["subject"], fields) if tpl.get("subject") else ""
        body = _render(tpl["body"], fields)
        limit = limits.get(channel)
        out.append({
            "channel": channel, "subject": subject, "body": body,
            "chars": len(body), "limit": limit,
            "over": bool(limit and len(body) > limit),
        })
    return out


def best_channel(prospect: dict, config: Optional[dict] = None) -> str:
    """Choose the first channel to try. Warm and public routes come first."""
    if prospect.get("email"):
        return "email"
    if prospect.get("x_handle") and prospect.get("segment") in (
            "analyst", "founder", "market_data"):
        return "x_dm"
    return "linkedin_note"


def search_links(prospect: dict) -> dict:
    """Links that the founder opens by hand. These never automate a session."""
    query = f"{prospect.get('name', '')} {prospect.get('firm', '')}".strip()
    links = {
        "linkedin": prospect.get("linkedin_url")
        or f"https://www.linkedin.com/search/results/people/?keywords={quote_plus(query)}",
        "linkedin_found": bool(prospect.get("linkedin_url")),
    }
    handle = (prospect.get("x_handle") or "").lstrip("@")
    links["x"] = (f"https://x.com/{handle}" if handle
                  else f"https://x.com/search?q={quote_plus(query)}&f=user")
    return links
