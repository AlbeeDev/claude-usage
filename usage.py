#!/usr/bin/env python3
"""Read Claude plan usage and print it as JSON.

Cloudflare challenges any client that is not a genuine browser session, so the
numbers cannot be fetched over plain HTTP. Instead a browser you are logged
into — one you started with --remote-debugging-port, or the container in
docker-compose.yml — is asked the question from inside a page, where it is
indistinguishable from the site's own request. Nothing copies cookies and
nothing launches a browser of its own. See README.

Exit codes: 0 = usage read, 1 = usage unavailable (reason in the JSON).
Also exposed as an MCP tool by mcp_server.py, which calls read_usage().
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from contextlib import contextmanager
from pathlib import Path

try:
    import fcntl
except ImportError:  # Windows
    fcntl = None

HERE = Path(__file__).parent
DEFAULT_CDP = "http://localhost:9222"
CDP = os.environ.get("USAGE_CDP_URL", DEFAULT_CDP)
CACHE = HERE / ".cache.json"
LOCK = HERE / ".lock"
PROFILE = HERE / "browser-profile"
CACHE_TTL = 60          # seconds; several callers may check at once
CHALLENGE_TIMEOUT = 60  # seconds to let Cloudflare clear


class Unavailable(Exception):
    def __init__(self, status, detail=None):
        super().__init__(status)
        self.payload = {"status": status}
        if detail:
            self.payload["detail"] = str(detail)[:200]


def browser_binary():
    """Wherever this machine keeps Chrome. Edge counts — it is Chromium too."""
    if sys.platform == "win32":
        roots = [os.environ.get(v, "") for v in
                 ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA")]
        candidates = [
            Path(r) / sub for r in roots if r for sub in (
                r"Google\Chrome\Application\chrome.exe",
                r"Microsoft\Edge\Application\msedge.exe",
                r"Chromium\Application\chrome.exe",
            )
        ]
    elif sys.platform == "darwin":
        candidates = [Path(p) for p in (
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/Applications/Chromium.app/Contents/MacOS/Chromium",
            "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
        )]
    else:
        found = (shutil.which(n) for n in
                 ("google-chrome", "google-chrome-stable", "chromium",
                  "chromium-browser", "microsoft-edge"))
        candidates = [Path(p) for p in found if p]

    return next((p for p in candidates if p.exists()), None)


def start_browser():
    """Open the browser the human logs into, with debugging turned on.

    Its profile lives beside this file, so the login survives restarts and is
    kept away from the browser they use for everything else.
    """
    # Whatever is already on the port would answer the check below and make a
    # launch that achieved nothing look like a success.
    if browser_answers_http() is not None:
        raise Unavailable("browser_unavailable",
                          f"{describe_browser()}; restart that browser rather than "
                          f"starting another")

    exe = browser_binary()
    if exe is None:
        raise Unavailable("browser_unavailable", "no Chrome, Chromium or Edge found")

    port = CDP.rsplit(":", 1)[-1].rstrip("/")
    args = [
        str(exe),
        f"--remote-debugging-port={port}",
        f"--user-data-dir={PROFILE}",
        "--no-first-run",
        "--no-default-browser-check",
    ]
    # Chrome refuses to start as root unless sandboxing is off. Servers often
    # run as root; a desktop never hits this.
    if getattr(os, "geteuid", None) and os.geteuid() == 0:
        args.append("--no-sandbox")
    args.append("https://claude.ai/")

    # mkstemp, not a predictable name: on a shared machine anyone could
    # pre-create a known path as a symlink and have this truncate whatever it
    # points at, which as root is any file on the box.
    errfd, errlog = tempfile.mkstemp(prefix="claude-usage-browser-", suffix=".log")
    kwargs = {"stdout": subprocess.DEVNULL}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        # Chrome has no flag for this, but Windows lets the parent say how the
        # first window should appear. Only once there is a profile: the run that
        # creates one is the run where somebody has to see the window to log in.
        # No equivalent elsewhere — window placement belongs to the window
        # manager on Linux and macOS.
        if PROFILE.exists():
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = 7  # SW_SHOWMINNOACTIVE: minimised, no focus
            kwargs["startupinfo"] = startupinfo
    else:
        kwargs["start_new_session"] = True  # outlive this process

    try:
        with os.fdopen(errfd, "wb") as err:
            proc = subprocess.Popen(args, stderr=err, **kwargs)

        # Popen succeeding says only that the file was executable. Wait for the
        # browser to actually offer a debugger before claiming it started.
        deadline = time.time() + 30
        while time.time() < deadline:
            try:
                urllib.request.urlopen(f"{CDP}/json/version", timeout=2).close()
                return exe
            except Exception:
                if proc.poll() is not None:
                    break
                time.sleep(1)

        # Chrome is noisy, so its last line is a hint rather than the cause.
        said = [ln for ln in Path(errlog).read_text(errors="replace").splitlines()
                if ln.strip()]
        detail = f"{exe} never offered a debugger on {CDP}"
        if said:
            detail += f"; last said: {said[-1]}"
        raise Unavailable("browser_unavailable", detail)
    finally:
        try:
            os.unlink(errlog)
        except OSError:
            pass


def browser_answers_http(timeout=5):
    """Whether anything is serving the debugger's HTTP side.

    Worth asking separately, because Chrome answers these while its debug
    protocol is wedged — the two live on different threads. "Nothing there" and
    "there but not listening to us" need different advice.
    """
    try:
        with urllib.request.urlopen(f"{CDP}/json/version", timeout=timeout) as r:
            return json.load(r)
    except Exception:
        return None


def describe_browser():
    """A line about what is on the port, for when talking to it fails."""
    info = browser_answers_http()
    if info is None:
        return f"nothing is listening on {CDP}"
    try:
        with urllib.request.urlopen(f"{CDP}/json/list", timeout=5) as r:
            targets = len(json.load(r))
    except Exception:
        targets = "?"
    return (f"{info.get('Browser', 'a browser')} on {CDP} answers HTTP "
            f"({targets} targets) but not the debug protocol")


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
        raise Unavailable(
            "browser_unavailable", f"nothing at {CDP} ({e}); run ./usage --login")

    authority = CDP.split("://", 1)[-1].rstrip("/")
    path = url.partition("://")[2].partition("/")[2]
    return f"ws://{authority}/{path}"


def open_claude(page):
    """Put a page on claude.ai and wait for Cloudflare to be done with it."""
    page.goto("https://claude.ai/", wait_until="domcontentloaded", timeout=60000)

    deadline = time.time() + CHALLENGE_TIMEOUT
    while time.time() < deadline:
        text = page.evaluate("() => document.body.innerText")[:200]
        if "security verification" not in text and "Just a moment" not in text:
            return
        page.wait_for_timeout(3000)
    raise Unavailable("blocked", "Cloudflare challenge did not clear")


def fetch():
    """Ask the logged-in browser, from a claude.ai tab it leaves open."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise Unavailable("request_failed",
                          "playwright is not installed — see Setup in the README")

    endpoint = ws_endpoint()
    with sync_playwright() as p:
        try:
            browser = p.chromium.connect_over_cdp(endpoint, timeout=30000)
        except Exception as e:
            # The socket opening says nothing: Chrome accepts the connection on
            # one thread and answers it on another. Say which of those failed,
            # since restarting the browser fixes one and not the other.
            raise Unavailable("browser_unavailable",
                              f"{describe_browser()} — {str(e).splitlines()[0]}")

        # close() here drops our connection; it does not close the human's
        # browser, which has to keep running to hold the login.
        try:
            ctx = browser.contexts[0] if browser.contexts else browser.new_context()

            # Reuse a claude.ai tab if one is open. Opening a tab and navigating
            # it pulls the window to the front, which is intolerable for
            # something that may run every few minutes; asking a tab that is
            # already sitting there disturbs nothing. The tab is left open
            # afterwards, which is what keeps later runs quiet.
            page = next((pg for pg in ctx.pages
                         if pg.url.startswith("https://claude.ai")), None)
            fresh = page is None
            if fresh:
                page = ctx.new_page()
                open_claude(page)

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

            def read():
                orgs = api("/api/organizations")
                chat = [o for o in orgs if "chat" in (o.get("capabilities") or [])]
                if not chat:
                    raise Unavailable("request_failed",
                                      "no chat-capable organization on this account")
                return api(f"/api/organizations/{chat[0]['uuid']}/usage")

            try:
                return read()
            except Unavailable:
                if fresh:
                    raise
                # The tab we borrowed was stale — sitting on a challenge or an
                # error page. Reload it and try once more before giving up.
                open_claude(page)
                return read()
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


@contextmanager
def one_at_a_time():
    """Keep parallel callers from each opening their own page.

    Windows has no fcntl, and there the 60-second cache is the only guard. That
    is enough for what this protects against — a few extra tabs, briefly.
    """
    if fcntl is None:
        yield
        return

    LOCK.touch(exist_ok=True)
    with open(LOCK, "r+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def read_usage():
    """Return the usage digest. Always a dict; check its "status" field."""
    hit = cached()
    if hit:
        return hit

    with one_at_a_time():
        hit = cached()  # another run may have refreshed while we waited
        if hit:
            return hit
        try:
            data = digest(fetch())
            data["source"] = "browser"
            CACHE.write_text(json.dumps({"ts": time.time(), "data": data}))
            os.chmod(CACHE, 0o600)  # your usage and spend are nobody else's
            return data
        except Unavailable as e:
            return e.payload
        except Exception as e:
            return {"status": "request_failed", "detail": str(e)[:200]}


def open_login_page():
    """Bring up claude.ai in the browser that is already running.

    For the case auto-detection cannot help with: the browser is there, the
    session simply expired. Starting another one would be the wrong answer.
    """
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(ws_endpoint(), timeout=30000)
        try:
            ctx = browser.contexts[0] if browser.contexts else browser.new_context()
            # Reuse the claude.ai tab rather than leaving one behind per run.
            page = next((pg for pg in ctx.pages
                         if pg.url.startswith("https://claude.ai")), None) or ctx.new_page()
            page.goto("https://claude.ai/login", wait_until="domcontentloaded")
        finally:
            browser.close()


def mcp_command():
    """The interpreter and script Claude Code should run, as absolute paths.

    The venv beside this file wins, for the same reason the wrappers prefer it:
    an MCP client runs a command, it does not activate anything.
    """
    venv = HERE / ".venv" / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    python = venv if venv.exists() else Path(sys.executable)
    # Absolute, but NOT resolved: .venv/bin/python is a symlink to the real
    # interpreter, and following it discards the environment, which is the whole
    # point of pointing at it. A venv is found relative to the binary's path.
    return os.path.abspath(python), os.path.abspath(HERE / "mcp_server.py")


COMMAND_NAME = "claude-usage"


def path_dirs():
    """Directories on PATH we could install into, best first."""
    on_path = [Path(p) for p in os.environ.get("PATH", "").split(os.pathsep) if p]

    if sys.platform == "win32":
        preferred = [Path.home() / "AppData/Local/Microsoft/WindowsApps"]
    elif os.geteuid() == 0:
        preferred = [Path("/usr/local/bin")]
    else:
        preferred = [Path.home() / ".local/bin", Path.home() / "bin"]

    ranked = [d for d in preferred if d in on_path]
    ranked += [d for d in on_path if d not in ranked and os.access(d, os.W_OK)]
    return ranked, preferred


def add_to_path():
    """Install this as `claude-usage`, runnable from anywhere.

    A link, not a copy: the wrapper resolves symlinks back to here, so the venv
    and the browser profile are still found, and updating the repo updates the
    command.
    """
    source = HERE / ("usage.bat" if sys.platform == "win32" else "usage")
    if not source.exists():
        print(f"{source} is missing — run this from a checkout, not an install.")
        return 1

    ranked, preferred = path_dirs()
    if not ranked:
        want = preferred[0]
        print(f"No writable directory on your PATH. Create {want} and add it to\n"
              f"PATH, then run this again.")
        return 1

    target = ranked[0] / (COMMAND_NAME + (".bat" if sys.platform == "win32" else ""))

    if target.exists() or target.is_symlink():
        if target.is_symlink() and os.path.realpath(target) == str(source.resolve()):
            print(f"Already installed at {target}")
            return 0
        print(f"{target} already exists and is not ours. Remove it first, or\n"
              f"install under another name by linking it yourself.")
        return 1

    try:
        if sys.platform == "win32":
            # Windows symlinks need admin rights, so leave a shim instead.
            target.write_text(f'@echo off\r\n"{source}" %*\r\n')
        else:
            target.symlink_to(source)
    except OSError as e:
        print(f"Could not write {target}: {e}")
        return 1

    print(f"Installed {target}\n\nRun `{COMMAND_NAME}` from anywhere. "
          f"To remove it, delete that file.")
    return 0


def register_mcp():
    """Register the MCP server with Claude Code, or say how to do it by hand."""
    python, server = mcp_command()

    claude = shutil.which("claude")
    if claude is None:
        print("Claude Code's `claude` command isn't on PATH, so add this to your\n"
              "MCP client's config yourself:\n")
        print(json.dumps({"mcpServers": {"claude-usage": {
            "type": "stdio", "command": python, "args": [server]}}}, indent=2))
        return 1

    # Replacing an existing entry is the whole point of running this again, and
    # `claude mcp add` refuses to overwrite, so clear it first. Said out loud
    # rather than done quietly, since it is someone else's config being edited.
    listed = subprocess.run([claude, "mcp", "list"], capture_output=True, text=True)
    if "claude-usage" in listed.stdout:
        print("Replacing the existing claude-usage registration.")
        gone = subprocess.run([claude, "mcp", "remove", "claude-usage"],
                              capture_output=True, text=True)
        if gone.returncode != 0:
            print((gone.stderr or gone.stdout).strip())
            return 1

    done = subprocess.run(
        [claude, "mcp", "add", "claude-usage", "--scope", "user", "--", python, server],
        capture_output=True, text=True)
    if done.returncode != 0:
        print((done.stderr or done.stdout).strip())
        return 1

    print(f"Registered claude-usage for all your projects.\n"
          f"  python: {python}\n  server: {server}\n\n"
          "Start a new Claude session to pick it up.")
    return 0


def cli(argv):
    """The command-line front door.

    read_usage() never opens a browser — callers embedding it should not get a
    window appearing out of nowhere. Run from a terminal, though, there is a
    person right there, so a missing browser is worth starting rather than
    merely reporting. Guidance goes to stderr so stdout stays parseable JSON.
    """
    if "--add-to-path" in argv:
        return add_to_path()

    if "--register-mcp" in argv:
        return register_mcp()

    if "--login" in argv:
        try:
            open_login_page()
        except Unavailable:
            pass  # no browser to open a page in; the usual path starts one
        else:
            print("Opened claude.ai in the running browser. Sign in there.", file=sys.stderr)
            return 0

    result = read_usage()

    # A browser that is there but not answering needs restarting, not company.
    # Worth saying wherever it is, so this advice is not tied to the default
    # address the way starting one has to be.
    if result["status"] == "browser_unavailable" and browser_answers_http() is not None:
        print(f"{describe_browser()}.\n\n"
              "Restart it — `docker compose restart browser` if it is the\n"
              "container, or close and reopen the window if you started it\n"
              "yourself — then run this again.", file=sys.stderr)

    elif result["status"] == "browser_unavailable" and CDP == DEFAULT_CDP:
        print("No browser is running. Starting one...", file=sys.stderr)
        try:
            exe = start_browser()
        except Unavailable as e:
            print(json.dumps(e.payload, indent=2))
            print(f"\nCould not start a browser: {e.payload.get('detail', '')}\n"
                  "On a machine with no screen, use Docker instead: docker compose up -d",
                  file=sys.stderr)
            return 1
        print(f"Started {exe}", file=sys.stderr)
        # The profile usually still holds a login — closing the window loses the
        # connection, not the session — so read rather than assume a sign-in.
        result = read_usage()

    if result["status"] == "unauthenticated":
        print("Not logged in. Sign into claude.ai in the browser that is already\n"
              "running, or run this with --login to open the page there.",
              file=sys.stderr)

    print(json.dumps(result, indent=2))
    return 0 if result["status"] == "ok" else 1


if __name__ == "__main__":
    sys.exit(cli(sys.argv[1:]))
