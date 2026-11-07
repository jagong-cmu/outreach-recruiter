"""Load config.yaml, then overlay config.local.yaml (git-ignored).

The repo is public. Your name, company and pitch belong in config.local.yaml.
"""
from __future__ import annotations

import os
from functools import lru_cache

import yaml

ROOT = os.path.dirname(os.path.dirname(__file__))
CONFIG_PATH = os.environ.get("OUTREACH_CONFIG", os.path.join(ROOT, "config.yaml"))
LOCAL_PATH = os.environ.get("OUTREACH_LOCAL_CONFIG", os.path.join(ROOT, "config.local.yaml"))


def _merge(base: dict, over: dict) -> dict:
    merged = dict(base)
    for key, value in (over or {}).items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge(merged[key], value)
        else:
            merged[key] = value
    return merged


@lru_cache(maxsize=1)
def load_config() -> dict:
    """Parse the config files once and cache the result."""
    with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh)
    if os.path.exists(LOCAL_PATH):
        with open(LOCAL_PATH, "r", encoding="utf-8") as fh:
            cfg = _merge(cfg, yaml.safe_load(fh) or {})
    return cfg


def missing_identity(cfg: dict) -> list[str]:
    """Return the sign-off fields that are empty. Short messages work without them."""
    return [f for f in ("signoff",) if not str(cfg["sender"].get(f) or "").strip()]
