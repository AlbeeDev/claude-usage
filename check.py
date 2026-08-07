#!/usr/bin/env python3
"""Read Claude plan usage and print it as JSON.

Cloudflare challenges any client that is not a genuine browser session, so the
numbers cannot be fetched over plain HTTP. A Chromium container holds the login
— a human logs into it once, by hand — and this connects to that same browser
and asks the question from inside a page, where it is indistinguishable from
the site's own request. Nothing copies cookies and nothing launches a second
browser. See README.

Exit codes: 0 = usage read, 1 = usage unavailable (reason in the JSON).
Also exposed as an MCP tool by mcp_server.py, which calls read_usage().
"""

import fcntl
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).parent
CDP = os.environ.get("USAGE_CDP_URL", "http://localhost:9223")
CACHE = HERE / ".cache.json"
LOCK = HERE / ".lock"
CACHE_TTL = 60          # seconds; several callers may check at once
CHALLENGE_TIMEOUT = 60  # seconds to let Cloudflare clear


class Unavailable(Exception):
    def __init__(self, status, detail=None):
        super().__init__(status)
        self.payload = {"status": status}
        if detail:
            self.payload["detail"] = str(detail)[:200]


def ws_endpoint():
    """Where to talk to the browser.

    Chrome advertises its debugger socket as 127.0.0.1, which is its own
    loopback and means nothing to us, so keep the address we already reached it
    on and take only the path.
    """
    try:
        with urllib.request.urlopen(f"{CDP}/json/version", timeout=10) as r:
            url = json.load(r)["webSocketDebuggerUrl"]
    except Exception as e:
        raise Unavailable("browser_unavailable", e)

    authority = CDP.split("://", 1)[-1].rstrip("/")
    path = url.partition("://")[2].partition("/")[2]
    return f"ws://{authority}/{path}"


def fetch():
    """Ask the logged-in browser, from inside a page it opens itself."""
    from playwright.sync_api import sync_playwright

    endpoint = ws_endpoint()
    with sync_playwright() as p:
        try:
            browser = p.chromium.connect_over_cdp(endpoint, timeout=30000)
        except Exception as e:
            raise Unavailable("browser_unavailable", e)

        # close() here drops our connection; it does not close the human's
        # browser, which has to keep running to hold the login.
        try:
            ctx = browser.contexts[0] if browser.contexts else browser.new_context()
            page = ctx.new_page()
            try:
                page.goto("https://claude.ai/", wait_until="domcontentloaded", timeout=60000)

                deadline = time.time() + CHALLENGE_TIMEOUT
                while time.time() < deadline:
                    text = page.evaluate("() => document.body.innerText")[:200]
                    if "security verification" not in text and "Just a moment" not in text:
                        break
                    page.wait_for_timeout(3000)
                else:
                    raise Unavailable("blocked", "Cloudflare challenge did not clear")

                def api(path):
                    r = page.evaluate(
                        """async (p) => {
                            const res = await fetch(p, {headers: {'Accept': 'application/json'}});
                            return {status: res.status, body: await res.text()};
                        }""", path)
                    if r["status"] in (401, 403):
                        raise Unavailable("unauthenticated", f"HTTP {r['status']} from {path}")
                    if r["status"] != 200:
                        raise Unavailable("request_failed", f"HTTP {r['status']} from {path}")
                    return json.loads(r["body"])

                orgs = api("/api/organizations")
                chat = [o for o in orgs if "chat" in (o.get("capabilities") or [])]
                if not chat:
                    raise Unavailable("request_failed", "no chat-capable organization on this account")
                return api(f"/api/organizations/{chat[0]['uuid']}/usage")
            finally:
                page.close()
        finally:
            browser.close()


def digest(u):
    # Any limit that is both active and critical is what actually stops a run.
    blocking = [
        {
            "kind": lim.get("kind"),
            "percent": lim.get("percent"),
            "resets_at": lim.get("resets_at"),
            "scope": ((lim.get("scope") or {}).get("model") or {}).get("display_name"),
        }
        for lim in (u.get("limits") or [])
        if lim.get("is_active") and lim.get("severity") == "critical"
    ]
    five, seven = u.get("five_hour") or {}, u.get("seven_day") or {}
    spend = u.get("spend") or {}

    def dollars(m):
        if not m or m.get("amount_minor") is None:
            return None
        return m["amount_minor"] / (10 ** m.get("exponent", 2))

    # A response this no longer recognises would digest to all-nulls under status
    # "ok", and a null percentage renders as 0% — "plenty of headroom", the exact
    # opposite of the truth. This is what an endpoint change looks like.
    if five.get("utilization") is None:
        raise Unavailable("request_failed", "no five_hour utilization in usage response")

    return {
        "status": "ok",
        "session_pct": five.get("utilization"),
        "session_resets_at": five.get("resets_at"),
        "weekly_pct": seven.get("utilization"),
        "weekly_resets_at": seven.get("resets_at"),
        "blocking": blocking,
        "credits_enabled": (u.get("extra_usage") or {}).get("is_enabled"),
        "credits_spent": dollars(spend.get("used")),
        "credits_limit": dollars(spend.get("limit")),
    }


def cached():
    try:
        c = json.loads(CACHE.read_text())
        if time.time() - c["ts"] < CACHE_TTL:
            return c["data"]
    except Exception:
        pass
    return None


def read_usage():
    """Return the usage digest. Always a dict; check its "status" field."""
    hit = cached()
    if hit:
        return hit

    # One caller at a time — parallel runs would each open their own page.
    LOCK.touch(exist_ok=True)
    with open(LOCK, "r+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        hit = cached()  # another run may have refreshed while we waited
        if hit:
            return hit
        try:
            data = digest(fetch())
            data["source"] = "browser"
            CACHE.write_text(json.dumps({"ts": time.time(), "data": data}))
            return data
        except Unavailable as e:
            return e.payload
        except Exception as e:
            return {"status": "request_failed", "detail": str(e)[:200]}


if __name__ == "__main__":
    result = read_usage()
    print(json.dumps(result, indent=2))
    sys.exit(0 if result["status"] == "ok" else 1)
