# Handoff: Friendly Mission Control (non-developer-facing redesign)

## Overview

Mission Control is currently a power-user tool — dark, dense, and full of developer jargon ("Dispatch", "ExitPlanMode", "Guardian", token counters, raw agent logs, terminal paths). This handoff is a **full rethink of the user-facing surface for non-developers** (PMs, designers, ops people) who want to run agents without understanding the machinery.

The design replaces the dev metaphor with something closer to a small team of helpers: each project is owned by a named **Assistant** with an emoji avatar, its status is described in plain English, and the UI surfaces *"Needs you"* items first so users always know what the next action is.

**Goals**
- Make the app legible to someone who has never touched Claude Code.
- Preserve power-user access behind an "Advanced" affordance — don't remove features, just hide them.
- Offer two visual tones (Soft Dark / Warm) that the user can switch between in Settings, plus density and writing-voice controls.

## About the Design Files

The files in this bundle are **design references created in HTML + React-via-Babel** — prototypes showing the intended look and behavior. They are **not production code to copy directly.** Your job is to recreate them in the Mission Control codebase (which is currently a hand-rolled HTML/CSS/JS app in `static/index.html`) using that codebase's established patterns: inline `<style>` blocks with CSS custom properties, vanilla JS + DOM manipulation, no framework.

If you'd prefer to introduce a lightweight framework (Preact, Lit, Alpine) for this surface, that's a reasonable judgment call — the existing code is approaching the size where that would help. Check with the repo owner first.

## Fidelity

**High-fidelity.** Colors, typography, spacing, border radii, interaction states, and copywriting are all intentional and final. Hex values, font sizes, and tokens are in `theme.css` and should be lifted exactly. Recreate pixel-perfectly; do not restyle.

The prototype uses in-browser Babel + React for demo speed — that's a prototyping convenience, not a recommendation for production.

## Screens / Views

### 1. Home — Cards layout (default)

- **Purpose:** The user's dashboard. Each project is a card showing the assistant, its current status in plain English, progress, and a primary CTA ("Answer questions", "Peek at progress", etc.).
- **Layout:**
  - Sidebar 230px fixed on the left. Main content area uses `grid-template-columns: 230px minmax(0, 1fr)` with `min-width: 0` on `.fc-main` (critical — without it, cards overflow).
  - Main area max-width 1120px, padding `34px 40px 80px`, `box-sizing: border-box`.
  - Header row: greeting + eyebrow on left, ghost "Team" + primary "+ New project" buttons on right. `flex-wrap: wrap` so it doesn't overflow on narrow widths.
  - Card grid: `grid-template-columns: repeat(auto-fill, minmax(280px, 1fr))`, gap 18px.
  - Cards sort order: `asking` and `stuck` first, then `working`, then `idle`/`done`. This is deliberate — the first thing the user sees is what needs their attention.
- **Card components (per project):**
  - Avatar: 44×44 rounded square, emoji or illustration placeholder.
  - Title: assistant name (e.g. "Party Planner") in 15px/700. Project name (e.g. "Birthday party planner") in 13px dim below.
  - Status chip (top-right): dot + label. `working` = green pulsing; `asking` = amber; `stuck` = red; `done` = green static; `idle` = grey.
  - Summary line (15px): one-sentence plain-English description of what the assistant is doing or waiting for. Pulled from `data.summary[voice]`.
  - Progress bar (when `steps > 0`): 6px rail, accent fill.
  - Meta row: current task · last-action relative time.
  - Primary CTA button: "primary" variant if status is `asking` or `stuck`, "ghost" variant otherwise.
- **Card accent strip:** `asking` cards get an amber top-border (and hard drop-shadow in Warm tone); `stuck` cards get red.

### 2. Home — Chat layout (optional secondary)

- **Purpose:** Alternative entry point. User talks to an Orchestrator; it fans out work to assistants shown inline as "work cards" within the conversation.
- **Layout:** Centered column, max-width 780px, no sidebar.
- **Hero:** H1 "What would you like to get done?" (casual) or "What do you need?" (pro).
- **Suggestion grid:** 2×2 grid of prompt suggestions (emoji + title + subtitle). Click fills the composer.
- **Message list:** User bubbles right-aligned in accent color; Orchestrator bubbles left-aligned in surface with border.
- **Work cards:** Inline within Orchestrator messages — `status chip + assistant name + note`. Shows work being done behind the conversation.
- **Composer:** Pill-shaped, sticky bottom, 42px circular send button with accent background.

### 3. Home — Today list layout (optional tertiary)

- **Purpose:** A calm alternative to cards — grouped to-do list.
- **Groups (in order):** "Needs you" → "Working on it" → "Done today" → "Resting".
- **Each row:** 44px avatar, name + status chip, summary line, last-action + CTA button on the right.
- **List container:** single panel with 1px dividers between rows.

### 4. Settings page

- **Purpose:** Where users personalize the app. Accessed from sidebar `⚙ Settings`.
- **Sections:**
  1. **Appearance**
     - Theme: segmented control "Soft dark / Warm".
     - Accent color: 5 pill-style swatches (Sunset, Rose, Lilac, Lagoon, Ink).
     - Density: segmented control "Cozy / Compact".
  2. **Voice**
     - Writing style: segmented control "Casual / Professional".
     - Live preview block showing how one assistant's summary reads in the selected voice.
  3. **Help & tour**
     - "Replay the welcome tour" button.
- **Settings panel styling:** Surface background, bordered, rows separated by 1px dividers, `grid-template-columns: 1fr auto` with 20px gap.

### 5. Onboarding tour overlay

- **Purpose:** First-run walkthrough. 4 steps, each a modal card with optional spotlight on a real element.
- **Steps:**
  1. "Meet your team" — general intro, no spotlight.
  2. "This is a Project Card" — spotlight on the first card.
  3. "Tap the button to help out" — spotlight on the CTA button of the first card.
  4. "Start with a sample" — no spotlight, closes tour.
- **Backdrop:** `rgba(10, 12, 20, 0.55)` + 2px blur.
- **Spotlight:** positioned absolute over target's `getBoundingClientRect()` with 8px padding, accent-colored 4px ring, giant `box-shadow: 0 0 0 9999px rgba(...)` for the dim effect.
- **Card:** 440px max width, progress dots, "Skip tour" button on left, "Next"/"Got it" primary on right.
- **First-run trigger:** check `localStorage` for a `mc.tourSeen` flag — show if absent, then set it.

## Interactions & Behavior

### Status taxonomy

Five states drive all status visuals in the app. Never surface raw dev states — always map to these.

| Internal state | User-facing (casual) | User-facing (professional) | Color | Pulse? |
|---|---|---|---|---|
| Agent running | Working on it | In progress | green | yes |
| Needs user input / plan approval | Needs you | Awaiting input | amber | no |
| Error / blocked | Stuck | Blocked | red | no |
| Completed | All done | Completed | green | no |
| No active task | Resting | Idle | grey | no |

### Writing-voice toggle

Every piece of copy that can vary exists in two forms — **Casual** (default) and **Professional**. Examples:

| Context | Casual | Professional |
|---|---|---|
| Greeting | "Good afternoon, Jess" | "Overview" |
| Nav item | "My team" | "Team" |
| New button | "+ New project" | "+ New project" |
| Assistant summary (party planner) | "I need a couple of quick answers before I book the venue." | "Awaiting input: venue selection requires 2 decisions." |
| Assistant summary (market) | "I'm reading today's earnings reports. Almost done." | "Parsing Q1 earnings releases. ETA 3 min." |
| Card CTA (asking) | "Answer questions" | "Respond" |

The content map lives in `data.js` under `FC_ASSISTANTS[].summary` and `FC_ASSISTANTS[].cta` — each has `{ casual, pro }` variants. Recreate this as a translation-style dictionary in production.

### Hidden by default

These power-user concepts should not appear in the default (non-advanced) UI:
- Tokens / cost counters
- Raw agent logs / streaming output
- Plan-approval (ExitPlanMode) mechanics — present as "Needs you" instead
- Model picker / settings
- GitHub issues sync controls
- Shared rules / `MEMORY.md` files
- Terminal paths (`~/code/…`)

Reveal them via an "Advanced" toggle — exact UI for that is TBD and not in this handoff.

### Animations & transitions

- Card hover: `transform: translateY(-2px); box-shadow: 0 10px 30px rgba(0,0,0,0.35); border-color: var(--line-strong);` over 0.15s ease.
- Status chip `working` pulses: dot `transform: scale(1)` ↔ `scale(1.2)` opacity 1 ↔ 0.7 on 2s infinite.
- Progress bar fill: `width` transitions 0.3s ease.
- Button hover: `translateY(-1px)` over 0.1s.
- Tour spotlight: `transition: all 0.25s ease` on position/size so it smoothly moves between steps.
- No bounces, no gradients in motion — the aesthetic is calm.

## State Management

```
AppState {
  settings: {
    tone: 'tone-dark' | 'tone-warm'            // persisted
    accent: '#e8824a' | '#d96480' | ...        // persisted
    density: 'density-cozy' | 'density-compact' // persisted
    voice: 'casual' | 'pro'                    // persisted
  }
  ui: {
    activeNav: 'home' | 'team' | 'calendar' | 'inbox' | 'settings'
    tourOpen: boolean                          // true on first run
  }
  data: {
    assistants: Assistant[]  // fetched from API
  }
}

Assistant {
  id, emoji, name, project,
  status: 'working' | 'asking' | 'stuck' | 'done' | 'idle',
  summary: { casual: string, pro: string },
  cta:     { casual: string, pro: string },
  currentTask: string,
  lastAction: string,  // relative time, formatted server-side for now
  steps: number, steps_done: number,
}
```

Persist `settings` in `localStorage` under `mc.friendly.settings`. `tourOpen` is controlled by the `mc.friendly.tourSeen` flag.

## Design Tokens

All tokens live in `theme.css`. **Lift them exactly.**

### Core palette (both tones)
- `--accent`: user-selectable. Defaults: `#e8824a` (Sunset), `#d96480` (Rose), `#8a7ce0` (Lilac), `#4fa89a` (Lagoon), `#2b2f3a` (Ink).
- `--accent-ink`: contrast-computed — white on dark accents, `#1a1a1a` on light.

### Soft dark tone (`.tone-dark`)
- `--bg: #11131a`, `--bg2: #171a23`
- `--surface: #1c202c`, `--surface2: #232836`
- `--ink: #f2f4f9`, `--ink-dim: #a5adc2`, `--ink-faint: #6c7389`
- `--line: #2a2f3f`, `--line-strong: #3a4157`
- Status: `--green: #7edcb4`, `--amber: #f5c76d`, `--red: #f89191`
- Status backgrounds: semi-transparent versions of the above at 14–16% opacity.
- Shadow: `0 10px 30px rgba(0,0,0,0.35)` (cards), `0 2px 10px rgba(0,0,0,0.25)` (soft).
- Subtle radial gradient at top-right using accent at 8% opacity.

### Warm tone (`.tone-warm`)
- `--bg: #f6f0e4`, `--bg2: #efe7d6`
- `--surface: #ffffff`, `--surface2: #fbf7ee`
- `--ink: #201c16`, `--ink-dim: #5a5247`, `--ink-faint: #8a8172`
- `--line: #e8e0cd`, `--line-strong: #c9bea6`
- Status: `--green: #2f8a5b`, `--amber: #c77a1a`, `--red: #c23a3a`
- Status backgrounds: `#dff2e6`, `#fbe6c7`, `#f8dcdc`.
- Shadow: `0 8px 0 rgba(32, 28, 22, 0.08)` — **hard-shadow style, no blur.**
- Cards get `1.5px` borders instead of `1px`. Buttons get `1.5px` ink-color border and a hard `0 4px 0` shadow that compresses on `:active` (`translateY(2px); 0 2px 0`). This "pressed button" feel is important — keep it.

### Typography
- **Soft dark:** body + display → `Inter` (weights 400/500/600/700).
- **Warm:** body → `Inter`; display → `Nunito` (weights 700/800). Nunito's rounder terminals are what make Warm feel friendly.
- Letter-spacing on displays: `-0.01em`.

### Radii
- `--radius-lg: 20px` (cards in cozy density) / `14px` (compact)
- `--radius-md: 14px` / `10px`
- `--radius-sm: 10px` / — unchanged

### Spacing (cozy → compact)
- `--pad-card: 22px → 16px`
- `--gap-cards: 18px → 12px`
- `.fc-main` padding: `34px 40px 80px → 24px 28px 60px`

## Assets

- **No bundled assets.** Emoji avatars are Unicode — replace with your own illustrated assistant portraits when time allows. Line-art style would match Warm tone; geometric would match Soft dark.
- Fonts load from Google Fonts via `@import` in `theme.css`. Self-host for production (CSP + perf).
- No icons are bundled — the sidebar uses Unicode glyphs (🏠 👥 📅 📬 ⚙️). Replace with your preferred icon set (Lucide, Phosphor) — stroke icons at 20px work.

## Files

Reference files (under `design_handoff_friendly_mission_control/source/`):

- `index.html` — app shell + root component + `localStorage`/edit-mode wiring. Ignore the `EDITMODE-BEGIN/END` markers; they're a prototype convenience.
- `theme.css` — all tokens, both tones, densities, and component styles. **This is the main reference.** Line-by-line re-implementation is the expected workflow.
- `data.js` — mock assistants, today-list groups, chat seed, suggestions, onboarding steps.
- `Primitives.jsx` — `FcChip`, `Avatar`, `Progress`, `Sidebar`, `GreetingHeader`, `statusLabel()` helper. The statusLabel dictionary is the canonical voice translation table.
- `CardHome.jsx` — Cards layout (the default Home).
- `ChatHome.jsx` — Chat layout.
- `TodayList.jsx` — Today list layout.
- `Settings.jsx` — Settings page.
- `Onboarding.jsx` — Tour overlay + the design-time tweaks panel (the tweaks panel should NOT ship to production; it's a preview harness).

Supporting reference (from the parent design system):

- `../../colors_and_type.css` — the existing Mission Control design system this redesign diverges from. Don't copy; reference only.
- `../../README.md` — design system root doc.
