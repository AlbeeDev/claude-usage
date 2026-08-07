# claude-usage-mcp

Read your Claude plan usage — the numbers behind claude.ai's "Current session"
and "Weekly" meters — from the CLI, from an MCP tool, or over HTTP.

```console
$ ./check.py
{
  "status": "ok",
  "session_pct": 16.0,
  "session_resets_at": "2026-08-05T22:40:00Z",
  "weekly_pct": 30.0,
  "weekly_resets_at": "2026-08-08T07:00:00Z",
  "blocking": [],
  "credits_enabled": false,
  "credits_spent": 0.0,
  "credits_limit": null,
  "source": "fallback-chromium"
}
```

Useful for long unattended runs that should stop before hitting a limit rather
than being killed mid-task, and for putting a usage meter in your own tools.

## How it works, briefly

Anthropic publishes no API for plan usage. The only source is the endpoint
claude.ai's settings page calls, which needs your session — and Cloudflare
rejects any client that isn't a real browser, cookies or not.

So this keeps a Firefox container that holds your login (you log in once, by
hand) and reads the numbers through a genuine browser: `check.py` drives a
Chromium seeded with that session. There is also an optional setup where a
userscript in that Firefox reports the numbers on its own, which the checker
prefers when it is available.

Cookies alone are not enough, which is worth knowing before you try the obvious
shortcut: an HTTP client sending perfectly valid cookies still gets `403` with
`cf-mitigated: challenge`, a freshly minted `__cf_bm` does not change that, and
neither does replaying a complete browser header set. What is being fingerprinted
is the client itself, so the request has to come from a real browser.

**This reads your own account through your own logged-in session.** No API key,
no token, nothing sanctioned — and therefore best-effort by nature. Don't build
anything critical on top of it.

## What this costs you

Worth knowing before you start, because a browser holding a login is not a
zero-maintenance thing:

- **Docker**, and a machine that stays on. The browser has to keep existing.
- **One manual login**, by hand, in that browser.
- **Logging in again roughly monthly**, when the session cookie expires. This
  never goes away.
- **Python, Playwright with Chromium, and Xvfb** on the host, plus access to the
  docker CLI.

## Setup

```bash
docker compose up -d
```

Open <http://localhost:5800>, which is the Firefox holding the login. Go to
claude.ai and **log in with the email-code flow, not Google** — Google
frequently blocks unfamiliar browsers, the emailed code doesn't care.

That's the only manual step. Everything else is optional.

> That Firefox UI has no password on it. On a shared or untrusted network, bind
> it to `127.0.0.1:5800` in `docker-compose.yml`, or put it behind something
> that does have auth.

Then install what does the reading and try it:

```bash
pip install -r requirements.txt
python3 -m playwright install chromium
./check.py
```

`check.py` drives its own Chromium, seeded with the session cookie it reads out
of the Firefox container — which is why it needs docker CLI access. Reports
`"source": "fallback-chromium"`.

### Optional: the push path

You do not need this. It trades a few minutes of clicking for a setup that is
harder for Cloudflare to object to, since nothing is being automated — a real
logged-in tab reports its own numbers instead.

1. In that Firefox, install Violentmonkey or Tampermonkey from
   addons.mozilla.org.
2. New script → paste [`userscript.js`](userscript.js) → save.
3. Open a claude.ai tab and **pin it**, so session restore brings it back.

That tab then reports every 2 minutes and `check.py` prefers its reading,
skipping the browser entirely — no Playwright, no Xvfb, no docker access, and
`"source"` becomes `"browser"`. It is also what makes the HTTP endpoint below
return anything.

These steps have not been tested end-to-end. The plumbing has: pushed readings
are stored, preferred while fresh, and fall back cleanly when stale.

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

**HTTP** — `GET http://localhost:8000/latest` returns the last reading and its
receive timestamp. Best option for an app in another container: no docker
socket, no browser, just a request. **Requires the optional push path above** —
without it there is nothing to serve and it returns `{"error":"no reading yet"}`.

## Maintenance

Your session cookie lasts about a month. When it expires the checker returns
`{"status": "unauthenticated"}` — log in again in the Firefox container. That's
the only recurring chore.

Cache the result if you're polling; the checker already caches for 60 seconds
and serializes concurrent callers.

If you renamed the compose project, point the checker at your own container:

```bash
USAGE_FIREFOX_CONTAINER=my-firefox ./check.py
```

Treat every reading as best-effort — the endpoint is undocumented and can change
without warning. Show "usage unavailable" on a non-`ok` status rather than zero:
a zero reads as plenty of headroom, which is the opposite of the truth.

## Tests

`./test_check.py` (or `pytest`) covers the digest and the pushed-reading gate.
No browser or network needed.

## License

MIT — see [LICENSE](LICENSE).
