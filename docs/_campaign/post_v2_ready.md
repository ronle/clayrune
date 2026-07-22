# READY TO POST — give-first teaching post (v2)

## Routing (rules verified 2026-07-22 via API)
- **STEP 1 — test bed: r/AgentsOfAI** (120k). Self-promo allowed *if the body is a
  detailed text description* (Rule 4) — ours is. On-topic (managing multiple agents),
  fresh room, mid-size. This proves the message cheaply.
  **Flair: "I Made This"** (the dominant showcase flair, 41/100 hot posts; correct
  disclosure per Rule 4 — don't dress it as "Resources/Discussion").
- **STEP 2 — flagship: r/ClaudeCode** (360k), only after the message is proven.
  Flair: **"Tutorial / Guide"** (reads far less promo than "Showcase"). Rule 6 needs
  disclosure (what it does / who benefits / cost / your relationship) — the post covers all.
- **Backup:** r/mcp (allowed w/ showcase tag, but MCP-specific angle).
- **RULED OUT:** r/ClaudeAI (already posted — no reposting), r/Anthropic (bans self-promo),
  r/vibecoding (tools need mod pre-approval), r/LLMDevs (no commercial/disguised promo).

**Type:** text/self post · **When:** Tuesday or Wednesday, ~10am PT (1pm ET). Not Sunday, not evening.
**After posting:** post the FIRST COMMENT below (links + clip). r/AgentsOfAI Rule 4
requires links go in the comments, not the body — Reddit's pre-submit checker flags
body links. Body stays link-free; the detailed workflow satisfies the rest of Rule 4.

---

## Title
How I run 5 Claude Code agents across different projects without losing track

## Body

I've been running Claude Code across about 5 projects at once for a few months, and for a while I was drowning in terminal windows. Here's the workflow that actually fixed it — most of it you can copy without any tool.

**1. One context per project, not per task.** I stopped spawning a fresh session for every little thing. Each project gets one long-running context I come back to, instead of starting cold every time.

**2. A memory file per project.** My biggest time sink was agents re-reading the whole repo to work out where we left off. Now each project has a short running notes file — decisions made, what's done, what's next — and I point the agent at it every session. It picks up instead of re-analyzing.

**3. A backlog per project for stray ideas.** Ideas show up faster than I can act on them. A simple per-project list means nothing gets lost and I'm not holding it all in my head.

**4. Check in from your phone.** Honestly, half my "managing" was just wanting to know if an agent was done or stuck. Being able to glance from my phone cut a lot of desk time.

**5. One dashboard instead of N terminals.** The thing that made it click: every project as a tile, so at a glance I can see which agent is working, which is waiting on me, which is idle — instead of alt-tabbing through terminals trying to remember.

One thing worth saying since it always comes up: all of this runs on **your own Claude subscription through the CLI — not the API.** So it costs exactly what Claude Code costs you today. Nothing extra metered, nothing routed through a third party, nothing leaves your machine.

I ended up bundling all of the above into a tool called **Clayrune** — free, MIT, runs on your own machine. There's a live demo you can click through with zero install, a one-command install if you want to actually run it, and the whole thing is open source. Dropping the links in a comment below so this isn't just a link-drop.

Curious how everyone else keeps multiple agents straight — always looking to steal a better system.

---

## FIRST COMMENT (post immediately after — links + clip live here per Rule 4)

Links, as promised:

- Live demo, no install: https://clayrune.io/demo
- Install (one command — PowerShell on Windows, one line on Mac/Linux): https://clayrune.io
- Source, MIT: https://github.com/ronle/clayrune

Also attaching a ~20s clip of it running a few agents at once (clayrune_clip_reddit.mp4) — add it to this comment or a quick follow-up.

---

## Why this should beat v1
- **Give-first:** 5 copyable tips someone can use even without the tool (Rule 6 "give before you take"). Tool is the payoff, not the ask.
- **No gate:** dropped the "Max plan / power users only" filter that disqualified readers up front.
- **Demo, not cliff:** CTA is the zero-install live demo, not an installer that needs Claude Code pre-set-up.
- **Cost objection killed up front:** the "CLI not API, no extra bill" line lands before anyone has to ask.
- **Ends with a question:** invites discussion → comments → ranking.
