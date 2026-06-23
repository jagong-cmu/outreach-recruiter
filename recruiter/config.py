"""Load and access config.yaml."""
from __future__ import annotations

import os
from functools import lru_cache

import yaml

CONFIG_PATH = os.environ.get(
    "RECRUITER_CONFIG",
    os.path.join(os.path.dirname(os.path.dirname(__file__)), "config.yaml"),
)


@lru_cache(maxsize=1)
def load_config(path: str | None = None) -> dict:
    """Parse config.yaml once and cache it."""
    with open(path or CONFIG_PATH, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)
