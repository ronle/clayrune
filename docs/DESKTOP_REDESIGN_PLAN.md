# Clayrune Desktop Redesign — Plan

**What this is.** The plan for bringing the "Simplified Dashboard" design language
to the **desktop** app (>960px), after the mobile redesign shipped. Design
reference: `C:\Users\levir\Documents\_claude\Simplified Dashboard.pdf` — frames
**5f** (desktop conversation view) and the two whole-app directions at the bottom
of the board (**"Calm — keep the sidebar, tier it"** and **"Focus — one column,
palette-driven"**). Board crops rendered to `_scratch/redesign_pdf/` (gitignored).

**Companion:** `docs/CONVERSATION_REDESIGN_ACTION_PLAN.md` (the mobile effort this
builds on — same tokens, same "restyle/relocate, don't invent" discipline).

## Locked decisions (2026-07-10)

| # | Decision | Ron's pick |
|---|----------|-----------|
| 1 | Direction | **Calm (tiered sidebar) first**, keep it simple. Focus (⌘K-only) is a later optional mode, not now. |
| 2 | Desktop Inbox | **Same as mobile** — both the "Waiting on you" section on the dashboard AND the Inbox *timeline* as its own surface. |
| 3 | History consolidation | **Yes** — merge Agent Log + Schedule Runs + Hivemind Runs into one "History" surface. |
| 4 | Surfaces: modals vs pages | **User toggle** — a preference to open surfaces as full in-page **Pages** or floating **Windows** (current modals). Ship both, user chooses. |

Firm from the parent effort: **read theme tokens, never hex**; all three tones
(default dark / warm / editorial); the changes are **restyle + relocate +
consolidate**, mapping every element to an existing Clayrune feature — nothing new
invented.

## Target IA — the "Calm" desktop

**Sidebar (slimmed + tiered):**
```
Clayrune
  Dashboard
  Inbox              ← NEW entry: the notification timeline (same as mobile)
  ＋ New Project
  WORKSPACE
    Backlog
    Automation       ← renamed from "Scheduler"
    History          ← NEW: merges Agent Log + Schedule Runs + Hivemind Runs
  ADVANCED  (dimmed — present, not competing)
    Hivemind
    Skills & MCP     ← merged (was two entries)
    Shared Rules
    Incognito
  Settings  (bottom)  ← absorbs nothing yet; Shared Rules stays its own for now
```
From **11 flat entries → 9, grouped**, with the power surfaces visually demoted.

**Dashboard main area:**
- Header: title · **Search (⌘K)** · live-agent count · ● LIVE.
- **"Waiting on you [n]"** inbox block at top — blocking/actionable only, each row
  with an **Answer** (question) / **Review** (plan) / **Unblock** (stuck) button
  that deep-links into that chat. This is the desktop twin of the mobile section
  (shared source: `_buildAttentionList`).
- **Your projects** — the existing grid, with the Grid/List toggle retained.
- The old **top-strip (Schedule banner + Beacon bar)** and the **right-hand feed
  column** are **removed** — their signal moves into Waiting-on-you (attention) +
  Inbox (timeline) + History (runs).

**Conversation view (5f):** opening a project = three panes — left rail (that
project's recents: New conversation, search chats, recent list w/ status), main
thread (bubbles, plan card, quick-reply chips), quiet header (‹ · model · ↻
resume · ⋮), one composer at the bottom. This is the desktop form of the mobile
Layer-2/Layer-3 model.

## Surface-by-surface change map

| Today (desktop) | Becomes |
|---|---|
| Beacon bar + report modal | **removed** → folded into Inbox + Waiting-on-you |
| Right-hand activity feed column | **removed** → Inbox timeline |
| Skills (sidebar) + MCP (sidebar) | **Skills & MCP** — one surface, two tabs |
| Scheduler (sidebar) | **Automation** (rename; same surface) |
| Agent Log + Schedule Runs + Hivemind Runs | **History** — one surface, filterable by source |
| Schedule banner (top strip) | **removed** → next-run info lives in Automation |
| Hivemind, Shared Rules, Incognito, Power | keep, but Hivemind/Shared Rules/Incognito move under **ADVANCED**; Power → Settings/system menu |
| Floating modals for every surface | governed by the **Pages/Windows** toggle (decision 4) |

## Decision 4 — the Pages/Windows toggle

A single appearance preference, e.g. `surface_mode: 'pages' | 'windows'`
(Settings → Appearance):
- **Windows** (today's behavior): nav opens the surface as a floating,
  draggable/zoomable modal (`openModals`, unchanged).
- **Pages**: nav swaps the **main content area** to a full-width in-page view
  (dashboard-style), sidebar stays; back = Dashboard. One surface visible at a
  time.

Both render from the *same* surface builders — the toggle only changes the
container (modal window vs. main-area swap). Keep the builders container-agnostic
so this stays a thin routing layer, not a fork.

## Open questions to resolve during build

- **Inbox vs. History overlap** — Inbox = friendly "what pushed, click to open the
  chat"; History = detailed run records (per schedule/hivemind/agent). They're
  adjacent. Watch for redundancy; if it bites, History becomes a *filter/tab*
  inside Inbox rather than a separate entry. Start separate (matches PDF + mobile).
- **Focus mode (direction ②)** — deferred. Revisit as an optional `nav_mode:
  'sidebar' | 'palette'` once Calm is solid and the ⌘K palette covers every view.
- **Settings absorption** — the mobile pass moved Settings to own its config; on
  desktop, decide later whether Shared Rules / Processes / Power formally nest
  under Settings or stay as (Advanced) entries.

## Recommended build order

1. **Slim + tier the sidebar** — regroup into WORKSPACE / ADVANCED, rename
   Scheduler→Automation, merge the Skills/MCP entries into one nav item.
   (Low-risk relocation; the surfaces themselves are untouched.)
2. **Dashboard "Waiting on you" block** — desktop render of `_buildAttentionList`
   with Answer/Review/Unblock; remove the Beacon bar + Schedule banner top-strip.
3. **Inbox surface (desktop)** — the notification timeline (reuse
   `/api/notifications` + the mobile Inbox UI, desktop layout); remove the feed
   column.
4. **Skills & MCP merge** — one surface, two tabs (reuse both existing panels).
5. **History merge** — Agent Log + Schedule Runs + Hivemind Runs into one
   filterable surface.
6. **Pages/Windows toggle** — the container router + the Appearance setting.
7. **Conversation view (5f)** — desktop three-pane (per-project recents rail +
   thread). This is the deepest surgery (relaxes the one-modal-tab invariant);
   its own spike, do last / iterate live in a browser.
8. **Focus mode** — optional, later.

Each step is its own commit; iterate the visual ones live in a real browser
(desktop pixels are not verifiable headless).
