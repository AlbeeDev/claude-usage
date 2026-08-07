#!/usr/bin/env python3
"""Tests for the parts that have logic: the digest and the debugger address.

Nothing here touches a browser or the network. Run with pytest, or directly:

    ./test_usage.py
"""

import io
import json
import urllib.request

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


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"ok  {name}")
