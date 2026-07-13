# Clayrune — launch copy

**Owner:** Steward (charter `2375e16b`). **DRAFT v1, 2026-07-12.**
Phase C of `docs/MARKETING_CAMPAIGN_PLAN.md`. Nothing here is posted — posting is
an external, irreversible act and needs Ron's explicit approval.

## Facts this copy is allowed to claim (verified 2026-07-12)

| Claim | Verified |
|---|---|
| Repo `github.com/ronle/clayrune`, **public**, **MIT** | GitHub API |
| Built for Claude Code; **also runs Gemini and Codex** agents | provider dispatch on `master`, exposed in the UI |
| Local-first, bring-your-own-key | — |
| Phone control via the clayrune.io tunnel | shipped |
| Scheduler, autonomous steward, hivemind (multi-agent) | shipped |
| Cross-session memory | shipped |
| Live interactive demo | clayrune.io/demo — rebuilt + verified 2026-07-11 |

**Baseline before launch: 1 GitHub star.** That's the number to measure lift from.

**Do NOT claim:** "works with any agent" (it's Claude-first — Gemini/Codex are
supported, not equal), "no setup" (there's an installer), or anything about the
hosted cloud (not shipped; lives in `clayrune_cloud`).

**The one rule for all copy:** we are *not* another coding agent. We're the
console that runs the ones you already have. Every headline must survive the
question "so it's Cursor?" — if it doesn't, rewrite it.

---

## 1. Show HN

HN punishes marketing voice. Plain, first-person, honest about limits. Post
Tue–Thu ~8–10am ET. Then answer every comment for the first 3 hours.

**Title** (80 char limit; leads with the fleet + phone hook, not the tech):

> Show HN: Clayrune – run and monitor many Claude Code agents from one dashboard

Alternates:
- `Show HN: Clayrune – mission control for your coding agents, including from your phone`
- `Show HN: I got tired of babysitting Claude Code in six terminals, so I built this`

**Body:**

> I use Claude Code a lot, across half a dozen projects. The workflow that
> emerged was six terminal tabs, no idea which agent was stuck, which was waiting
> on me, or which had quietly finished an hour ago. If I walked away from the
> desk, everything stopped.
>
> Clayrune is the layer I built on top of it. Every project is a tile; every
> agent runs where you can see it. You dispatch work, watch it stream, and
> approve plans — from the browser or, over a tunnel, from your phone. It also
> does the things I kept wanting: schedule an agent, give one a standing charter
> and let it work unattended, and keep memory across sessions so agents don't
> start every task cold.
>
> It's not a coding agent and doesn't try to be — it runs the agent you already
> use. Claude Code is the first-class path; Gemini and Codex also work.
>
> Local-first, your own API key, MIT. There's a live simulated demo if you'd
> rather click than install: https://clayrune.io/demo
>
> Honest limits: it's Claude-Code-first, the installer is Windows-friendly and
> rougher elsewhere, and the hosted version doesn't exist yet. Happy to answer
> anything.
>
> Repo: https://github.com/ronle/clayrune

**Why this works:** opens with a concrete, recognizable pain (six terminals);
never says "AI-powered"; states limits before the crowd finds them; the demo link
lets skeptics evaluate without installing.

---

## 2. Product Hunt

**Tagline** (60 char): `Mission control for your AI coding agents`

**Description:**

> Clayrune puts every AI coding agent you run into one dashboard. Dispatch work
> across all your projects, watch it stream live, and approve plans — from your
> desk or your phone. Schedule agents, or hand one a standing charter and let it
> work while you don't.
>
> It isn't another coding agent. It runs the ones you already use — Claude Code
> first, plus Gemini and Codex. Local-first, your own key, MIT-licensed.

**First comment (maker):**

> Hi PH 👋 I built Clayrune because I was running Claude Code across six projects
> and had turned into a human process monitor — tabbing between terminals to find
> which agent was stuck or waiting on an answer.
>
> Three things I most wanted, which are the three I'd point you at:
> • **The fleet view.** Every project, every agent, one screen.
> • **The phone.** Approve a plan from the sofa. This is the one people don't
>   expect and end up using most.
> • **It runs without you.** Scheduled agents, and a "steward" you give a standing
>   goal to — this changelog and our own website upkeep are run by one.
>
> Free, open source, runs on your machine with your own API key. There's a live
> demo (no install) if you want to poke at it: clayrune.io/demo
>
> I'll be here all day — tell me what's missing.

*(The steward line is true and is our most interesting proof point — the product
maintains its own marketing site. Lead with it in interviews.)*

---

## 3. X / Twitter thread

1/ I was running Claude Code across 6 projects and had become a human process
monitor — six terminals, no idea which agent was stuck, waiting, or done.
So I built the missing layer. 🧵

2/ Clayrune is mission control for coding agents. Every project a tile. Every
agent visible. Dispatch, watch it stream, approve plans — one screen.
[demo GIF]

3/ The part nobody expects: it works from your phone. Approve a plan from the
sofa. Kill a run from the train. Your agents don't need you at the desk.
[phone clip]

4/ It runs without you. Schedule an agent. Or hand one a standing charter and let
it work unattended. (Our own website is kept in sync by one of these.)

5/ It's not another coding agent — it runs the one you already use. Claude Code
first-class, Gemini and Codex too. Local-first, your own key, MIT.

6/ Live demo, no install: clayrune.io/demo
Repo: github.com/ronle/clayrune
⭐ if it's useful — I'm building this in the open.

---

## 4. r/ClaudeAI (soft launch — do this FIRST)

Reddit smells promotion instantly. No links in the title, story first, link last.

**Title:** `I got tired of babysitting Claude Code in 6 terminals, so I built a dashboard for it`

**Body:**

> Anyone else running Claude Code across several projects at once? I kept losing
> track of which agent was stuck, which was waiting on an answer, and which had
> finished twenty minutes ago while I stared at a different tab.
>
> So I built a dashboard that sits on top of Claude Code. Projects as tiles,
> agents streaming live, plan approvals in one place. Two things that changed how
> I work more than I expected:
>
> - **Phone access.** I approve plans from the kitchen now. Turns out most of my
>   "babysitting" was just waiting to say yes.
> - **Unattended agents.** Scheduled runs, and a "steward" you hand a standing
>   goal to. One of them maintains my project's website.
>
> It's free, MIT, runs on your machine with your own key. Not trying to replace
> Claude Code — it *runs* Claude Code.
>
> Demo without installing: clayrune.io/demo · Code: github.com/ronle/clayrune
>
> Genuinely after feedback on what's missing — especially from people running
> more than 2-3 projects.

Also post to the Anthropic Discord (#showcase) with the same framing.

---

## 5. Pre-launch checklist (blocking)

- [ ] **Demo GIF + 90s video** — `MARKETING_ASSETS_SPEC.md` A1/A2. **Blocks the
      X thread and PH gallery.** Human capture; the #1 blocker.
- [ ] **Refresh `assets/clayrune-dashboard-light.png`** — verified stale
      2026-07-12: since it was taken, the sidebar was tiered
      (Workspace/Advanced), the top-strip was removed, and a "Waiting on you"
      block was added. It's the hero's base image — the first thing a visitor
      sees.
- [ ] **GitHub repo description + topics** — currently *"Multi-project management
      dashboard for Claude Code agents"*. Fine, but lead with the differentiator:
      *"Mission control for your Claude Code agents — run, schedule, and monitor
      many of them across every project, from your desk or your phone."*
      Topics: `claude-code`, `ai-agents`, `agent-orchestration`, `developer-tools`.
- [ ] **Canonicalize the repo URL on the site** — 60 links still point at
      `github.com/ronle/mission-control`. They redirect, so nothing is broken, but
      it looks careless on a launch day that drives traffic there.
- [ ] Port the README's "Isn't this just Claude Code?" table to the landing page.
- [ ] Baseline metrics recorded: **1 star** (2026-07-12).

## 6. Sequence

**r/ClaudeAI + Discord** (fix what they report) → **Show HN** (Tue–Thu am ET) →
**Product Hunt** (same or next day, needs the video) → X thread on PH day.

Soft launch first, deliberately: it surfaces the objections cheaply, so the HN
thread doesn't become the place we discover our own weak spots.
