# AskUserQuestion — real-time when watched, offline channel when not

**Goal (Ron, 2026-07-13):** if he's on the active window he sees an interactive form
in real time; if the run is unattended, the question goes out on a user-defined
channel he can review and **reply to offline**.

## The actual bug

The system prompt promises every agent:

> "When you need to ask the user, use the AskUserQuestion tool. Clayrune intercepts
> it and presents an interactive form."

**That tool is not in the headless toolset.** Confirmed live (ToolSearch returns
nothing) and by the night review three nights running. Claude Code ships
`AskUserQuestion` as a *native* tool in **interactive** mode; Clayrune runs it
**headless**, where it is absent. So the prompt lies, and every unattended agent that
needs to ask something falls back to a plaintext decision sheet nobody reads.

## What already exists — do NOT rebuild

- **`MC_TOOL_PROTOCOL_PROMPT` + `apply_mc_tool_blocks()`** (`agent_runtime.py`): a
  provider-agnostic text protocol — the model emits a ```` ```mc:question ```` fence
  and stops; the runtime parses it into `session['pending_questions']` +
  `waiting_for_question`. **Already wired for Gemini, never for Claude**, on the
  false assumption that Claude has the native tool.
- **The entire downstream path**: SSE `question` event → `renderAgentQuestion` →
  chips → `_dispatchQuestionAnswer` → `POST /agent/followup`. **Answering a question
  IS sending a follow-up** — that is the resume path an email reply must reuse.
- **`session['trigger_type']`** — `manual` / `schedule` / `hivemind` / steward.
- **`session['_last_sse_poll_time']`** — a viewer heartbeat. The Guardian already
  uses exactly this ("question + no SSE poll for 60s → may have been missed"), so
  attended-detection is precedent, not invention.
- **`tools/night-review/send_mail.py`** — the mailer. Per AGENT_RULES: reuse it, add
  no SMTP.

## Design

### 1. Give Claude the ability to ask (the real fix)
`ClaudeRuntime` appends `MC_TOOL_PROTOCOL_PROMPT` to its system prompt (dispatch and
respawn paths, exactly as Gemini does) and runs `apply_mc_tool_blocks()` on each
completed turn. Then fix the lying system-prompt line to describe what actually works.

### 2. Attended → real time
Falls out of #1 for free: `pending_questions` → SSE → the existing form.

### 3. Unattended → user-defined channel
New `mc/question_channel.py`.

**Attended test:** a question is *attended* if an SSE viewer polled this session
within `question_channel_grace_s` (default 45s).

**The grace window is the whole trick.** Deliver instantly and a user who opens the
tab three seconds later gets a redundant email; never deliver and an unattended run
hangs forever. So: raise → wait the grace → re-check for a viewer → deliver only if
still nobody.

**Config (per-project, falling back to global):**
- `question_channel`: `off` | `email` (default `email`)
- `question_channel_to`: recipient (default: the night-mail `to`)
- `question_channel_grace_s`: default 45

**Delivery:** subject `[Clayrune question] <project> · <qid8>` — the qid is the reply
key. Body carries the question, **numbered** options with descriptions, and how to
reply. Self-contained: he may read it days later, on a phone, with no session context.

### 4. Reply offline → resume the agent
An IMAP poller (background thread, reusing the night-mail credentials) scans for
replies whose subject carries a known `qid`, takes the first meaningful line of the
reply, maps it to an option (number → label, or label match, else free text), and
answers through the same `/agent/followup` path the UI uses.

**Idempotency is load-bearing:** an answered qid is never answered twice; a reply for
an unknown or expired qid is ignored, not guessed.

## Safety / non-goals

- Best-effort, **never load-bearing** — a channel failure must never break a run.
  Same posture as Scribe and the Distiller.
- The steward's reversibility firewall is untouched. **A question is not an
  approval.** An emailed answer resumes an agent; it does not authorize anything the
  agent could not already do.
- No new SMTP code, no new credentials.
- One delivery per question id, ever. Alert fatigue kills the channel.

## Build order

1. `ClaudeRuntime` ← MC Tool Protocol (+ tests). **This alone un-breaks the promise.**
2. `question_channel.py`: attended check + email delivery (+ tests).
3. Hook into the question-raised path in `agent_routes.py`.
4. IMAP reply poller → `/agent/followup` (+ idempotency tests).
5. Config keys + Settings surface.
6. Fix the system-prompt text.
