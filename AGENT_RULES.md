# Mission Control — agent rules

Critical, standing constraints for any agent working in this project.

---

## Email is the channel for anything that needs Ron's action (added 2026-07-11)

**Whenever you need an action or a decision from Ron, EMAIL HIM.** Do not rely on
the chat reply alone — he is often not watching the session, and unattended
cycles (steward, night-review, scheduled runs) have no one reading them at all.

He will **reply to himself** on the thread, so his answer lands back in the
inbox where you can read it on a later cycle even if he never responds in chat.

### Send

Reuse the existing mailer — **do not build a new one, do not add SMTP code**:

```bash
python tools/night-review/send_mail.py \
  --subject "[Clayrune steward] DECISION NEEDED: <one line>" \
  --body-file /tmp/mail_body.txt
```

Defaults to `leviran1@gmail.com`. Credentials: Gmail App Password at
`~/.clayrune/night-mail.json` (or `NIGHT_MAIL_USER` / `NIGHT_MAIL_APP_PASSWORD`).
Never commit them.

**Subject convention — always prefix so replies are findable:**
`[Clayrune <role>] DECISION NEEDED: …` / `BLOCKED: …` / `FYI: …`

**Body must be self-contained.** He may read it days later, on a phone, with no
session context. Include: what you need, the **exact command** you'd run, the
risk/blast radius, how to roll back, and what happens if he does nothing.

### Read his reply

The **`mail` MCP server** (global, `tools/mail-mcp/server.py`) gives READ-ONLY
IMAP access to the same inbox: `list_recent`, `search_email` (Gmail syntax),
`read_email`. Search the subject tag to pick up replies from a previous cycle:

```
search_email("[Clayrune steward]")
```

**Check for a reply at the START of every unattended cycle** before re-raising a
decision you've already sent — otherwise you'll spam him with the same ask.

### Discipline

- Email for **decisions and blockers**, not routine progress. The backlog note is
  the durable log; email is the interrupt. Emailing every cycle trains him to
  ignore it — exactly the failure mode `PushNotification` warns about.
- An emailed ask does **not** authorize the action. Approval is his reply.
- Still post the backlog note as well; email is in addition to, not instead of,
  the charter log.
