#!/usr/bin/env python3
"""Read Claude plan usage and print it as JSON.

Cloudflare challenges any client that is not a genuine browser session, so the
numbers are not scraped. A userscript in the always-on Firefox pushes readings
to the local collector from a real logged-in tab (see userscript.js), and this
reads the latest one. If no fresh reading exists it falls back to driving a
Chromium itself, which works today but is the fragile path — it is automation,
and automation is what gets challenged. See README.

Exit codes: 0 = usage read, 1 = usage unavailable (reason in the JSON).
Also exposed as an MCP tool by mcp_server.py, which calls read_usage().
"""

import fcntl
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

HERE = Path(__file__).parent
CONTAINER = os.environ.get("USAGE_FIREFOX_CONTAINER", "usage-check-firefox")
PROFILE_DB = "/config/profile/cookies.sqlite"
CHROME_PROFILE = HERE / "chromium-profile"
CACHE = HERE / ".cache.json"
PUSHED = HERE / "data" / "latest.json"
LOCK = HERE / ".lock"
CACHE_TTL = 60          # seconds; several night runs may check at once
PUSHED_TTL = 600        # seconds a browser-pushed reading stays usable
CHALLENGE_TIMEOUT = 60  # seconds to let Cloudflare clear


class Unavailable(Exception):
    def __init__(self, status, detail=None):
        super().__init__(status)
        self.payload = {"status": status}
        if detail:
            self.payload["detail"] = str(detail)[:200]


@contextmanager
def display():
    """Headless Chromium gets flagged, so give it a virtual display to run in.

    Started in-process rather than by re-exec'ing under xvfb-run, because this
    also runs inside the MCP server, which must not be replaced.
    """
    if os.environ.get("DISPLAY"):
        yield
        return

    num = next(n for n in range(99, 130) if not os.path.exists(f"/tmp/.X{n}-lock"))
    proc = subprocess.Popen(
        ["Xvfb", f":{num}", "-screen", "0", "1280x800x24"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    os.environ["DISPLAY"] = f":{num}"
    time.sleep(1)
    try:
        yield
    finally:
        os.environ.pop("DISPLAY", None)
        proc.terminate()
        proc.wait(timeout=10)


def session_key():
    with tempfile.TemporaryDirectory() as tmpdir:
        dest = Path(tmpdir) / "cookies.sqlite"
        try:
            subprocess.run(
                ["docker", "cp", f"{CONTAINER}:{PROFILE_DB}", str(dest)],
                check=True, capture_output=True, timeout=60,
            )
        except FileNotFoundError:
            raise Unavailable("browser_unavailable", "docker not found")
        except subprocess.CalledProcessError as e:
            raise Unavailable("browser_unavailable", e.stderr.decode("utf-8", "replace").strip())
        except subprocess.TimeoutExpired:
            raise Unavailable("browser_unavailable", "docker cp timed out")

        db = sqlite3.connect(dest)
        row = db.execute(
            "SELECT value FROM moz_cookies WHERE name='sessionKey' AND host LIKE '%claude.ai%'"
        ).fetchone()
        db.close()

    if not row:
        raise Unavailable("unauthenticated", "no sessionKey in browser profile")
    return row[0]


def fetch(key):
    """Load claude.ai in a real browser, then call the API from the page."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            user_data_dir=str(CHROME_PROFILE),
            headless=False,
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
        )
        try:
            ctx.add_cookies([{
                "name": "sessionKey", "value": key, "domain": ".claude.ai",
                "path": "/", "secure": True, "httpOnly": True,
            }])
            page = ctx.new_page()
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
            ctx.close()


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


def pushed():
    """Latest reading volunteered by the browser userscript, if it's fresh.

    Preferred over driving a browser ourselves: it comes from a real logged-in
    tab, so there is no automation for Cloudflare to challenge. A reading that
    is stale, failed, or no longer digestible falls through to the browser.
    """
    try:
        c = json.loads(PUSHED.read_text())
        age = time.time() - c["received_at"]
        if age > PUSHED_TTL or "error" in c["usage"]:
            return None
        out = digest(c["usage"])
        out["source"] = "browser"
        out["age_seconds"] = round(age)
        return out
    except Exception:
        return None


def read_usage():
    """Return the usage digest. Always a dict; check its "status" field."""
    hit = pushed()
    if hit:
        return hit

    hit = cached()
    if hit:
        return hit

    # One browser at a time — parallel night runs would fight over the profile.
    LOCK.touch(exist_ok=True)
    with open(LOCK, "r+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        hit = cached()  # another run may have refreshed while we waited
        if hit:
            return hit
        try:
            with display():
                data = digest(fetch(session_key()))
            data["source"] = "fallback-chromium"
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
