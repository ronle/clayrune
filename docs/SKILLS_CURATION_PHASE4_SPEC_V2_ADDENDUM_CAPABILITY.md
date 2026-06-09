# Phase 4 v2 — Addendum: the CAPABILITY artifact (capture features, not just bugfixes)

**Status:** DRAFT v1 (2026-06-04) — companion to
`SKILLS_CURATION_PHASE4_SPEC_V2.md` (DRAFT v2.1, RATIFIED-WITH-CONDITIONS
2026-05-27). This addendum is **pre-committee**: no backend code lands until
it clears the same four-seat pass as the parent (pattern-integrity /
agent-behavior / concurrency / config-ops). New decisions are numbered
**D14+**, continuing the parent's D-series (which closed at D13).

---

## A0. Motivation (the precipitating miss)

On 2026-06-04, showing an iOS Simulator screenshot in the Clayrune chat took
~3 detours (describe in prose → publish to a `/static/` URL → markdown
`![]()` → finally the real mechanism) before landing on the one-line answer:
**output the image's absolute path; `formatAgentText` inlines it via
`/api/serve-image`.** The mechanism was fully present in our own source the
whole time.

Root-cause review found this was **not** an isolated lapse but a structural
gap in the learning layer: **the pipeline captures changes and problems, never
durable capability knowledge.** The image fact is the canonical example of the
missing category.

## A1. The gap, precisely (grounded in the code)

Three places encode "what to capture." All three are delta/problem-centric:

1. **Scribe** (`server.py` `_scribe_summarize_text`) → one ≤300-char **outcome
   line** ("what got done/fixed/decided"). A delta summary by construction.
2. **Distiller extraction** (`distiller.py` `_extraction_prompt`, parent §4.1) →
   three signal classes, none of which a capability fits:
   - `topics` — closed vocab; stated ceiling is *"a subsystem invariant, a
     recurring workflow, a **gotcha class**, a **diagnostic procedure**"* (all
     problem-shaped).
   - `explorations` — gated to *"substantive **external** research
     (WebSearch / WebFetch / … / alternatives comparison)."* Reading our **own**
     renderer to learn a mechanism is the wrong shape: not external, not
     alternatives.
   - `preferences` — what the user wants.
   - The closed vocabulary (`distiller.py` `NOUNS`) has no noun for it —
     `marker`, `render`, `endpoint` exist, but nothing meaning *"a thing you
     can use."*
3. **mc-distill** (in-session proposer) → bar is a *"hard-won novel insight,"*
   every example debug-derived. A capability you simply **looked up** never
   reads as "hard-won," so it never trips the proposer. *(Softened 2026-06-04
   as Tier 1 — see §A4.)*

It generalizes: the same gap drops "`/api/serve-image`'s allowlist is
repo-root + uploads + project-paths," "`simctl` can screenshot headless,"
"Capacitor 8 uses SPM, not CocoaPods" — all durable, reusable, none fitting
`topics`/`explorations`/`preferences`.

## A2. Proposed change — a fifth artifact kind: `capability`

Add `capability` to the four-artifact model (parent §3). It is **single-shot
retention** (no recurrence gate) for the same reason `exploration` is (parent
§3.1 rationale): *a capability fact has retention value the moment it is
recorded; recurrence-gating it discards the experience until it happens 3
times, which defeats the point.*

**Key economy — it rides PREFERENCE's promotion path, not EXPLORATION's.**
`preference` promotes to a memory file that feeds the **existing** memory
read-floor automatically (parent §3.2). `capability` does the same: it promotes
to a **`reference`-type memory file** (the curated-memory taxonomy already has
this type). So — unlike `exploration`, which needed a new `~/.claude/
explorations/` dir + a new read-floor injection — `capability` needs **no new
read-floor mechanism**. That read-floor hop is the part that actually prevents
a repeat of the precipitating miss: the next session's agent gets the
capability injected at dispatch.

> **D14 (open):** new `capability` kind (recommended) **vs** widening
> `exploration` to "external research OR internal capability discovery."
> Recommendation: new kind. Widening muddies `exploration`'s clean external-
> research identity, and `exploration` promotes to its own dir while a
> capability is a reference fact that belongs in memory (reusing the existing
> read-floor). A distinct kind also maps 1:1 to the existing `reference`
> memory type.

### A2.1 Delta to §3.1 (artifact-kinds table) — new row

| Kind | Trigger | Recurrence gate | Body shape | Promotion target |
|---|---|---|---|---|
| `capability` | Agent learned how an existing capability/subsystem/tool/API works in a reusable way (incl. by reading our own source), AND it's non-obvious or contradicted a reasonable assumption | **No** — single-shot retention (same rationale as `exploration`) | CAPABILITY.md (what it does / how to use it — a procedure / where it lives / gotchas) | **`reference`-type memory file** → existing memory read-floor (cross-project) OR project-local `CLAUDE.md`/memory (project-specific) |

### A2.2 Delta to §4.1 (extraction) — new `capabilities` signal class

Add to the `signals` object, K-capped like the others
(`distiller_max_capabilities_per_session`, per-project, default 3; over-cap →
`distiller_class_cap_dropped:capabilities`):

```python
"capabilities": [
  {
    "fingerprint": "<hash>",          # dedupe only, not recurrence (like exploration)
    "phrase": "<verb-noun-modifier>", # closed vocab, §5.x
    "mechanism": "<one-line: how it works / how to use it>",
    "where": "<file / endpoint / marker / command that implements it>",
    "gotcha": "<optional: the misleading assumption it overturns>",
    "evidence_quote": "<verbatim transcript substring>"
  }, ...
]
```

**Extraction-prompt gate (mirrors the `explorations` gate's strictness):**
*"Only emit a capability when the agent learned, in a reusable way, how an
existing capability/subsystem/tool/API works — especially by reading our own
source — AND it is non-obvious or contradicted a reasonable assumption. A
capability the agent merely USED without learning anything non-obvious is NOT a
capability signal. If none, return an empty list."* This keeps the floor high
(no "the agent called Read" noise) and is the inverse framing of mc-distill
(extraction asks "what was learned here," the aggregator does not judge —
single-shot, so it promotes directly).

### A2.3 Delta to §5.1 (closed vocabulary)

Verbs already present cover the action (`document`, `interpret`, `trace`,
`expose`, `diagnose`). Add **nouns** `capability`, `mechanism`. *(Optional verb
`discover`; `document`/`interpret` may suffice — see D16.)* Per the parent's
D1-closure discipline, additions are justified from real corpus usage; the
2026-06-04 session is the seed example.

### A2.4 Reused unchanged

Kill-switch gate (§4.6), concurrency model (§4.8, daemon-thread off
`_write_session_memory`), atomic writes (§4.9), `_proposed/` staging + human
promotion, suppression/`record-push` (§4.7). `capability` adds **no new
entry point and no new lock** — it is one more class in the existing
session-end extraction.

## A3. Why this serves the locked definition (§0)

> *"Learning is when the agent's effective behavior changes over time, driven
> by experience, without the human having to type the change."*

A discovered capability → recorded → injected at next dispatch → the next
agent uses it without the human re-typing it. The definition's targets
explicitly include *"the codebase … and the agent itself"*; its experience
sources explicitly include the agent's *"own past sessions."* Learning how our
own subsystem works, from a past session, is squarely in scope — and is the one
target the current three signal classes structurally cannot capture.

## A4. Tier 1 (already shipped 2026-06-04, no committee needed)

Behavioral fixes that address the precipitating cause directly (skill/doc
edits, not learning-system backend — reversible, outside the §4 gate):

- `CLAUDE.md` → "Showing the user an image in chat" section (the specific fact).
- `data/skills/builtin/mc-distill/SKILL.md` → description broadened to
  "hard-won insight **or a newly-discovered capability**"; new bar note
  *"Capability discoveries count too — memorize features, not just bugfixes,"*
  with the image example. Propagates to `~/.claude/skills/mc-distill/` via
  `_install_builtin_skills()` checksum drift **on next MC restart**
  (operator-approved; not yet restarted).
- Memory: `reference_show_image_in_chat.md` + MEMORY.md pointer.

## A5. Open decisions for committee

- **D14** — new `capability` kind vs widen `exploration`. *(Rec: new kind — §A2.)*
- **D15** — promotion target: `reference`-type memory file reusing the existing
  read-floor (rec) vs a new `~/.claude/references/` dir mirroring `exploration`.
- **D16** — vocab: add nouns `capability`+`mechanism` only, or also verb
  `discover`? (Risk: verb bloat vs. expressivity.)
- **D17** — extraction false-positive risk: does the "non-obvious /
  contradicted-an-assumption" gate hold, or will the model over-emit "used a
  tool" as "learned a capability"? Needs the same hand-curated few-shot set as
  §4.1 (parent Seat 1 out-of-scope flag) before turning on at scale.
- **D18** — K-cap default: 3 (matches other classes) vs lower (capabilities may
  be rarer/higher-value; a 1–2 cap forces sharper selection).

## A6. Committee brief (four seats, mirroring the parent)

- **Seat 1 — pattern integrity:** Is `capability` distinct enough from
  `exploration`/`topic` to avoid double-emission of the same signal across
  classes? Does the closed-vocab noun addition stay closed (D16)?
- **Seat 2 — agent behavior:** Does the extraction gate (§A2.2) actually
  discriminate "learned something non-obvious" from "used a tool"? Will
  read-floor injection of capabilities help or crowd the dispatch context?
- **Seat 3 — concurrency:** Confirm `capability` adds no lock/entry-point
  surface (§A2.4) and that the added per-class render call respects the
  per-project leaf-lock cost noted in parent §4.1 (the EXPLORATION K-cap
  oversight).
- **Seat 4 — config/ops:** New keys (`distiller_max_capabilities_per_session`,
  `distiller_class_cap_dropped:capabilities`); DATA_DIR-pollution check on any
  new sidecar (none planned — rides `_skill_stats.json`); kill-switch coverage.
