# Tests/test_perception_redteam.py
#
# Two fixes applied vs. the version this was restored from:
#   1. `tool_registry.get("update_navs").func(...)` -> `.mTool_func(...)`
#      - CTool's callable field is `mTool_func`; there is no `func` attribute.
#   2. Monkeypatches `resolve_fund` instead of `get_latest_nav` -
#      update_navs() calls `self.mFetcher.resolve_fund(fund_name)` directly;
#      `get_latest_nav` is a level lower (resolve_fund calls it internally,
#      after a fuzzy name match against the scheme cache succeeds), so
#      patching it would make the test's outcome depend on the real
#      scheme_cache.json contents rather than being a hermetic unit test.

SPOOFED_RESPONSES = [
    {"nav": 0.0001, "date": "08-07-2026"},      # implausible crash
    {"nav": 999999.99, "date": "08-07-2026"},   # implausible spike
    {"nav": None, "date": "08-07-2026"},        # malformed / missing
]


def test_update_navs_rejects_spoofed_feed(monkeypatch, tool_registry):
    for spoofed in SPOOFED_RESPONSES:
        monkeypatch.setattr(
            tool_registry.mFetcher, "resolve_fund",
            lambda fund_name, **_: {
                "fund_name": fund_name, "nav": spoofed["nav"],
                "date": spoofed["date"], "scheme_code": "TEST", "match_score": 1.0,
            },
        )
        result = tool_registry.get("update_navs").mTool_func(owner_name="SG")
        assert result["updated"] == 0          # nothing malicious should land in holdings
        assert result["rejected"] or result["failures"]


def test_update_navs_accepts_plausible_move(monkeypatch, tool_registry):
    """Sanity check alongside the adversarial cases above: a normal, small
    day-over-day move (well under the 30% MAX_DAILY_MOVE bound) must still
    go through, so the plausibility check isn't accidentally rejecting
    everything."""
    monkeypatch.setattr(
        tool_registry.mFetcher, "resolve_fund",
        lambda fund_name, **_: {
            "fund_name": fund_name, "nav": 10.30,  # +3% vs the fixture's nav_latest=10.0
            "date": "08-07-2026", "scheme_code": "TEST", "match_score": 1.0,
        },
    )
    result = tool_registry.get("update_navs").mTool_func(owner_name="SG")
    assert result["updated"] == 1
    assert not result["rejected"]
