# claude-usage-mcp

Read your Claude plan usage — the numbers behind claude.ai's "Current session"
and "Weekly" meters — from the CLI, or as an MCP tool.

```console
$ ./check.py
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

So this runs a Chromium in a container, you log into it once by hand, and it
keeps running. `check.py` connects to that same browser and asks the question
from inside a page — which is what the site's own settings modal does. Nothing
copies cookies out, and no second browser is launched.

**This reads your own account through your own logged-in session.** No API key,
no token, nothing sanctioned — and therefore best-effort by nature. Don't build
anything critical on top of it.

## What this costs you

A browser holding a login is not a zero-maintenance thing:

- **Docker**, and a machine that stays on. The browser has to keep existing.
- **One manual login**, by hand, in that browser.
- **Logging in again roughly monthly**, when the session expires. This never
  goes away.

## Setup

```bash
docker compose up -d
pip install -r requirements.txt
```

No browser download — `playwright` is only used to talk to the one in the
container, so `playwright install` is not needed.

Open <http://localhost:3000>, which is the Chromium holding the login. Go to
claude.ai and log in. Then:

```bash
./check.py
```

That's the whole setup.

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

**MCP** — register the server and any Claude session gets a `claude_usage` tool:

```json
{
  "mcpServers": {
    "claude-usage": {
      "type": "stdio",
      "command": "python3",
      "args": ["/path/to/claude-usage-mcp/mcp_server.py"]
    }
  }
}
```

## Failure statuses

All exit 1, with the reason in `status`:

| Status | Means | Fix |
|---|---|---|
| `unauthenticated` | The login expired | Log in again at <http://localhost:3000> |
| `browser_unavailable` | The container isn't reachable | `docker compose up -d` |
| `blocked` | Cloudflare didn't clear | Usually temporary; try again |
| `request_failed` | The endpoint changed, or something else | Check what `detail` says |

A response the digest doesn't recognise is a `request_failed`, never an `ok`
full of nulls — a null percentage renders as 0%, which reads as plenty of
headroom and is the opposite of the truth. Treat every reading as best-effort
and show "usage unavailable" rather than zero.

## Notes

The checker caches for 60 seconds and serializes concurrent callers, so polling
it is cheap.

If your browser is somewhere else, point the checker at it:

```bash
USAGE_CDP_URL=http://other-host:9223 ./check.py
```

## Tests

`./test_check.py` (or `pytest`) covers the digest and the debugger address. No
browser or network needed.

## License

MIT — see [LICENSE](LICENSE).
