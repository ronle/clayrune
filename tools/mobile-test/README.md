# mobile-test — Android chat-switching release harness

Drives the **real Capacitor WebView on an emulator** (CDP over `adb`) through
chat-navigation scenarios and asserts the agent's output is never silently
dropped or duplicated. See `docs/MOBILE_RELEASE_TEST_PLAN.md` for the full plan,
the bugs it guards against, and the oracle.

## Quick start

```
pwsh tools/mobile-test/boot-emulator.ps1      # create + boot the AVD (once)
pwsh tools/mobile-test/build-dev-apk.ps1      # build + install hermetic dev APK
npm  install --prefix tools/mobile-test       # harness deps (chrome-remote-interface)
node tools/mobile-test/run.js                 # run S1..S4
```

Requires MC running on `localhost:5199`. The emulator reaches it at
`http://10.0.2.2:5199`; the dev APK loads the SPA from there with no Cloudflare
and no credential gate (debug-only — never ship it).

## Files

| File | Role |
|------|------|
| `run.js` | Scenario runner (S1–S4) + pass/fail oracle. |
| `lib/adb.js` | adb wrapper: WebView devtools-socket discovery, forward, app lifecycle (HOME/foreground), screencap. |
| `lib/cdp.js` | Connect to the dashboard page over CDP (`local: true` is required for the Android WebView inspector). |
| `lib/app.js` | SPA driving (dispatch, switch, kill-SSE) + server truth (`/agent/status`) + scratch-project bootstrap. |
| `probe.js` | CDP connectivity smoke test. |
| `discover.js` | Dump the SPA's reachable driving surface (debugging aid). |
| `boot-emulator.ps1` / `build-dev-apk.ps1` | One-time environment setup. |

## Env vars

`MC_ONLY=S2` (one scenario) · `MC_NORELOAD=1` (skip WebView reload) ·
`MC_TEST_PROJECT=<id>` · `MC_PROMPT="..."` · `MC_HOST_API` · `MC_ADB` ·
`MC_SERIAL` · `MC_CDP_PORT`.

## Notes

- The harness reloads the WebView each run so it tests the current
  `static/index.html` (the SPA is long-lived and won't otherwise see edits).
- Scenarios inject the failure (kill the session's SSE) to deterministically
  reproduce what Doze/socket-parking does organically; the **recovery triggers**
  (tab switch, foreground) are real user actions, so the fixes they validate
  apply to the real app.
- Native transport only — the CF tunnel + custom `EventSource` polyfill are not
  exercised here (see the manual checklist in the test plan).
