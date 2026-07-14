# Clayrune — Marketing Campaign Plan

> ## ⚠️ SUPERSEDED IN PART — read `docs/LAUNCH_PLAN.md` first (2026-07-13)
>
> `LAUNCH_PLAN.md` is now the authoritative **strategy, sequence, and metrics**.
> This doc is retained **only** for §2 (competitive landscape) and §4 (segments),
> which still stand.
>
> **Three things below are now FORBIDDEN** (`clayrune-cloud/docs/STEWARD_HANDOFF.md`
> §2 — legal, not stylistic):
> - ❌ **"always on" / "no computer to babysit"** — that is the *parked* Cloud tier.
>   Connect is remote access to the user's own machine. **If it sleeps, the agent
>   stops.** Struck below.
> - ❌ The **Cloud tier** in any copy: parked by Ron, 2026-07-13.
> - ❌ **Stars / downloads / HN rank** as success metrics (§7). Track WAU, D7/D30,
>   % enabling remote.

**Owner:** Steward (charter `2375e16b`). **Status:** v1, 2026-07-11 — partly stale.
**Purpose:** the anchor document for the campaign to make Clayrune a known tool
that stands alongside main-market competitors. Future steward cycles execute
one slice of this per cycle. This doc is the plan; the backlog notes are the log.

Positioning language already live and to stay consistent with:
- Site (`marketing/v2`): *"operator console for long-running Claude agents."*
- ~~Cloud investor brief: "Your own AI agent — always on, on your phone, with no
  computer to babysit."~~ **STRUCK 2026-07-13 — forbidden claim.**

---

## 1. The category problem (why marketing is non-trivial)

Clayrune does not fit the crowded "AI coding agent" shelf (Cursor, Cline,
Aider, Copilot). It is the **operator / mission-control layer on top of** the
agent you already run (Claude Code). If we market it *as* a coding agent we lose
head-to-head; if we market it as **the console that manages many agents across
many projects — from your phone**, we own an under-served category.

**One-line pitch (developer):** *Mission control for your Claude Code agents —
run, schedule, and babysit many of them across every project, from your desk or
your phone.*

~~**One-line pitch (approachable / cloud):** *Your own always-on AI agent, on your
phone, no computer to babysit.*~~ **STRUCK 2026-07-13 — forbidden claim (Cloud tier
is parked; Connect is not always-on).** Replacement: *"Your dev machine, in your
pocket."*

---

## 2. Competitive landscape

| Competitor | What it is | Where Clayrune wins |
|---|---|---|
| **Cursor / Windsurf** | AI IDE, single-repo, desk-bound | Multi-project orchestration; phone control; scheduled + autonomous agents |
| **Cline / Roo** | VS Code agent extension | Not tied to an editor; runs headless, unattended, scheduled |
| **Aider** | CLI pair-programmer | Dashboard + memory + multi-agent + mobile, not a terminal loop |
| **Conductor / Vibe Kanban** | Parallel Claude Code runner (Mac) | Cross-platform, phone-first, scheduler, hivemind, cross-session memory |
| **Devin / Amp** | Autonomous cloud SWE | BYO-key, local-first option, transparent, far cheaper; you own the box |
| **Raw Claude Code** | The engine itself | Clayrune is the fleet console *for* it — complementary, not competing |

**Takeaway:** lead with what nobody else bundles — **many agents, many projects,
one console, reachable from a phone, with scheduling + autonomy + memory.**

---

## 3. Differentiators to hammer (the message pillars)

1. **Fleet, not a single agent** — manage every project's agents from one grid.
2. **Phone control** — dispatch, monitor, restart from anywhere. Nearly unique.
3. **Runs unattended** — scheduler, autonomous steward, hivemind multi-agent.
4. **Remembers** — cross-session memory so agents don't start cold.
5. **Yours** — local-first, bring-your-own-key, transparent; cloud is optional.
6. **Approachable on-ramp** — free start via Gemini key; installer, not a repo.

---

## 4. Target segments (priority order)

1. **Claude Code power users** running many agents/repos — feel the pain today,
   highest conversion. Reach: r/ClaudeAI, Anthropic Discord, X AI-dev circles.
2. **Indie hackers / solo devs** who want phone control + set-and-forget agents.
   Reach: Product Hunt, Hacker News, indie newsletters.
3. **Non-technical BYO-key cloud users** (approachability play) — later, once the
   cloud free-tier on-ramp is friction-free.

---

## 5. Approachability improvements (product asks that unblock marketing)

These are *marketing-blocking product gaps*; each becomes a `decision-needed` or
backlog item when it's the cycle's chosen step. Ordered by leverage:

- **README hero + animated GIF/demo** — the repo is the #1 landing surface; it
  currently opens with a feature list, not a 5-second "what it looks like" hook.
- **60–90s demo video** — the single highest-leverage asset for every channel.
- **One-command / one-click try** — lower the install cliff; a hosted live demo
  or sandbox would remove the biggest approachability barrier.
- **Screenshots gallery** on the landing page (grid, phone control, hivemind).
- **Clear "Clayrune vs. just Claude Code" explainer** — kills the #1 confusion.

---

## 6. Launch channels & sequence

**Phase A — Assets & foundation (pre-launch):**
- Tighten README hero + add demo GIF.
- Record the 60–90s demo video.
- Landing page: screenshots gallery + "vs. Claude Code" section.
- Prepare Show HN post, Product Hunt listing draft, X thread, subreddit posts.

**Phase B — Soft launch (community-first):**
- r/ClaudeAI + Anthropic Discord: authentic "I built this" post with demo.
- X/Twitter thread targeting the Claude Code dev community.
- Collect first testimonials + fix the top friction reported.

**Phase C — Big launch:**
- **Show HN** (Tue–Thu, morning ET) — title emphasizes the fleet/phone angle.
- **Product Hunt** launch (coordinate same/adjacent day) with the demo video.
- Dev newsletter outreach (TLDR, Console.dev, etc.).

**Phase D — Sustain:**
- YouTube walkthrough / "day in the life running 5 agents."
- Comparison blog posts (Clayrune vs Conductor / vs Cursor workflow).
- Ship-log / changelog cadence to keep the repo alive-looking.

---

## 7. Success metrics — ⚠️ REPLACED, see `LAUNCH_PLAN.md` §5

~~GitHub stars, installer downloads, HN/PH ranking, cloud sign-ups.~~ These are
**vanity metrics and are explicitly rejected.** Track instead:

**WAU · D7 / D30 retention · % who enable remote · time-to-first-agent-turn.**

**If D7 retention is bad, marketing is irrelevant — fix the product.**

---

## 8. Immediate next steps (steward backlog)

1. Draft the **README hero rewrite + demo-GIF spec** (reversible, doc-only).
2. Draft the **"Clayrune vs. Claude Code" explainer** copy.
3. Draft the **Show HN + Product Hunt copy** for review.
4. Storyboard the **60–90s demo video**.
5. Baseline current metrics (stars, downloads) so Phase C lift is measurable.

Each is a single future cycle. Anything that publishes externally (posting to
HN/PH/Reddit, pushing site changes live) is **irreversible** → `decision-needed`
with exact copy, never auto-posted.
