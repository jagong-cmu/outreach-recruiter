"""Generate per-channel drafts. This module never sends anything.

Every message uses one frame: a CMU student who is building in GPU financing and
wants to learn. The "learn how ____" blank fills from the person's role.
"""
from __future__ import annotations

import datetime as dt
import re
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


def topic_for(prospect: dict, cfg: dict) -> str:
    """Fill "learn how ____": personal override, then role rule, then segment."""
    if (prospect.get("topic") or "").strip():
        return prospect["topic"].strip()
    tpl = cfg["templates"]
    segment = prospect.get("segment")
    title = prospect.get("title") or ""
    for rule in tpl["role_topics"]:
        if segment in rule["segments"] and any(
                re.search(r"\b" + re.escape(t) + r"\b", title, re.IGNORECASE)
                for t in rule["title_has"]):
            return rule["topic"]
    return tpl["segment_topics"].get(segment, tpl["segment_topics"]["default"])


def fact_for(prospect: dict, cfg: dict) -> Optional[dict]:
    """Pick the fact for this segment, unless it is about this person's firm."""
    firm = (prospect.get("firm") or "").lower()
    for fact in cfg["templates"]["facts"].values():
        if prospect.get("segment") in fact["segments"] and not any(
                ex in firm for ex in fact["exclude_firms"]):
            return fact
    return None


def timing(cfg: dict, today: Optional[dt.date] = None) -> tuple[str, str]:
    """Return long and short timing lines. They change after the interview."""
    today = today or dt.date.today()
    date = dt.date.fromisoformat(str(cfg["sender"]["interview_date"]))
    if today > date:
        return ("I just had a YC interview, so anything this week would be really appreciated.",
                "I just had a YC interview, so anything this week helps.")
    day = date.strftime("%A")
    return (f"I have a YC interview on {day} so anything this weekend would be really appreciated.",
            f"I have a YC interview {day}, so anything this weekend helps.")


DEFAULT_INTROS = {
    "short_intro": ("I'm a Carnegie Mellon student", "I'm a fellow Tartan"),
    "dm_intro": ("I'm a Carnegie Mellon student", "I'm also a fellow Tartan"),
    "long_intro": ("I'm a Carnegie Mellon student",
                   "I'm a current Carnegie Mellon student, so a fellow Tartan,"),
}


def intros(sender: dict, alum: bool) -> dict:
    """Return the intro phrases. `sender.intros` in config.local.yaml can change them.

    Each phrase has a plain form and an alumni form, for example `short_intro`
    and `short_intro_alum`.
    """
    own = sender.get("intros") or {}
    out = {}
    for key, (plain, alum_text) in DEFAULT_INTROS.items():
        plain = own.get(key) or plain
        out[key] = (own.get(key + "_alum") or alum_text) if alum else plain
    return out


def _render(text: str, fields: dict, lowercase: bool = True) -> str:
    body = text.format(**fields)
    body = re.sub(r"[ \t]+\n", "\n", body)      # An empty field leaves a bare line.
    body = re.sub(r"\n{3,}", "\n\n", body)
    body = re.sub(r"[ \t]{2,}", " ", body).strip()
    return body.lower() if lowercase else body


def generate(prospect: dict, config: Optional[dict] = None,
             today: Optional[dt.date] = None) -> list[dict]:
    """Return [{channel, subject, body, chars, limit, over}] for one prospect."""
    cfg = config or load_config()
    sender = cfg["sender"]
    tpl_cfg = cfg["templates"]
    lowercase = tpl_cfg.get("lowercase", True)
    fact = fact_for(prospect, cfg)
    calendar = (sender.get("calendar_url") or "").strip()
    alum = bool(prospect.get("cmu_link"))
    timing_line, timing_short = timing(cfg, today)
    fields = {
        "first_name": first_name(prospect.get("name", "")),
        "opener": (prospect.get("opener") or "").strip(),
        "topic": topic_for(prospect, cfg),
        "fact_line": f"Fun fact I came across: {fact['text']}" if fact else "",
        "timing_line": timing_line,
        "timing_short": timing_short,
        "calendar_line": f"Feel free to book something with me here {calendar}" if calendar else "",
        "calendar_short": calendar,
        **intros(sender, alum),
        "signoff": (sender.get("signoff") or sender.get("sender_name") or "").strip(),
    }
    limits = cfg["limits"]
    out = []
    enabled = tpl_cfg.get("enabled_channels")
    for channel, tpl in tpl_cfg["channels"].items():
        if enabled and channel not in enabled:
            continue
        if channel == "x_dm" and not prospect.get("x_handle"):
            continue
        body = _render(tpl["body"], fields, lowercase)
        limit = limits.get(channel)
        subject = tpl.get("subject", "")
        out.append({
            "channel": channel, "subject": subject.lower() if lowercase else subject,
            "body": body, "chars": len(body), "limit": limit,
            "over": bool(limit and len(body) > limit),
        })
    return out


def best_channel(prospect: dict, config: Optional[dict] = None) -> str:
    """Choose the first channel to try. Warm and public routes come first."""
    enabled = (config or load_config())["templates"].get("enabled_channels")
    if enabled and len(enabled) == 1:
        return enabled[0]
    if prospect.get("email"):
        return "email"
    if prospect.get("x_handle") and prospect.get("segment") in (
            "analyst", "founder", "market_data"):
        return "x_dm"
    return "linkedin_note"


def alumni_search_url(keywords: str) -> str:
    """CMU's own alumni tool on LinkedIn. The founder opens this by hand."""
    return ("https://www.linkedin.com/school/carnegie-mellon-university/people/"
            f"?keywords={quote_plus(keywords)}")


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
