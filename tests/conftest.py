import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


@pytest.fixture()
def cfg():
    from outreach import config
    config.load_config.cache_clear()
    base = dict(config.load_config())
    base["sender"] = {**base["sender"], "sender_name": "Test Founder", "signoff": "Test",
                      "calendar_url": "https://cal.com/a/15min"}
    base["limits"] = {**base["limits"], "linkedin_note": 300}
    base["templates"] = {**base["templates"], "enabled_channels": None,
                         "lowercase": True}
    return base


@pytest.fixture()
def lender():
    return {"name": "Pat Example", "title": "Managing Director, Asset-Based Finance",
            "firm": "Example Credit", "segment": "private_credit",
            "why_relevant": "Leads GPU-backed lending at the firm.",
            "hook": "Spoke on GPU collateral at a 2026 conference.",
            "opener": "I read your remarks on GPU collateral.",
            "source_url": "https://example.com/a", "confidence": "high"}
