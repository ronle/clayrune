# r/ClaudeAI — reply playbook (first 3 hours)

Pre-drafted answers for the post. Copy, adapt, keep it conversational. Ron's voice:
short, honest, no marketing gloss.

**Hard rules for every reply** (same as the post):
- Never say "always on" / "works while your laptop is closed" / promise a hosted Cloud tier.
- Never disparage other tools — their users are our users. "Different shape" not "worse".
- Don't get defensive. Concede fair hits; that's what earns the thread.
- If you don't know, say "don't know yet" — it reads better than a hedge.

---

## 1. "How is this different from Conductor / Vibe Kanban / Crystal / worktree runners?"
*(The single most likely top comment. Answer generously.)*

> Fair question, and honestly there's overlap. Most of those focus on running parallel
> Claude sessions on **one** repo — usually git-worktree based. Clayrune's unit is the
> **project**, not the branch: several separate projects side by side, each with its own
> rules, backlog and memory, plus scheduled/recurring runs and phone access. If you're
> parallelising work inside a single repo, those tools are arguably a better fit than
> mine. If you're juggling five unrelated projects, that's the gap I built for.

## 2. "Why not just use tmux / a few terminal tabs?"

> That's literally what I did for months, and it works right up until you have ~5 of them.
> The bit tmux doesn't solve is *state*: which agent is mid-task, which is waiting on a
> question, what did this one decide yesterday. Clayrune is mostly about that — status at
> a glance and memory across sessions. If tmux covers you, genuinely, stick with tmux.

## 3. "Does it burn more tokens / eat my Max quota faster?"  ← expect this one

> It shells out to the Claude Code CLI you already have, so the agent work costs exactly
> what it would cost you in a terminal. The extra usage is small helper calls — mainly
> summarising a session into memory — and those run on a cheap model. It doesn't proxy or
> re-route anything, and there's no Clayrune API key.

## 4. "What data leaves my machine?"

> None. It runs locally against your own Claude subscription, your repos stay on your
> disk, and there's no telemetry phoning home. It's MIT — the whole thing is readable.

## 5. "Isn't this just a wrapper around Claude Code?"

> It is a layer on top, yes — I'd call that the point rather than a defence. It doesn't
> touch how Claude codes. What it adds is the stuff around it: many projects at once,
> memory that survives a session, a backlog, scheduling, and reaching it from a phone.

## 6. "Platforms?"

> Windows, macOS and Linux. There's an installer rather than a clone-and-configure.

## 7. "How does the memory actually work?"

> Each project keeps a memory file — a curated index plus an archive. At the end of a
> session (and at checkpoints mid-session) it writes down what happened, and on the next
> dispatch the relevant parts get injected back into the agent's context. Net effect:
> the agent doesn't re-read your whole repo to work out where you left off.

## 8. "Does it work with Gemini / Codex / other agents?"

> They're supported, but Claude Code is what I actually build and test against day to
> day — so treat the others as working-but-secondary rather than a headline feature.

## 9. "What happens when my machine sleeps?"

> The agent stops — it's running on your box, so there's no magic there. There's a
> setting that keeps the machine awake while an agent is actually working, and it tells
> you when a run was cut short. I'd rather say that plainly than pretend otherwise.

## 10. "Will it stay free? What's the catch / business model?"

> The app is MIT and stays that way. Phone/remote access also works free — you point it
> at your own tunnel (Tailscale, ngrok, or plain LAN). The thing I'd eventually charge
> for is the zero-config version of that: a `you.clayrune.io` address you enable with one
> click instead of setting up DNS and TLS yourself. Paying to skip the plumbing, not to
> unlock a feature.

## 11. "Can I get remote access without paying?"  → same as #10, lead with "yes".

## 12. Hostile: "another vibe-coded AI wrapper / slop"

*(Don't argue. Concede the shape, point at the substance, move on.)*

> Fair suspicion, there's a lot of it about. It's built with Claude and I've said so —
> but it's the thing I actually run my own work on every day, not a weekend demo. Code's
> MIT if you want to judge it directly. Happy to hear what would make it not-slop to you.

## 13. "Can I see it doing something real?" → point at the clip + offer a specific walkthrough.

## 14. If someone reports a bug in-thread
> Best possible outcome — that's exactly why I posted. Grab it as a GitHub issue and
> I'll pick it up.

---

## Engagement discipline
- Reply to **every** top-level comment in the first ~3h. That drives the ranking more
  than the post text does.
- Thank bug reports harder than compliments.
- Anyone who says "I'll try it" → get them to DM, that's a beta tester.
- Do NOT vote-manipulate or ask for upvotes (Rule 10 = permanent ban).
