# User Flow — End-to-End

**Last updated:** 2026-04-30

This document walks through the user experience from the moment someone installs Mission Control to the moment they're using it remotely from another device. It's intentionally non-technical — for the implementation specifics, see `01-architecture.md`, `02-attestation-protocol.md`, and `03-control-plane-api.md`.

---

## The four phases

```
╔════════════════════════════════════════════════════════════════════════╗
║  PHASE 1 — Install + use MC locally  (one-time setup, ~2 min)          ║
╠════════════════════════════════════════════════════════════════════════╣
║                                                                        ║
║   User              [download MC]  ──►  launches Tauri app             ║
║                                              │                         ║
║                                              ▼                         ║
║                                   http://localhost:5199                ║
║                                   (works offline, no signup)           ║
║                                                                        ║
║   At this point: MC works fine. No tunnel, no signin, no domain.       ║
║   Remote access is OPT-IN, not required.                               ║
╚════════════════════════════════════════════════════════════════════════╝
                                  │
                                  │  user clicks "Enable Remote Access"
                                  ▼
╔════════════════════════════════════════════════════════════════════════╗
║  PHASE 2 — Enroll  (one-time per PC, ~5 sec)                           ║
╠════════════════════════════════════════════════════════════════════════╣
║                                                                        ║
║   Settings → Remote Access → [ Enable Remote Access ]                  ║
║   Settings → Remote Access → enter username "ron"                      ║
║                                              │                         ║
║                                              ▼                         ║
║              ┌───────────────────────────────────────────┐             ║
║              │  MC, behind the scenes:                   │             ║
║              │   • generates Ed25519 keypair             │             ║
║              │     (stored in Windows Credential Mgr)    │             ║
║              │   • POSTs /v1/enroll to control plane     │             ║
║              │   • CP creates CF tunnel + DNS + Access   │             ║
║              │     app for ron.clayrune.io               │             ║
║              │   • CP returns enrollment_token           │             ║
║              │   • MC spawns cloudflared.exe             │             ║
║              │   • MC starts attestation loop (10-min)   │             ║
║              └───────────────────────────────────────────┘             ║
║                                              │                         ║
║                                              ▼                         ║
║                  Status pill goes green: "Online"                      ║
║                  URL shown:  https://ron.clayrune.io  [Copy]           ║
║                                                                        ║
╚════════════════════════════════════════════════════════════════════════╝
                                  │
                                  │  user opens URL on a different device
                                  ▼
╔════════════════════════════════════════════════════════════════════════╗
║  PHASE 3 — Remote access from any device  (per-device, ~30 sec first)  ║
╠════════════════════════════════════════════════════════════════════════╣
║                                                                        ║
║   📱 Phone        🖥 Tablet         💻 Friend's laptop                 ║
║      │                │                    │                           ║
║      └────────────────┴────────────────────┘                           ║
║                       │                                                ║
║                       ▼                                                ║
║              https://ron.clayrune.io                                   ║
║                       │                                                ║
║                       ▼                                                ║
║         ┌──────────────────────────────┐                               ║
║         │  Cloudflare Access OTP page  │   "Email me a sign-in code"   ║
║         └──────────────────────────────┘                               ║
║                       │                                                ║
║                       ▼                                                ║
║              user gets 6-digit code via email → enters it              ║
║                       │                                                ║
║                       ▼                                                ║
║         ┌──────────────────────────────┐                               ║
║         │  MC: "Name this device"      │   ← only on first visit       ║
║         │   • [ My iPhone ]            │     for this CF session       ║
║         │   • [ My Phone  ]            │                               ║
║         │   • [ Work Laptop ]          │                               ║
║         │   • or type a custom name    │                               ║
║         └──────────────────────────────┘                               ║
║                       │                                                ║
║                       ▼                                                ║
║         ┌──────────────────────────────┐                               ║
║         │  Mission Control dashboard   │   ← full UI, all features     ║
║         │  (same as localhost)         │     available remotely        ║
║         └──────────────────────────────┘                               ║
║                                                                        ║
║   For the next 24h: visiting the URL skips OTP + naming page →         ║
║   straight to dashboard.                                               ║
║                                                                        ║
╚════════════════════════════════════════════════════════════════════════╝
                                  │
                                  │  user manages from PC
                                  ▼
╔════════════════════════════════════════════════════════════════════════╗
║  PHASE 4 — Day-to-day controls  (Settings → Remote Access)             ║
╠════════════════════════════════════════════════════════════════════════╣
║                                                                        ║
║   ◉ Online   ron.clayrune.io  [Copy]  [Pause]  [Disconnect]            ║
║                                                                        ║
║   Active sign-in sessions  (named per device)                          ║
║   ─────────────────────────────────────────────                        ║
║   📱 My iPhone     · Safari on iPhone · 13m ago    [Sign out]          ║
║   💻 Work Laptop   · Chrome on Mac    · 2h ago     [Sign out]          ║
║                                              [Sign out everywhere]     ║
║                                                                        ║
║   [Pause]      → stops cloudflared; keeps keys; quick resume           ║
║   [Disconnect] → revoke device on CF; wipe keys; clean slate           ║
║                                                                        ║
╚════════════════════════════════════════════════════════════════════════╝

LEGEND
  ────────────────────────────────────────────────────────────────────
  User-facing surface        Behind the scenes
  ────────────────────────   ────────────────────────────────────────
  Tauri / web dashboard      MC server (Flask, port 5199)
  Settings panel             mc_remote (proprietary; supervisor + attest)
  ron.clayrune.io URL        Cloudflare (tunnel + Access OTP)
  "Name this device" page    Cloud Run control plane (FastAPI + Firestore)
  Sessions list              data/session_labels.json (nonce → name)
```

---

## Key UX principles the flow encodes

### Three distinct journeys; only one is required

Local use never needs the remote-access setup. Remote setup is a single one-time click. Visiting from a new device is a one-time OTP + name-this-device.

### Naming happens once per CF session (24h)

Returning to the same device within 24h skips OTP + naming. The session label is keyed by the CF Access nonce; when CF expires the cookie at 24h and the user re-OTPs, a new nonce → naming page again. Acceptable annoyance; could be smoothed later by also keying on a UA fingerprint and copying the label forward.

### Pause is reversible; Disconnect is destructive

- **Pause** stops `cloudflared` but keeps the device keys + Firestore row + CF resources. Resume is instant.
- **Disconnect** revokes the device on the platform: deletes CF tunnel + DNS + Access app, deletes the Firestore device row, releases the username claim, and wipes the local keystore. End-to-end clean teardown — no orphans. The username becomes free for re-enrollment.

### What the user never has to think about

- Key generation, key rotation, key storage
- Tunnel lifecycle, cloudflared crash recovery
- Certificate provisioning (CF Access handles user-facing TLS; Google/Cloud Run handles CP-facing TLS)
- Attestation envelopes, dual signatures, the 14+1-step verification chain
- CF API calls, rate limits, account-wide config
- Firestore schema, idempotency keys, transaction semantics

### What we surface deliberately

- The public URL (with a copy-to-clipboard button that works in WebView2)
- A four-color status pill (Online / Offline / Connecting / Error) with a one-line cause hint
- Named sessions with parsed user-agent + relative time
- One-shot device naming on each new sign-in
- Sign-out per session (best-effort) and sign-out-everywhere (always works)

Enough to feel in control without exposing internals.

---

## Failure modes the user might see

| Symptom | What's happening | What the user does |
|---|---|---|
| Status pill stays "Connecting" indefinitely | Supervisor running but attestation failing or cloudflared not connected | Click Disconnect, then Enable again — clean re-enroll bypasses any stale state |
| URL returns CF Access "user not allowed" | The signing email doesn't match the user's enrolled email | Sign out of CF Access in browser, retry with the correct email |
| "Sign out" button does nothing visible | CF doesn't expose per-session revoke for our token; falls back to revoking all sessions | Toast surfaces this; "Sign out everywhere" always works |
| Mobile sign-in doesn't trigger naming page | CF cookie still valid from a prior unnamed session | Click "Sign out everywhere" in Settings, then reload mobile — fresh OTP triggers naming |

---

## Cross-references

- `01-architecture.md` — component overview, data plane, key material
- `02-attestation-protocol.md` — envelope shape + 14+1-step verify chain
- `03-control-plane-api.md` — endpoint contracts
- `04-abuse-prevention.md` — bandwidth caps, rate limits, abuse signals
- `RESUME_HERE.md` — how to bring everything back up after reboot
- `SETUP_CHECKLIST.md` — first-time GCP/CF/Firebase wiring (per environment)
