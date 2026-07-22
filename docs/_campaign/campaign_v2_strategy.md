# Clayrune campaign — v1 post-mortem + v2 strategy (E2E)

**Date:** 2026-07-21 · **Owner:** Vector (steward), E2E per Ron's request.

---

## 1. The data (r/ClaudeAI post 1v22u8b, first 25h)

| Funnel stage | Number | Read |
|---|---|---|
| Post impressions | ~1,600 | Reddit showed it around |
| Upvotes | **0** (score 1) | Seen, not endorsed |
| Clickthrough to site | **~0 net** (83 visitors = ~85/day baseline) | The hook didn't pull |
| Reached download page | 4 | |
| Installer downloads | **0** | |
| Installs / stars / forks | 0 / 1 / 0 | No proof accrued |

The collapse is at the **top** of the funnel — impressions → clickthrough. Nobody got far enough to judge the product or the download page. **So the failure is the message, not the machine.**

## 2. Root causes (ranked, most decisive first)

1. **Ask-first, give-never.** The post asked a stranger to volunteer labor ("I need beta testers") with no payoff shown. r/ClaudeAI Rule 6 says it outright: *give before you take.* We took first.
2. **The title filtered instead of hooked.** *"Looking for power users … (Max plan) … I need beta testers"* leads with a qualifier gauntlet + "I need." It disqualifies 95% of readers before any value and creates no curiosity. This is why 1,600 impressions produced ~0 clicks.
3. **We pointed at a cliff, not a demo.** The single friction-killer — the **zero-install live demo at clayrune.io/demo** — was buried. We sent people toward an installer that requires Claude Code + a Max sub already set up. Highest-friction possible first touch.
4. **Wrong room.** r/ClaudeAI skews *builders showing their own tools* (2 of 3 commenters plugged competitors). It's a maker audience, not an adopter audience. The one genuinely interested person (this_for_loona) was an outlier — and was the *opposite* of our stated target (casual, sequential projects, minimal dev exp).
5. **No proof.** 1 star, purpose-made account, zero social proof. "Try my tool" with no evidence others valued it is a cold sell.

## 3. The reframe (what we're actually trying to do)

We optimized for a *launch moment*. At 1 star / 0 installs, that's the wrong goal. The real goal is **the first ~10 real users + proof.** That changes the tactics:

- **Give before asking.** Teaching/showing content where the tool is the payoff, not the request.
- **Zero-friction first touch.** Lead every surface with the **live demo**, never the installer. Remove the install cliff from the first contact entirely.
- **Rooms where adopters live, not just builders.** And, highest-yield: **1:1 with people already posting the pain.**
- **Accrue proof.** Convert those first users into testimonials before any broad push.

## 4. Course of action (phased)

**Phase 0 — approachability (this week, reversible, I own):**
- Make **"try the live demo, no install"** the hero CTA on README + landing + every post.
- Audit the try-it path; lower the cliff (the demo is the answer for cold traffic).

**Phase 1 — give-first content (channel held constant, tests "was it the message?"):**
- The teaching post: *"How I run 5 Claude Code agents across projects without losing my mind"* — real, copyable workflow; Clayrune as the "I bundled this" payoff at the end; CTA = **the demo**.
- Post to r/ClaudeAI. Measure clickthrough to /demo, not vanity views.

**Phase 2 — new channels (tests "was it the room?"):**
- r/selfhosted (your-machine/your-data/MIT angle).
- `awesome-claude-code` PR — durable long-tail, near-zero risk, do regardless.

**Phase 3 — 1:1 (highest conversion, lowest scale):**
- Find people posting "drowning in Claude sessions / managing multiple agents" and genuinely help them; mention the tool only if it fits. This is where the first 10 users actually come from.

**Continuous:** metrics digest each cycle; double down on whatever converts, kill what doesn't.

## 5. E2E operating model

- **I own:** strategy, all copy/assets, channel choice, timing/sequencing, metrics, iteration.
- **You do only the irreducibly-you steps:** click "post" on ready-made drafts from your account; approve any git push/PR. That's it.
- **Reporting:** a short digest per cycle (did / numbers / next). I ping you only when I need a click or a decision — not to narrate.
- **Constraint I can't cross:** I physically can't post (read-only token) or send externally without you; those stay your click. Everything up to that click is mine.

## 6. Decisions I need from Ron (3)
1. **Green-light the pivot?** (give-first + demo-first + broader/adopter audience + 1:1).
2. **May I make reversible README + landing edits** to lead with the live demo, committed on-branch (you push when ready)?
3. **Cadence:** OK that I prep each post as a copy-paste-ready "post this now" (title/body/flair/timing) and you just post? Daily digest, or only-when-needed?
