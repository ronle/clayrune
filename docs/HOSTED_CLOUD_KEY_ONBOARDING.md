# Hosted Clayrune — Assisted Key Onboarding (BYOK for non-technical users)

**Status:** spec v1 (2026-06-02). Companion to `docs/HOSTED_CLOUD_PLATFORM_DESIGN.md`
§5 (key custody) and §11 (phases). Solves the one real friction in the chosen
BYOK model: getting a non-technical, mobile-first user a working provider key
without it feeling technical.

---

## 1. The reality we design around (verified 2026-06-02)

- **No OAuth shortcut.** No major provider offers third-party *delegated API-key
  creation* for consumers. And Anthropic (Feb 2026 usage policy) **bans using
  Claude Free/Pro/Max OAuth tokens in any third-party service** — OAuth is for
  Claude Code / claude.ai only. So a hosted service **must** use **API-key
  BYOK**, never subscription OAuth. ([Anthropic OAuth ban](https://explore.n1n.ai/blog/anthropic-bans-oauth-third-party-claude-guide-2026-02-19))
- **The friction floor is the provider's account + billing**, which is
  unavoidable in BYOK (it's the user's own account). We can't remove it; we make
  it a guided two-minute path and validate instantly.
- **Gemini is the low-friction on-ramp.** Google AI Studio issues a free API key
  with an existing Google account, **no credit card**, in ~2 minutes (free tier
  ~1,500 req/day Flash). ([AI Studio free key](https://ai.google.dev/gemini-api/docs/api-key))

**Strategy that falls out of this:** *"Start free in 2 minutes with Gemini; add
your Claude key anytime for top quality."* The non-technical user is running
immediately (Gemini, free, no card); the heavier BYOK setup (account + billing)
only applies when they choose Claude/OpenAI for quality.

---

## 2. The flow

```
Workspace setup
   │
   ├─ Pick a provider  (ease signal shown; Gemini recommended as the free start)
   │
   ├─ "I already have a key"  ──► paste ──► validate ──► vault ──► done   (power users)
   │
   └─ "Help me create one"   ──► GUIDED WIZARD (per provider):
          1. Deep-link button → opens the provider's key page (as deep as possible)
          2. Step cards w/ screenshots: sign in / create acct → (billing) → create key, copy
          3. Return to app → paste-back field (clipboard-assisted)
          4. INSTANT validation: tiny test call → "✓ your key works" / clear error
          5. Vault: key → TLS → control-plane KMS vault (never the workspace volume)
```

Design rules:
- **Deep-link as far as possible** (straight to the API-keys page, not the
  homepage) to cut navigation.
- **Validate before finishing** — a non-technical user must never discover a bad
  key later as a mystery failure. One cheap test call, green check or a specific
  fix-this message.
- **Paste-back, not hand-off-and-pray** — the app waits on a paste screen and
  detects the key; on mobile, offer "I set it up on a computer" as a fallback
  (the key lands in the same cloud vault wherever it's pasted).

---

## 3. Provider registry (adding a provider = one row)

| Provider | Ease | Key page | Account + billing? | Env var | Validate |
|---|---|---|---|---|---|
| **Gemini** (AI Studio) | ★ easiest, **free, no card** | `aistudio.google.com/app/apikey` | No (free tier) | `GEMINI_API_KEY` | tiny `generateContent` |
| **Claude** (Anthropic) | best quality | `console.anthropic.com/settings/keys` | Yes (card) | `ANTHROPIC_API_KEY` | 1-token `messages` |
| **OpenAI** | — | `platform.openai.com/api-keys` | Yes (card) | `OPENAI_API_KEY` | `GET /v1/models` |

(Deep-link URLs + key formats verify-at-build.) The runtime side is already
done — `agent_runtime.py` abstracts providers and reads these env vars; this spec
is the **onboarding + vault** layer on top.

---

## 4. Custody (ties to design §5 committee conditions)

- **Capture → vault, never the volume.** The wizard sends the key over TLS to the
  control plane; it's encrypted at rest in a **KMS vault**. It is **never** written
  to the workspace volume or to logs.
- **Inject at wake only.** The orchestrator injects the key as the provider's env
  var into the workspace process at boot/resume (tmpfs/secret-mount). MC's dispatch
  inherits it (verified, design §5) — no dispatch code change.
- **Disable the plaintext write path `[C:S2.1]`.** Hosted mode must disable MC's
  Settings→Providers `provider_env.json` write (`server.py:3008`) for keys, or
  relocate it off the snapshotted volume — otherwise a key set in-app lands on disk.
- **API-key only, never OAuth tokens** (Feb 2026 compliance) — the hosted Claude
  Code CLI runs in API-key mode (`ANTHROPIC_API_KEY`); never `claude /login`
  subscription OAuth inside a hosted VM.
- **Trust copy shown to the user:** *"Encrypted and stored securely. Used only to
  run your agents, never shown, never logged. Remove it anytime."*

---

## 5. Rotation / revoke

- **Update:** re-run paste + validate; re-injects on next wake.
- **Revoke `[C:S2.2]`:** clear the vault entry **+ destroy any suspend snapshot +
  cold-boot** so a frozen RAM image can't resurrect the key. Tell the user we
  can't revoke it at the provider — surface a "rotate it at <provider>" link too.

---

## 6. Build & phasing

- **Low-build:** dispatch/runtime (MC already abstracts providers + inherits env).
- **New:** the onboarding wizard UI (per-provider cards, deep-links, paste-back,
  validation), the control-plane KMS vault (capture/inject/rotate), the provider
  registry, and the hosted-mode guard on `provider_env.json`.
- **Phasing:** lands in design **§11 Phase 1** (onboarding + vault + injection).
  Use **Gemini-first** for Phase 0/1 validation — it's the only provider you can
  end-to-end test with zero billing friction.

---

## 7. Open questions

1. **Clipboard access on mobile** — paste-back UX differs iOS/Android; confirm the
   Capacitor shell can read the clipboard with permission, else a manual paste field.
2. **Validation cost** — the test call costs the *user* a sliver of their own
   tokens (BYOK). Keep it to ~1 token; tell them "we ran a tiny test."
3. **Multi-key workspaces** — a user may hold Gemini + Claude. Registry + vault are
   per-(workspace, provider); the dispatch model pick (design §model-policy) chooses
   which key to use per session.
4. **Provider availability by region** — Gemini AI Studio excludes some regions;
   the wizard must detect and fall back to another provider.
