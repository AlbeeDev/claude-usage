#!/usr/bin/env python3
"""Tests for the parts that have logic: the digest and the debugger address.

Nothing here touches a browser or the network. Run with pytest, or directly:

    ./test_usage.py
"""

import io
import json
import tempfile
import time
import urllib.request
from pathlib import Path

import usage

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
    d = usage.digest(USAGE)
    assert d["status"] == "ok"
    assert d["session_pct"] == 12.0
    assert d["weekly_pct"] == 35.0
    assert d["credits_spent"] == 2.50
    assert d["credits_limit"] is None


def test_digest_keeps_only_active_critical_limits():
    # A model-scoped block is a real stop even while both percentages look fine.
    blocking = usage.digest(USAGE)["blocking"]
    assert len(blocking) == 1
    assert blocking[0]["scope"] == "Opus 5"


def test_digest_refuses_an_unrecognised_response():
    # Null percentages render as 0% — "plenty of headroom" — so they must not
    # be reported as ok.
    for bad in ({}, {"probe": True}, {"five_hour": {}}):
        try:
            usage.digest(bad)
        except usage.Unavailable as e:
            assert e.payload["status"] == "request_failed"
        else:
            raise AssertionError(f"digest({bad}) should have refused")


def test_ws_endpoint_ignores_the_browsers_own_loopback():
    # Chrome reports 127.0.0.1, which is its loopback inside the container and
    # not anywhere we can reach. The address we already used has to win.
    advertised = {"webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/browser/abc-123"}
    original = urllib.request.urlopen
    urllib.request.urlopen = lambda *a, **k: io.BytesIO(json.dumps(advertised).encode())
    try:
        authority = usage.CDP.split("://", 1)[-1].rstrip("/")
        assert usage.ws_endpoint() == f"ws://{authority}/devtools/browser/abc-123"
    finally:
        urllib.request.urlopen = original


def test_ws_endpoint_reports_an_unreachable_browser():
    original = urllib.request.urlopen

    def boom(*a, **k):
        raise OSError("connection refused")

    urllib.request.urlopen = boom
    try:
        usage.ws_endpoint()
    except usage.Unavailable as e:
        assert e.payload["status"] == "browser_unavailable"
    else:
        raise AssertionError("an unreachable browser should be reported")
    finally:
        urllib.request.urlopen = original


def test_cache_is_keyed_per_account():
    # Serving one account's numbers for another is the failure the whole
    # multi-account feature exists to avoid, and the cache is where it would
    # happen quietly.
    original = usage.CACHE
    with tempfile.TemporaryDirectory() as tmp:
        usage.CACHE = Path(tmp) / "cache.json"
        try:
            usage.remember("pro", {"status": "ok", "session_pct": 10.0})
            usage.remember("team", {"status": "ok", "session_pct": 90.0})
            assert usage.cached("pro")["session_pct"] == 10.0
            assert usage.cached("team")["session_pct"] == 90.0
            assert usage.cached(None) is None, "no account must not read an account's"
            usage.remember(None, {"status": "ok", "session_pct": 50.0})
            assert usage.cached(None)["session_pct"] == 50.0
            assert usage.cached("pro")["session_pct"] == 10.0, "must not be clobbered"
        finally:
            usage.CACHE = original


def test_session_cookies_are_recognised_by_name():
    # All four sessionKey variants must move together: with only sessionKey
    # swapped the previous account stays authenticated and the numbers are
    # attributed to the wrong one.
    for name in ("sessionKey", "sessionKeyLC", "sessionKeyV3", "sessionKeyV3LC",
                 "lastActiveOrg"):
        assert usage.is_session_cookie(name), name
    for name in ("cf_clearance", "__cf_bm", "_cfuvid", "anthropic-device-id"):
        assert not usage.is_session_cookie(name), name


def test_session_days_left():
    # Reported so a session can be renewed while that is still a login rather
    # than an outage, so it has to cope with what the browser actually hands
    # back: session cookies with no expiry at all, and a mix of the two.
    soon = time.time() + 3 * 86400
    later = time.time() + 20 * 86400
    assert usage.session_days_left([]) is None
    assert usage.session_days_left([{"name": "sessionKey", "expires": -1}]) is None
    assert usage.session_days_left([{"name": "lastActiveOrg", "expires": soon}]) is None
    assert usage.session_days_left([{"name": "sessionKey", "expires": later},
                                    {"name": "sessionKeyV3", "expires": soon}]) == 3.0
    assert usage.session_days_left([{"name": "sessionKey", "expires": soon},
                                    {"name": "lastActiveOrg", "expires": -1}]) == 3.0


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"ok  {name}")
