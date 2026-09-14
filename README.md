# claude-usage

Read your Claude plan usage from the command line, or as an MCP tool.

These are the numbers behind claude.ai's "Current session" and "Weekly" meters.
Anthropic publishes no API for them, so this reads them through a browser you
are logged into. See [How it works](#how-it-works).

```console
$ ./usage
{
  "status": "ok",
  "session_pct": 14.0,
  "session_resets_at": "2026-08-07T18:00:00Z",
  "weekly_pct": 38.0,
  "weekly_resets_at": "2026-08-08T07:00:00Z",
  "blocking": [],
  "credits_enabled": false,
  "credits_spent": 0.0,
  "credits_limit": null,
  "source": "browser"
}
```

## Features

- CLI printing JSON, exit 0 on success and 1 on failure
- MCP tool, so Claude can check its own remaining usage
- Works on Windows, macOS and Linux
- Docker optional — needed only on machines without a screen
- No API key, no browser download; several accounts from one browser

## Requirements

- Python 3.9+
- A browser (Chrome, Chromium or Edge), or Docker on a headless machine
- A machine that stays on, since the browser has to keep running
- One manual login, repeated roughly monthly when the session expires

## Install

```bash
git clone https://github.com/AlbeeDev/claude-usage.git
cd claude-usage
```

Then follow **A** if the machine has a screen, or **B** if it does not.

### A. Machine with a screen

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
./usage
```

Windows:

```
py -3 -m venv .venv
.venv\Scripts\pip install -r requirements.txt
usage
```

The first run finds a browser and starts one:

```console
$ ./usage
No browser is running. Starting one...
Started /usr/bin/google-chrome

Log into claude.ai in the window that opened, leave it running,
then run this again.
```

Log in, leave the window open, run `./usage` again.

### B. Headless machine (Docker)

The container provides the screen you log in through.

```bash
docker compose up -d
```

Then forward the port from your own machine and open
<http://localhost:3000>:

```bash
ssh -L 3000:localhost:3000 you@your-server
```

Log into claude.ai there, then **close that tab** — leave the blank one, which
keeps the browser alive. The session is saved to disk, so the tab is only needed
for typing your password, and a page left open accumulates memory for as long as
the browser runs. Back on the server:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
./usage
```

> The Chromium image is about 4.6 GB. Check you have the disk.

> **Anything that can reach port 9222 controls a browser signed into your Claude
> account** — it can read your conversations, act as you on any site that browser
> is logged into, and take its cookies. There is no authentication on that port;
> that is how the debug protocol works. All ports bind to `127.0.0.1` for this
> reason. Do not republish them on `0.0.0.0`, and treat any machine where you do
> not trust every local user as unsuitable.

### Put it on PATH

To call it from anywhere, or from another program:

```bash
./usage --add-to-path
```

```console
$ cd /anywhere && claude-usage
{"status": "ok", "session_pct": 15.0, ...}
```

It links into a directory already on your PATH — `/usr/local/bin` as root,
`~/.local/bin` otherwise — and prints where it went. On Windows it writes a
small `.bat` shim instead, since symlinks there need admin rights.

It is a link, not a copy, so updating the repo updates the command, and the venv
and browser profile are still found. To remove it, delete the file it names. It
will not overwrite a file it did not create.

## Usage

```bash
./usage                  # print usage as JSON
./usage --login          # open claude.ai in the running browser, to sign in again
./usage --register-mcp   # register the MCP server with Claude Code
./usage --add-to-path    # install as claude-usage, runnable from anywhere

./usage --capture-account <id>   # remember the account now signed in, under this id
./usage --accounts               # list captured accounts and when they expire
./usage --account <id>           # read that account instead of whoever is signed in
./usage --plans                  # every configured account and its plan, in one pass
```

On Windows, `usage` instead of `./usage`.

### MCP

```bash
./usage --register-mcp
```

Registers the server for all projects and prints what it wrote. Start a new
Claude session to pick it up. Claude then gets a `claude_usage` tool returning
`status`, `session_pct` and `resets_at`.

To register by hand, point `command` at the Python inside `.venv` — an MCP
client runs a command, it does not activate anything:

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

On Windows the interpreter is `.venv\Scripts\python.exe`.

The tool reads the browser but never starts one. If none is running it returns
`browser_unavailable`; start it with `./usage`.

## More than one account

If you have several Claude accounts, one browser can still serve them all. Log
into each one in turn and capture it:

```bash
./usage --capture-account pro     # while signed in as that account
# sign in as the other account in the browser UI, then:
./usage --capture-account team
```

Then read either, without logging in again:

```bash
./usage --account team
BURROW_USAGE_ACCOUNT=team ./usage   # same thing, for callers that set env vars
```

With no `--account`, nothing changes: you get whoever the browser is signed in
as, exactly as before.

How it works: each account's claude.ai session cookies are stored in
`accounts.json` (mode `0600`) and installed before the reading. Cloudflare's
cookies are left alone, so switching does not trigger a fresh challenge. Only
one browser is needed no matter how many accounts you have.

An account can own more than one chat-capable organisation — a Team one and a
free one, say. The organisation is pinned when the account is captured, so
readings keep using the same one rather than whichever the API lists first.

Every reading checks that the organisation it read matches the account asked
for, and fails with `request_failed` if not. A session that silently failed to
install would otherwise return the *previous* account's numbers under the
requested name, which is worse than an error.

### Listing the accounts and their plans

`./usage --plans` answers "which accounts are set up, and what are they on" in a
single pass:

```json
{
  "status": "ok",
  "accounts": [
    { "account": "pro",  "status": "ok", "plan": "claude_max 5x",
      "org_uuid": "...", "org_name": "..." },
    { "account": "team", "status": "unauthenticated",
      "detail": "HTTP 403; log in again and re-capture team" }
  ]
}
```

One browser connection for all of them rather than one process each, so it is
quicker than calling `--account` per account, and the result is cached for 60
seconds. Each account carries its own `status`: one expired session does not
stop the others being reported, and the call still exits 0 as long as the
browser could be reached.

`accounts.json` holds live sessions. It is gitignored and readable only by you,
but it is a credential file — the honest cost of not running one browser per
account. Sessions expire roughly monthly; `./usage --accounts` shows when.

## Output

| Field | Meaning |
|---|---|
| `status` | `ok`, or why the reading failed |
| `session_pct` | Percent of the 5-hour window used |
| `session_resets_at` | When that window rolls over |
| `weekly_pct` | Percent of the weekly limit used |
| `weekly_resets_at` | When the week rolls over |
| `blocking` | Active critical limits, which may name a specific model |
| `credits_enabled` | Whether extra usage credits are on |
| `credits_spent` / `credits_limit` | Credit spend, if enabled |
| `source` | Always `browser` |
| `account` | The account id asked for, or `null` |
| `org_uuid` | Which organisation the numbers are for |
| `plan` | The account's plan, e.g. `claude_max 5x`, or `null` |

`blocking` can name a model that is exhausted while both percentages still look
healthy.

The percentages are relative to the account's current limits, so a plan change
is invisible in them — an upgrade makes the same work report a *smaller* number.
`plan` is what makes that visible. It is Anthropic's own `analytics_subscription_plan`,
undocumented and passed through untouched, so treat it as an opaque label that may
be `null`.

## Failure statuses

All exit 1, with the reason in `status` and detail in `detail`.

| Status | Meaning | Fix |
|---|---|---|
| `unauthenticated` | The login expired | `./usage --login`, then sign in |
| `browser_unavailable` | No browser reachable, or one that stopped answering | `./usage`, or `docker compose up -d`. If `detail` says it answers HTTP but not the debug protocol, restart the browser |
| `blocked` | Cloudflare did not clear | Usually temporary; retry |
| `request_failed` | Endpoint changed, or another error | Read `detail` |

A response the digest does not recognise is reported as `request_failed`, never
as `ok` with null percentages — a null renders as 0%, which reads as plenty of
headroom and is the opposite of the truth. Treat every reading as best-effort
and show "usage unavailable" rather than zero.

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `USAGE_CDP_URL` | `http://localhost:9222` | Where the browser is |
| `USAGE_IDLE_TAB_MAX_MB` | `250` | Recycle the idle tab past this heap size |

```bash
USAGE_CDP_URL=http://other-host:9222 ./usage
```

## Behaviour

- Results are cached for 60 seconds; polling is cheap.
- Concurrent callers are serialised with a file lock on macOS and Linux. Windows
  has no such lock, so the cache is the only guard there.
- A claude.ai tab is opened per reading and closed again, so the browser keeps
  no page alive between runs and its memory stays flat. If you already have
  claude.ai open, that tab is used instead and left exactly as it was — nothing
  of yours is opened, navigated or closed.
- On Windows the browser starts minimised once a profile exists. The first run
  stays visible, because that is the run you log in on.
- The browser uses its own profile directory, not your everyday one. A browser
  with debugging enabled can be driven by anything else on the machine.
- Closing the browser does not lose the login — it is on disk. `./usage` starts
  it again.
- The idle tab that keeps the browser alive is replaced once its heap passes
  `USAGE_IDLE_TAB_MAX_MB`. A replacement opens before the old one closes, so the
  tab count never reaches zero and the browser never restarts — but the old
  renderer dies and its memory is returned. Without this the browser creeps
  upward forever: every reading commits a few hundred KB of V8 heap in that
  process which is never given back, and no amount of garbage collection helps
  because none of it is garbage.
- A browser left running for a long time can keep answering the debugger's HTTP
  side while its debug protocol stops replying. The reading then fails with
  `browser_unavailable` saying exactly that, and restarting the browser fixes
  it — nothing else does, so it will not try to start a second one.

## How it works

Anthropic publishes no API for plan usage. The only source is the endpoint
claude.ai's own settings page calls, which needs your session — and Cloudflare
rejects any client that is not a real browser.

Cookies alone are not enough, which is worth knowing before trying the obvious
shortcut:

- An HTTP client with valid cookies gets `403` and `cf-mitigated: challenge`.
- A freshly minted `__cf_bm` does not change that.
- Neither does replaying a complete browser header set.
- Chrome's new headless mode is challenged too, while the same browser visible
  is not.

What is fingerprinted is the client itself. So a browser you are logged into
stays running, and `usage.py` connects to it and runs the fetch from inside a
claude.ai tab — the same request the settings page makes. No cookie is copied
out and no browser is launched behind your back.

**This reads your own account through your own session.** No API key, no token,
nothing sanctioned, and therefore best-effort. Do not build anything critical on
it.

## Tests

```bash
./test_usage.py    # or: pytest
```

Covers the digest and the debugger address. No browser or network needed.

## License

MIT — see [LICENSE](LICENSE).
