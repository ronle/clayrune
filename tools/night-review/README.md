# Night-shift maintenance run

A self-gating nightly Clayrune schedule that does conservative repo maintenance
**only when the 5-hour usage window has headroom**, then emails a summary.

## How it works

1. The Clayrune scheduler fires the schedule nightly (default **03:00 local**).
2. The dispatched agent runs `PROMPT.md`. Its **first** step (Tier 0) hits
   `GET /api/system/usage` and reads `usage_limits.five_hour.utilization`.
   - `> 10%` used (or usage data unavailable) → logs one line to
     `docs/night-review-skips.log` and stops. Cheap no-op.
   - `<= 10%` used → proceeds with the maintenance tiers.
3. Tier 1 grooms the backlog, Tier 2 makes/commits only riskless edits (typos,
   dead doc links, formatting), Tier 3 writes human-review-only suggestions.
4. It writes `docs/night-review-<date>.md` and emails the reviewer a summary
   via `send_mail.py` (Gmail SMTP).

The schedule is self-gating because the Clayrune scheduler only dispatches
agents — it can't natively skip a run, so the gate lives in the prompt.

## One-time setup — Gmail credentials (required for the email step)

The email step needs a Gmail **App Password** (not your normal password; the
account must have 2-Step Verification on). Until this is set, runs still work —
they just note "email not sent" at the top of the report.

1. Create an App Password: <https://myaccount.google.com/apppasswords>
   (pick "Mail" / "Other"). Google shows a 16-char code like `abcd efgh ijkl mnop`.
2. Create `~/.clayrune/night-mail.json` (on this box:
   `C:\Users\levir\.clayrune\night-mail.json`) — **never commit it**:

   ```json
   {
     "user": "youraddress@gmail.com",
     "app_password": "abcd efgh ijkl mnop",
     "to": "leviran1@gmail.com"
   }
   ```

   (Spaces in the app password are fine — the mailer strips them.)

   Or, instead of the file, set env vars: `NIGHT_MAIL_USER`,
   `NIGHT_MAIL_APP_PASSWORD`, `NIGHT_MAIL_TO`.

3. Test it:

   ```
   python tools/night-review/send_mail.py --subject "night-review test" --body "hello"
   ```

   Exit 0 = sent. Exit 2 = credentials missing. Exit 1 = send failed (check the
   error; usually a wrong app password or 2FA not enabled).

## Files

- `send_mail.py` — Gmail SMTP sender (stdlib only). `--subject` + one of
  `--body` / `--body-file` / stdin; `--to` overrides the recipient.
- `PROMPT.md` — canonical copy of the agent prompt the schedule runs. Edit
  here **and** update the live schedule (`PUT /api/schedules/<id>` or the
  Scheduler UI) to keep them in sync.

## Managing the schedule

- List: `curl -s http://localhost:5199/api/schedules`
- Change the time / disable / re-enable: the Scheduler UI, or
  `PUT /api/schedules/<id>`.
- The dated reports (`docs/night-review-*.md`) and the skips log
  (`docs/night-review-skips.log`) are gitignored runtime output.
