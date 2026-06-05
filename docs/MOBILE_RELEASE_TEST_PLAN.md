# Mobile (Android) Chat-Switching — Release Test Plan

**Surface:** Clayrune Android app (Capacitor WebView) + the SPA's agent-chat.
**Date:** 2026-06-04
**Purpose:** Catch the class of mobile bug where **agent or user text fails to
appear in a chat even though the agent made progress** — specifically when
moving in and out of inactive chats and between them. Run before shipping an
APK or a change that touches agent-chat SSE / buffering / rendering.

---

## What this catches (and the bugs it already caught)

When a conversation is **inactive** (you switched to another chat, or
backgrounded the app), its live SSE stream can be dropped — by `turn_complete`,
the Chromium 6-connections-per-origin cap, or Android Doze parking the socket.
Two distinct failures then make the agent's output silently vanish:

1. **Transport gap (switch away → back).** `switchAgentTab` only re-rendered
   from the local buffer; it never re-fetched, so a chat that missed lines while
   inactive stayed permanently incomplete until a hard refresh.
   *Fix:* `switchAgentTab` now calls `fetchAgentStatus()` on switch-back, which
   refills the buffer from server truth, repaints, and reconnects the stream.
2. **Render gap (background → foreground).** `fetchAgentStatus` updated the
   buffer wholesale but `refreshModal` **preserves** the `agent-output` DOM node
   (for scroll/perf) and never repaints it from the grown buffer — so recovered
   lines sat in the buffer, invisible.
   *Fix:* `fetchAgentStatus` now repaints the panel from the buffer when it grows
   for an inactive/parked session (`_repaintAgentOutput`), gated on growth + no
   live SSE so the active streaming chat never flickers.

Both fixes are in `static/index.html`. A third issue — the two recovery paths
**double-rendering** the recovered lines — was caught by the harness's
no-duplication oracle and fixed by making switch-back recover via the
*idempotent* `fetchAgentStatus` repaint rather than an appending reconcile.

---

## How it works

The harness drives the **real Capacitor WebView on an emulator** over the
Chrome DevTools Protocol (CDP via `adb forward` to the WebView's debug socket),
so it exercises the actual native shell, not a desktop browser. It reads in-page
state (`agentOutputBuffers`, `agentServerLines`, the rendered DOM) and compares
against the **server's authoritative `log_lines`** (`GET /agent/status`).

**Oracle (per scenario), measured after returning to a chat:**
- **transport:** `agentServerLines[sid] >= server log_lines.length` — client got every line.
- **render:** DOM present AND `domCount >= agentServerLines[sid]` — every block painted.
- **no-duplication:** `domCount <= expectedDom` — recovered lines appear once, not doubled.

The streaming source is a real (cheap, auto-routed-to-Haiku) "count 1..12"
dispatch into a throwaway scratch project; the oracle is content-agnostic
(DOM-vs-server), so nondeterministic output is fine.

---

## Prerequisites / one-time setup

- [ ] **MC running** on `http://localhost:5199` (the emulator reaches it at `http://10.0.2.2:5199`).
- [ ] **Android SDK** with the emulator + an `android-34 google_apis x86_64` system image, and a JDK. On this machine: `ANDROID_HOME=E:\Android`, `JAVA_HOME=E:\JDK\jdk-21`, WHPX acceleration available. First-time SDK install:
  ```
  $sdk = "E:\Android\cmdline-tools\latest\bin\sdkmanager.bat"
  & $sdk --licenses
  & $sdk "emulator" "system-images;android-34;google_apis;x86_64" "platforms;android-34"
  ```
- [ ] **Boot the emulator** (creates the AVD if missing):
  ```
  pwsh tools/mobile-test/boot-emulator.ps1
  ```
- [ ] **Install the hermetic dev APK** (builds a debug APK that loads the SPA from `10.0.2.2:5199`, no Cloudflare/credential gate, then reverts the mobile-repo edits):
  ```
  pwsh tools/mobile-test/build-dev-apk.ps1
  ```
  > The dev APK is **debug-only** — it bypasses auth and must never be shipped. The shipping APK is unaffected; these edits are applied transiently and reverted with `git checkout`.
- [ ] **Install harness deps:** `npm install --prefix tools/mobile-test`

---

## Run

```
node tools/mobile-test/run.js
```

The harness reloads the WebView first (picks up the latest `static/index.html`),
auto-creates its scratch project, then runs all scenarios. Exit code `0` =
all pass. Useful env vars: `MC_ONLY=S2` (run one scenario), `MC_NORELOAD=1`
(skip the reload), `MC_TEST_PROJECT=<id>`.

Smoke-test the CDP path alone: `node tools/mobile-test/probe.js`.

---

## Scenarios

| ID | Scenario | Simulates | Asserts |
|----|----------|-----------|---------|
| **S1** | Dispatch, switch away to a new chat (SSE dropped), let it finish, switch back | Leaving a responding chat, returning later | Switch-back recovers the missed lines, rendered once |
| **S2** | Dispatch, background app (HOME) + drop SSE, let it finish, foreground | Android Doze parking the socket while backgrounded | Foreground-restore reconciles **and repaints** the recovered lines |
| **S3** | Dispatch and watch to completion, no navigation | The normal happy path | Output renders exactly once — no duplication, no missing (regression guard) |
| **S4** | Dispatch A, dispatch B (A goes inactive), finish both, switch A↔B | "Moving between" two live chats | Each chat shows its **own** complete output on return |

All four must pass. A failure prints the buffer/DOM/server numbers for the
failing transition.

---

## Manual exploratory checklist (not automated)

Do a short manual pass on a **real device or the shipping APK** before a release —
the harness uses a hermetic dev build over native transport, so it does **not**
exercise the Cloudflare path or the custom `EventSource`/native-POST bridge:

- [ ] Real phone, real `ronl.clayrune.io` tunnel: start a long agent turn, lock the phone ~30s, unlock → all output present, status correct.
- [ ] Switch between 3+ conversations rapidly while two are streaming → no missing/duplicated/cross-pasted text.
- [ ] Send a follow-up to a chat that was backgrounded mid-turn → it lands and the reply renders.
- [ ] Minimise the modal mid-stream, restore → output continues, scroll pinned to bottom.

> **Follow-up (not yet covered):** port S1/S2 to the real CF + CES-polyfill path
> (needs a paired device / service token) so the custom `EventSource` polyfill in
> `MainActivity.injectFetchOverride` is exercised by an automated run.

---

## Cleanup

The scratch project (`mobiletest`, empty dir under `%TEMP%\mc-mobiletest`) is a
harmless reusable fixture — leave it for re-runs, or delete it from the project
list. The emulator can be left booted or shut down with
`adb -s emulator-5554 emu kill`.

---

## Files

- `tools/mobile-test/run.js` — scenario runner + oracle.
- `tools/mobile-test/lib/{adb,cdp,app}.js` — adb/CDP/SPA-driving helpers.
- `tools/mobile-test/probe.js` — CDP connectivity smoke test.
- `tools/mobile-test/boot-emulator.ps1` — create/boot the AVD.
- `tools/mobile-test/build-dev-apk.ps1` — build + install the hermetic dev APK.
