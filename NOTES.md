# Dev Notes

## Usage 5h Widget — Limit Calibration

The checkpoints in the widget are estimates, not documented Anthropic limits.
Refine them as real data points accumulate.

**Current values (as of Jun 2026):**
- Pro off-peak: ~104K weighted output tokens
- Team off-peak: ~130K weighted output tokens
- Typical/peak heavy: scaled proportionally (see `renderUsage5h` in dashboard.py)

**How to calibrate:**
When you hit or approach a limit, note the weighted token count shown in the widget.
- If it's a clean limit hit (Claude Code only, no claude.ai chat mixed in), that's your ceiling
- If the session had mixed usage (chat + Claude Code), the real limit is higher than what the widget shows
- Target: 3+ clean data points before locking in numbers

**Known flaw:** The widget can't distinguish between accounts (Team vs Pro token sources)
since JSONL files have no account tag. The `session_accounts` table tags sessions going
forward with the active account at scan time, but historical data is untagged.

---

## Possible Improvements

- **Account split**: Once `session_accounts` has enough history, split the 5h bar by account
  so Team and Pro usage are tracked separately rather than lumped together.

- **Reset timer**: Show estimated time until the 5h window resets. Would need to track
  when the first turn of the current window was logged.

- **Limit notifications**: Webhook or Discord alert when weighted usage crosses a threshold
  (e.g. 80% of Team off-peak checkpoint).

- **Claude.ai chat separation**: Currently no way to distinguish Claude Code turns from
  claude.ai chat turns in the JSONL files — both land in the same usage pool.
  If Anthropic ever adds a `source` field to the JSONL records, use it here.
