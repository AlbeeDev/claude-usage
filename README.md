# claude-usage-mcp

Read your Claude plan usage — the numbers behind claude.ai's "Current session"
and "Weekly" meters — from the CLI, or as an MCP tool.

```console
$ ./usage
{
  "status": "ok",
  "session_pct": 17.0,
  "session_resets_at": "2026-08-07T12:40:00Z",
  "weekly_pct": 36.0,
  "weekly_resets_at": "2026-08-08T06:59:59Z",
  "blocking": [],
  "credits_enabled": false,
  "credits_spent": 0.0,
  "credits_limit": null,
  "source": "browser"
}
```

Useful for long unattended runs that should stop before hitting a limit rather
than being killed mid-task, and for putting a usage meter in your own tools.

## How it works

Anthropic publishes no API for plan usage. The only source is the endpoint
claude.ai's settings page calls, which needs your session — and Cloudflare
rejects any client that isn't a real browser.

Cookies alone are not enough, which is worth knowing before you try the obvious
shortcut: an HTTP client sending perfectly valid cookies still gets `403` with
`cf-mitigated: challenge`, a freshly minted `__cf_bm` does not change that, and
neither does replaying a complete browser header set. What is being
fingerprinted is the client itself.

So you keep a browser logged in — one you start yourself, or the container in
`docker-compose.yml` — and `usage.py` connects to that same browser and asks the
question from inside a page, which is what the site's own settings modal does.
Nothing copies cookies out, and no browser is launched behind your back.

**This reads your own account through your own logged-in session.** No API key,
no token, nothing sanctioned — and therefore best-effort by nature. Don't build
anything critical on top of it.

## What this costs you

A browser holding a login is not a zero-maintenance thing:

- **A browser that keeps running**, logged into your account, on a machine that
  stays on.
- **One manual login**, by hand, in that browser.
- **Logging in again roughly monthly**, when the session expires. This never
  goes away.

Windows, macOS and Linux all work. Docker is optional — see below.

## Setup

**Linux, macOS**
```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

**Windows**
```
py -3 -m venv .venv
.venv\Scripts\pip install -r requirements.txt
```

You never have to activate it: `./usage` uses a `.venv` sitting next to it if
there is one. Installing into your system Python instead works too, but most
current Linux distributions refuse it — `pip` will say
`error: externally-managed-environment` — so the virtual environment is the path
that works everywhere.

No browser download: `playwright` is used only to talk to a browser that is
already running, so `playwright install` is not needed.

Run it with `./usage` on Linux and macOS, or `usage` on Windows — small wrappers
that work out what Python is called here, so you don't have to.

Then pick whichever fits your machine.

### Option A: on a machine with a screen

No Docker involved. Just run it:

```bash
./usage           # Windows: usage
```

The first time, there is no browser yet, so it finds Chrome, Chromium or Edge
and starts one for you:

```console
$ ./usage
No browser is running. Starting one...
Started /usr/bin/google-chrome

Log into claude.ai in the window that opened, leave it running,
then run this again.
```

Log in there, then run it again and you get numbers. That is the whole setup.

If no browser can be found or it fails to start, it says so and exits 1 — it
won't claim success and leave you guessing.

**Leave that window running** — it is what holds the session. Minimise it, or
park it on another desktop; it only has to exist. You can close it, and the
login survives in `browser-profile/`, but then `./usage` has to start it again
before it can read anything, which it does by itself.

It leaves one claude.ai tab open and reuses it every run, so nothing opens,
navigates or comes to the front while you are working. Only the very first run
opens that tab.

**On Windows the browser is started minimised**, once a profile exists — the
first run stays visible, because that is the run you have to log in on. Chrome
has no flag for this, so it is done by telling Windows how to show the process's
first window; there is no equivalent on Linux or macOS, where that belongs to
the window manager.

Headless would be nicer and does not work: Cloudflare challenges Chrome's new
headless mode (`cf-mitigated: challenge`) while passing the same browser visible
(`cf-mitigated: (none)`). The window is the part being checked.

> It uses its own profile directory (`browser-profile/`), not your everyday
> one, and that is deliberate: a browser with debugging enabled can be driven
> by anything else on your machine, so it should not be the browser holding the
> rest of your logins.

### Option B: Docker

Best on a headless server, where the point is having a screen to log in
through.

```bash
docker compose up -d
```

Open <http://localhost:3000> — that's the Chromium holding the login. Go to
claude.ai and log in. Then `./usage`.

Either way the checker looks at `localhost:9222` and needs no configuring.

> Reaching that UI from another machine needs HTTPS, because the page uses
> browser features that plain HTTP won't allow off localhost. Port `3001` serves
> the same screen over HTTPS with a self-signed certificate, or put it behind
> something that terminates TLS properly.

> Neither port is password-protected, and the browser is logged into your
> account. On a shared or untrusted network, bind them to `127.0.0.1` in
> `docker-compose.yml`.

## Using it

**CLI** — prints JSON, exits 0 on success, 1 on failure with the reason in
`status`.

**MCP** — register the server and any Claude session gets a `claude_usage` tool
reporting `session_pct` and `resets_at`, so it can decide whether to keep going.

Point `command` at the Python **inside your `.venv`**, not a bare `python3` —
that is where the dependencies are, and the MCP client does not activate
anything.

**Linux, macOS**
```json
{
  "mcpServers": {
    "claude-usage": {
      "type": "stdio",
      "command": "/path/to/claude-usage/.venv/bin/python",
      "args": ["/path/to/claude-usage/mcp_server.py"]
    }
  }
}
```

**Windows**
```json
{
  "mcpServers": {
    "claude-usage": {
      "type": "stdio",
      "command": "C:\\path\\to\\claude-usage\\.venv\\Scripts\\python.exe",
      "args": ["C:\\path\\to\\claude-usage\\mcp_server.py"]
    }
  }
}
```

The tool reads the browser but never starts one — a window appearing because
something polled your usage would be wrong. If the browser isn't running it
returns `browser_unavailable`, and you start it with `./usage`.

## Failure statuses

All exit 1, with the reason in `status`:

| Status | Means | Fix |
|---|---|---|
| `unauthenticated` | The login expired | Log in again in that browser |
| `browser_unavailable` | The browser isn't reachable | `./usage --login`, or `docker compose up -d` |
| `blocked` | Cloudflare didn't clear | Usually temporary; try again |
| `request_failed` | The endpoint changed, or something else | Check what `detail` says |

A response the digest doesn't recognise is a `request_failed`, never an `ok`
full of nulls — a null percentage renders as 0%, which reads as plenty of
headroom and is the opposite of the truth. Treat every reading as best-effort
and show "usage unavailable" rather than zero.

## Notes

The checker caches for 60 seconds, and on Linux and macOS it also serializes
concurrent callers with a file lock. Windows has no such lock, so there the
cache is the only guard — enough for what it protects against, a couple of extra
tabs for a moment.

The checker defaults to `http://localhost:9222`. If your browser is elsewhere,
point it there:

```bash
USAGE_CDP_URL=http://other-host:9222 ./usage
```

## Tests

`./test_usage.py` (or `pytest`) covers the digest and the debugger address. No
browser or network needed.

## License

MIT — see [LICENSE](LICENSE).
