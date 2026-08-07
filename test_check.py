#!/usr/bin/env python3
"""Tests for the parts that have logic: the digest and the pushed-reading gate.

Nothing here touches a browser or the network. Run with pytest, or directly:

    ./test_check.py
"""

import json
import tempfile
import time
from pathlib import Path

import check

USAGE = {
    "five_hour": {"utilization": 12.0, "resets_at": "2026-08-07T12:39:59Z"},
    "seven_day": {"utilization": 35.0, "resets_at": "2026-08-08T06:59:59Z"},
    "limits": [
        {"kind": "five_hour", "percent": 12.0, "is_active": True, "severity": "info"},
        {
            "kind": "model_weekly", "percent": 98.0, "is_active": True,
            "severity": "critical", "resets_at": "2026-08-08T06:59:59Z",
            "scope": {"model": {"display_name": "Opus 5"}},
        },
    ],
    "extra_usage": {"is_enabled": False},
    "spend": {"used": {"amount_minor": 250, "exponent": 2}, "limit": None},
}


def test_digest_reads_both_windows():
    d = check.digest(USAGE)
    assert d["status"] == "ok"
    assert d["session_pct"] == 12.0
    assert d["weekly_pct"] == 35.0
    assert d["credits_spent"] == 2.50
    assert d["credits_limit"] is None


def test_digest_keeps_only_active_critical_limits():
    # A model-scoped block is a real stop even while both percentages look fine.
    blocking = check.digest(USAGE)["blocking"]
    assert len(blocking) == 1
    assert blocking[0]["scope"] == "Opus 5"


def test_digest_refuses_an_unrecognised_response():
    # Null percentages render as 0% — "plenty of headroom" — so they must not
    # be reported as ok.
    for bad in ({}, {"probe": True}, {"five_hour": {}}):
        try:
            check.digest(bad)
        except check.Unavailable as e:
            assert e.payload["status"] == "request_failed"
        else:
            raise AssertionError(f"digest({bad}) should have refused")


def test_pushed_falls_through_on_a_bad_reading():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "latest.json"
        original = check.PUSHED
        check.PUSHED = path
        try:
            path.write_text(json.dumps({"received_at": time.time(), "usage": {"probe": True}}))
            assert check.pushed() is None, "a bad pushed reading must not be used"

            path.write_text(json.dumps({"received_at": time.time(), "usage": USAGE}))
            good = check.pushed()
            assert good["session_pct"] == 12.0
            assert good["source"] == "browser"

            path.write_text(json.dumps({"received_at": 0, "usage": USAGE}))
            assert check.pushed() is None, "a stale pushed reading must not be used"
        finally:
            check.PUSHED = original


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"ok  {name}")
