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
- No API key, no cookie extraction, no browser download

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

Log into claude.ai there. Back on the server:

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

`blocking` can name a model that is exhausted while both percentages still look
healthy.

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

```bash
USAGE_CDP_URL=http://other-host:9222 ./usage
```

## Behaviour

- Results are cached for 60 seconds; polling is cheap.
- Concurrent callers are serialised with a file lock on macOS and Linux. Windows
  has no such lock, so the cache is the only guard there.
- One claude.ai tab is opened on first use and reused after, so nothing opens,
  navigates or takes focus while you work.
- On Windows the browser starts minimised once a profile exists. The first run
  stays visible, because that is the run you log in on.
- The browser uses its own profile directory, not your everyday one. A browser
  with debugging enabled can be driven by anything else on the machine.
- Closing the browser does not lose the login — it is on disk. `./usage` starts
  it again.
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
