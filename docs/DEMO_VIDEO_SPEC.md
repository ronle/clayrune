# The 45-second demo clip — shot spec

**This is the launch gate.** Nothing ships before it exists: not the Show HN post, not
the landing page, not the README. The clip *is* the pitch — the essay distributes,
the clip converts (`docs/LAUNCH_PLAN.md` §3.3).

**Capture is a human step.** Needs a real desktop, a real phone, and a clean demo
project set. The steward cannot record it.

---

## The one thing it must do

Make a Claude Code user say **"wait — it's still going, and that's my actual machine?"**

Everything below serves that beat. If a shot doesn't serve it, cut it.

**Two things no competitor has, and both must be on screen:** *many agents at once*,
and *the phone*. A demo of one agent doing one task in one repo markets us straight
into the Cursor/Cline comparison we lose.

---

## Shot list — 45s, one continuous take if possible

| # | Time | Shot | Why it's there |
|---|---|---|---|
| 1 | **0–6s** | **The grid.** Three or four project tiles. **Two agents visibly running at once** — the live activity indicator moving. No narration yet; let it read. | This single frame says "fleet." It is the whole category argument. |
| 2 | 6–14s | Click into a project. Agent chat **streaming a real reply in real time** — actual tokens landing, actual tool calls. | Proves it's real work, not a mock. |
| 3 | 14–22s | Dispatch a **second** task to a **different** project. Cut back to the grid: **both streaming.** | The thing terminals cannot do. |
| 4 | 22–28s | **Stand up and walk away from the desk.** Agents still running on the (visible) screen behind you. | Sets up the beat. Physical, not a UI transition. |
| 5 | **28–40s** | **THE BEAT.** Pick up the phone. Open it. **Same session, live, mid-stream.** Then **type into it and steer the agent** — and the desktop behind you updates. | This is the clip. Do not cut it for length. Do not speed it up. If you only have budget for one shot to be perfect, it's this one. |
| 6 | 40–45s | Cut to black. One line + `clayrune.io`. | — |

**Shot 5 is the product.** Shots 1–4 exist to make it land. Frame the phone so the
desktop is visibly in the same shot at least once — *one continuous physical space*
is what makes it feel real rather than composited.

---

## Rules

- **Real UI. Real agent. Real network.** No mockups, no sped-up fakery, no cuts that
  hide latency. If it's slow, it's slow — that's more credible than a fake.
- **1080p minimum**, 60fps for the phone pickup (motion).
- **Silent + captioned.** Most X / Reddit views are muted. The clip must work with
  zero audio. Any narration is a bonus track, not the carrier.
- **Never on screen:** API keys · real client or project names · token-cost figures ·
  the Cloudflare hostname · anything resembling a credential.
- **Never implied:** that we run Claude · that the machine can be asleep. If the
  desktop is visible in shot 5 and obviously awake, that honesty is *free* — it does
  the work no caption can.

---

## Derivatives — grab them from the same capture session

Record the 45s once at high quality, then cut everything else out of it. Do not
schedule three separate shoots.

| Asset | Source | Notes |
|---|---|---|
| **README GIF** (8–12s, ≤5 MB, seamless loop) | Shots 1 + 5, cut together | Blocks the README hero. Highest priority derivative. 1200–1400px, 2× downscaled. |
| **Landing-page hero** | Shot 1, still | Above the fold. |
| **Screenshot gallery** (5 stills) | 1) grid · 2) chat mid-stream · 3) phone · 4) scheduler/steward · 5) hivemind | The **order is the argument.** Light theme primary. |
| **OG / social card** (1200×630) | Shot 1 still + one line | Every unfurl on X, Slack, Discord, HN. |
| **The long cut (90s)** | Same session, extended | For the landing page and Product Hunt only. **Not the launch asset.** Add the scheduler/steward beat here — "it works while you don't" (`LAUNCH_PLAN.md` §1.3) — which is the *free tier's* hook and doesn't fit in 45s. |

---

## Definition of done

The clip is done when a Claude Code user who has never heard of Clayrune watches it
**on mute** and can say what the product is. Test it on one before publishing
anything. If they can't, the clip is wrong and the launch is not ready — no amount of
copy fixes it.
