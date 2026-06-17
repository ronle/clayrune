# `status` — cross-project status, on demand

One command to see where every Mission Control project stands — instead of
opening each one and asking for a brief status update.

```bash
python tools/status/status.py            # full digest
python tools/status/status.py --needs    # ONLY projects that need you (quick check)
python tools/status/status.py --all      # include projects dormant >30 days
python tools/status/status.py --json     # machine-readable
python tools/status/status.py --port 5199
```

## What it shows

Projects are grouped by urgency, freshest first:

- **⚠ NEEDS YOU** — awaiting plan approval, a pending question, blocked, or a
  failed resume.
- **▶ WORKING** — an agent turn is running right now.
- **✓ DONE (recent)** — finished its last turn.
- **· IDLE / where we left off** — resting; the line is the last thing that
  happened, so you can pick up where you left off.

## How it works

Pure read-only client over the existing `GET /api/projects` endpoint —
**no server changes, no restart, no new state.** The status collapse mirrors
the dashboard's own `friendlyStatus()` (`static/js/render-core.js`), so a closed
project reads the same here as it would on the dashboard tile.

`live_agent` (server-authoritative, fresh for *all* projects every poll) is the
primary signal. A closed *errored* session has no `live_agent`, so a
`resume failed` activity entry is also treated as stuck; the noisier generic
`error` match is intentionally skipped to avoid false alarms.

## Exit codes

`0` ok · `1` server unreachable · `2` something needs you (only with `--needs`,
so you can wire it into a watch/cron/alert).

## Config

Port via `--port` or `MC_PORT` (default `5199`). Dormant cutoff via
`--dormant-days` (default `30`).
