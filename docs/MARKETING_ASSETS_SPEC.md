# Clayrune — Marketing Assets Spec

**Owner:** Steward (charter `2375e16b`). **Status:** DRAFT v1, 2026-07-11.
Companion to `docs/MARKETING_CAMPAIGN_PLAN.md` (Phase A — Assets & foundation).

Every channel in the campaign (README, landing page, Show HN, Product Hunt, X,
Reddit) is bottlenecked on the **same two assets**: a short looping demo GIF and
a 60–90s demo video. This doc is the shot list so they can be captured in one
sitting without re-deriving what to film.

**The rule for every asset: show the fleet and the phone.** Those are the two
things no competitor has. A demo that shows one agent doing one task on one repo
markets us straight into the Cursor/Cline comparison we lose.

---

## A1 — Demo GIF (highest priority; blocks the README hero)

- **Where it lands:** `docs/assets/demo.gif` → uncomment the `ASSET TODO` line at
  the top of `README.md`.
- **Length:** 8–12s, silent, seamless loop.
- **Size:** ≤ 5 MB (GitHub renders it inline; above ~10 MB it stalls on load).
  1200–1400px wide, 2× DPI capture downscaled.
- **Shot list (no cuts if possible — one continuous pan beats a montage):**
  1. **(0–3s)** Project grid, several tiles, *more than one agent visibly running*
     — the live activity indicator is the hook. This frame alone must say "fleet."
  2. **(3–7s)** Click a project → agent chat streaming a real reply in real time.
  3. **(7–12s)** Cut to the **phone** showing the same session live. This is the
     "wait, what?" beat — do not cut it for length.
- **Do not show:** API keys, real client/project names, token-cost figures, the
  Cloudflare hostname. Use a clean demo project set.

## A2 — Demo video (60–90s; the Product Hunt / Show HN centerpiece)

Narrated screen capture. Structure — **problem first, product second**:

| Beat | ~Time | Content |
|---|---|---|
| Hook | 0–10s | "You've got Claude Code running in three terminals and no idea which one is stuck." |
| Fleet | 10–30s | The grid. Dispatch a task to project A, another to project B. Both stream. |
| Unattended | 30–50s | Scheduler + a steward with a standing charter — it works while you don't. |
| Phone | 50–70s | Walk away from the desk. Open the phone. Same agents, live. Dispatch from bed. |
| Memory + close | 70–90s | Agent recalls prior sessions. Close on the one-liner + `clayrune.io`. |

- Record at 1080p+. Real UI only — no mockups, no sped-up fakery.
- Ship a captioned cut: most X/Reddit views are muted.

## A3 — Screenshot gallery (landing page + Product Hunt gallery)

Five stills, in this order (the order *is* the argument):

1. Project grid, agents running (the fleet).
2. Agent chat mid-stream (it does real work).
3. Phone control (the differentiator).
4. Scheduler / steward charter (it runs unattended).
5. Hivemind or multi-window (the depth — power users buy this one).

Light theme for the primary set; dark-theme variants are nice-to-have.
Existing mobile stills at `docs/play-store/assets/screenshots/` are a starting
point for #3 but were framed for the Play Store — reshoot to match the set.

## A4 — Social card / OG image

1200×630. The one-liner ("Mission control for your Claude Code agents") over the
project grid. Used by every link unfurl on X, Slack, Discord, HN.

---

## Sequencing

A1 unblocks the README (our #1 landing surface) → do it first. A3 can be pulled
as frames from the A2 recording session, so **record A2 and grab A1 + A3 out of
the same capture**. A4 is a 20-minute derivative of A3 #1.

**Capture is a human step** (needs a real screen + phone + a clean demo project
set); the steward cannot record it. When these exist, Phase A of the campaign
plan is unblocked and the launch copy (Show HN / Product Hunt) becomes the
critical path.
