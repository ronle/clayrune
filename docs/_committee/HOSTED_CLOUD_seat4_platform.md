## Seat 4 — Platform & licensing — RATIFY-WITH-CONDITIONS

**Decision:** RATIFY-WITH-CONDITIONS

**Summary (1-2 sentences):** The compute design is sound and the BYOK env-inheritance claim is code-confirmed, but the doc materially *glosses* the reconciliation with the existing control plane: hosted enrollment is a **parallel enrollment-by-provisioning path** that bypasses the entire attestation/device-key/mc-tunnel spine the current `control_plane/` + `02-attestation-protocol.md` are built on — this fork is real and unacknowledged. Separately, the restated "we run the whole stack" moat is **mostly convenience, not a moat** (the open core is trivially self-hostable on the user's own Fly account), with one genuine exception (the proprietary `mc-tunnel` client-secret key) that the design *discards* for hosted — so §8's pricing-power story needs to be told honestly as a convenience/lock-in-via-data story, not a defensibility story.

---

### Blockers (if any)

None that rise to irreconcilable. The enrollment fork (Condition 1) is the closest — it is a structural conflict with the existing plane — but it is reconcilable by *consciously declaring a second enrollment path* rather than pretending the existing one extends. I classify it must-fix-in-design rather than BLOCK because the design can be made correct with a spec edit; it does not require abandoning a locked decision. If the v1.1 edit instead doubles down on "reuse the existing device-enrollment concepts" without acknowledging the fork, re-escalate to BLOCK.

---

### Conditions (RATIFY-WITH-CONDITIONS only)

#### Condition 1 (must-fix-in-design): Acknowledge enrollment-by-attestation vs enrollment-by-provisioning as a deliberate FORK, not an extension.

**Why:** The design asserts (§1) it "reuses … the device-enrollment concepts" and (§9) the client is an "onboarding-only change." This is false at the data-model and protocol level. Concretely, the existing control plane cannot be reused for hosted enrollment as written:

- **`EnrollRequest` requires `device_pub_b64`** (`control_plane/api_spec.yaml:135-141`; `EnrollRequest.required` lists `device_pub_b64`). That is an Ed25519 *device keypair generated on the user's PC and held in the OS keystore* (`02-attestation-protocol.md` §3.4; `mc_remote/config.py:100-108` `KEYSTORE_KEYS`). **Hosted mode has no user PC to generate or hold a device private key.** Either the microVM holds it (then "device" = our VM, and the user-facing "your device" semantics collapse) or hosted enrollment skips it (then it's a different request shape → a different endpoint).
- **`_do_enroll_after_auth` provisions a CF *named tunnel* and points its ingress at `http://localhost:5199`** (`control_plane/app/routes_account.py:1001-1008`: `create_named_tunnel(...)` then `set_tunnel_ingress(service_url="http://localhost:5199")`). This is the existing model's core assumption: a `cloudflared` running on the *user's PC* dials *out* and the tunnel forwards to that PC's loopback. **The design's §7 explicitly drops per-VM `cloudflared`/`mc-tunnel`** and routes via a control-plane ingress proxy instead. So the entire enroll provisioning body is wrong for hosted — there is no out-dialing cloudflared to terminate a named tunnel.
- **The whole attestation surface is inapplicable.** `/v1/nonce` + `/v1/attest` (`api_spec.yaml:742-809`) and the 14-step verification (`02-` §7.4) require a signed `AttestationRequest` carrying *both* `signature_b64` (device key) and `client_signature_b64` (the mc-tunnel client-secret — `api_spec.yaml:415-420`, `02-` §3.6). **Hosted mode emits no attestation envelope** — there is no mc-tunnel in the VM to sign one, and §7 says so. The tunnel-token-issuance machinery (`AttestationResponse`, `api_spec.yaml:446-468`) has no hosted analog.

**Proposed fix:** Add a §7.x subsection "Hosted enrollment is a separate path" stating plainly: hosted onboarding does NOT use `/v1/enroll` + attestation. It is a new control-plane surface (provision-VM + capture-key + bind-account) under, e.g., `/v1/hosted/*`. State which existing endpoints are REUSED verbatim (`/v1/signin/start`, `/v1/signin/complete`, Firebase auth, `/v1/account`, `/v1/sessions*` for CF Access session management — these are device-agnostic), which are REPLACED (`/v1/enroll`, `/v1/nonce`, `/v1/attest`), and which are REUSED-WITH-SHIFTED-SEMANTICS (the `devices` collection row now describes a microVM, not a PC; `/v1/devices/{id}/mobile-tokens` binds a phone to the VM's Access app — see Condition 4). The "onboarding-only change" framing in §9 must be corrected to "a second, distinct onboarding flow" (see Condition 5).

**Gate phase:** §11 Phase 1 (Control-plane provisioning) — but the spec edit gates v1.1 before any Phase-1 code.

---

#### Condition 2 (must-fix-in-design): Restate the moat honestly — it is convenience + data-gravity lock-in, NOT a structural moat — and reconcile with §8 pricing power.

**Why:** §7 asserts the moat "shifts and strengthens" because "we run the entire stack … far harder to replicate than a tunnel binary." Pressure-tested against `07-licensing.md`, this is mostly wrong:

- Today's moat is concrete and proprietary: the closed Rust `mc-tunnel` binary embeds `CLIENT_SECRET_PRIV` (`02-` §3.6), and `/attest` step 4.5 *rejects any envelope not signed by an active platform client key* (`api_spec.yaml:337` / `02-` §7.4 step 4.5). A fork literally cannot use `*.clayrune.io` without extracting that key. That is a real (if imperfect — `07-` §1 "What this does not solve") technical gate.
- The hosted "moat" is, by the design's own §9, a **Fly Machines API caller + a KMS-backed key vault + an ingress proxy** on top of the **open-source MC core** (`07-licensing.md` §2.1: `server.py`, the agent/hivemind/scheduler logic, all of it is MIT). Every one of those orchestration pieces is a well-known commodity. A competent competitor — *or the user themselves* — can `pip install` the open core, put it on their own Fly account, write the ~same provision/wake/inject scripts, and have the identical product in a weekend. There is no embedded secret, no attestation, no proprietary protocol gating it. **"We run it for you" is a convenience, not a moat.**
- The one thing that WOULD be a moat — the proprietary `mc-tunnel` client-secret binding — is exactly what §7 *drops* for hosted. So hosted strictly *weakens* the technical defensibility relative to the BYO-machine product.

What actually defends hosted revenue is (a) **operational convenience** (we babysit the fleet, sleep/wake, backups), (b) **data gravity** (the user's codebases + memory live on our volume — §6), and (c) **brand/trust**. Those are legitimate SaaS levers, but they are *retention/switching-cost* levers, not *defensibility-against-a-competitor* levers, and they cap pricing power: the alternative to "$X/mo hosted" is "run the same open core on $5/mo of my own Fly compute." §8 says "our subscription can be modest and still margin-positive" — that instinct is *correct*, but the doc should say it is modest **because the moat is thin**, not despite a strong moat.

**Proposed fix:** Rewrite the §7 "moat shifts and strengthens" paragraph. Replace with: "The hosted control plane is proprietary but not a strong technical moat — the open MC core is self-hostable, and we deliberately drop the one proprietary binding (`mc-tunnel`'s client secret, `02-` §3.6) for hosted. Hosted's defensibility is convenience + data gravity + brand, which are switching-cost levers, not anti-clone levers. This bounds pricing power (§8): price against 'run it yourself on raw Fly,' not against 'no alternative.'" Add a one-line cross-reference in §8 so the pricing rationale and the moat honesty agree.

**Gate phase:** §11 Phase 3 (Productize — pricing) is where it bites commercially, but it is a design-truthfulness fix → gates v1.1.

---

#### Condition 3 (must-fix-in-design): Specify the open/closed boundary for the hosted "mode" flag so no hosted-specific logic leaks into the open MC core, and confirm the existing BYO-machine licensing is not eroded.

**Why:** §9 ("Modified lightly") adds "a 'hosted mode' flag so the server skips per-VM `cloudflared`/`mc-tunnel` startup." `07-licensing.md` §2.1 lists `server.py` and "all agent / hivemind / scheduler / cron logic" as **open-source MC core**, and §4 makes the open-core claim "honest" via the `RemoteAccessProvider` Protocol — the open core must be *genuinely useful and replaceable* (§4.3), not "open-source-but-crippled." Two leak risks:

1. A bare boolean is benign. But "skip mc-tunnel startup, defer routing to the control plane" can easily grow into hosted-specific ingress/wake/idle-reporting code (§9's "tiny in-VM agent … so the orchestrator can read idle state + `next_run` and signal safe-to-suspend"). If that in-VM agent or any orchestrator-coupling lands in `server.py` (open core), the open repo now contains proprietary-platform glue — muddying the §2.1 boundary and handing competitors the exact wake/idle contract.
2. Today the open core's remote-access story is *already* clean: `mc_remote/` is the proprietary provider behind the `RemoteAccessProvider` interface (`07-` §4.1; `mc_remote/config.py` is proprietary, header at lines 5-9). The hosted flag must sit on the *same side of that line* — i.e., the "skip mc-tunnel / use control-plane ingress" behavior belongs in the proprietary provider (or a new proprietary `mc_hosted/` module the open core loads via the existing `get_provider()` seam), NOT inline in `server.py`.

Dropping mc-tunnel **for hosted** does NOT weaken the existing BYO-machine product's licensing *as long as the BYO path keeps mc-tunnel*: the moat there is unchanged (`02-` §3.6 still gates `*.clayrune.io` for BYO). The risk is only if the hosted flag is implemented as open-core logic that reveals or replaces the proprietary path. Confirm it does not.

**Proposed fix:** In §9, change "a 'hosted mode' flag" to specify: (a) the open core exposes only a neutral capability seam (e.g., the existing `RemoteAccessProvider` Protocol, or a `mc_compute_host` hook returning `None` in open builds); (b) ALL hosted-specific behavior (skip-tunnel, ingress expectations, idle/`next_run` reporting for the orchestrator) lives in a proprietary module on the `mc_remote/` side of the `07-licensing.md` §2.1 line; (c) explicitly assert the BYO-machine path retains `mc-tunnel` + attestation unchanged, so the existing moat and open-core honesty (`07-` §4.3) are preserved. Add a sentence to `07-licensing.md` §2 noting hosted control-plane components are proprietary, same as `mc-tunnel`/`mc_remote`/`control_plane`.

**Gate phase:** §11 Phase 1 (where the flag + in-VM agent first appear).

---

#### Condition 4 (must-fix-in-design): Reconcile mobile-pairing reuse with the device-record shift; "reuse the existing mobile-tokens machinery" is only ~half-true.

**Why:** §7 says hosted keeps CF Access at the edge and "for the app, service tokens — reuse the existing mobile-tokens machinery." But that machinery (`control_plane/app/routes_account.py:510-642` `create_mobile_token`) is built around a **host MC *device*** that has a `cf_access_app_id` and a `hostname_claim` (lines 560-567: it 409s `device_unprovisioned` if the device has no CF Access app). It mints a CF service token and attaches a Service-Auth policy *to that host device's Access app* (lines 599-613). In the existing model the host is the user's PC, provisioned at `/v1/enroll`. In hosted mode:

- The "host device" is now the **microVM**, and its CF Access app is provisioned by the (new, Condition-1) hosted-provisioning path, not by `/v1/enroll`. So the mobile-tokens code can be *largely* reused, but only after the `devices` row + Access app are created by a different mechanism with shifted semantics ("device" = VM). The doc should say this, not imply the phone-pairing path is unchanged.
- The CF Access **service-token header injection** (`CF-Access-Client-Id` / `CF-Access-Client-Secret` on every request — confirmed at `server.py:14290-14291` `_mobile_pair_verify`, and the `clayrune://pair` QR carries `i`/`s` = client_id/secret at `server.py:14311-14320`) is unchanged by going hosted. The edge is still CF Access; the phone still must present those headers. **This is the load-bearing fact for Condition 6 (iOS).**

**Proposed fix:** In §7, change "reuse the existing mobile-tokens machinery" to "reuse the mobile-tokens *code*, with the host `devices` row now describing the microVM (provisioned via the hosted path, Condition 1), and its CF Access app created at provision time. The phone still presents `CF-Access-Client-*` headers on every request — unchanged from BYO." Cross-link the §10.9 iOS claim to this (Condition 6).

**Gate phase:** §11 Phase 1 (provisioning must create the Access app the pairing flow attaches to) and Phase 3 (onboarding UI).

---

#### Condition 5 (must-fix-in-design): Capture the two-onboarding-flows reality explicitly; the "config-not-rebuild" claim is true for the DATA path only.

**Why:** §7 and §9 lean on "the client is domain-agnostic so pointing it at a hosted instance is config." That is **true and code-confirmed for the data path**: `mc_remote/config.py:26` (`PLATFORM_DOMAIN = os.environ.get("MC_REMOTE_PLATFORM_DOMAIN", "clayrune.io")`) and `control_plane_base_url()` at `:37-53` resolve everything from env; there is no hardcoded `API_BASE`. But the *onboarding UX* genuinely diverges and the doc never says so:

- **Existing (BYO):** "Install MC on your PC → it generates a device keypair → browser hop to Firebase signin → `/v1/enroll` provisions your tunnel → attestation loop begins" (`02-` §6.1 sequence; `mc_remote/enrollment.py`).
- **Hosted:** "Sign up → we provision a microVM + volume → you paste your Anthropic API key → we inject it → you connect GitHub" (design §5.1, §9 "Account / subscription / onboarding UI").

These share Firebase auth and the domain config, but they are two distinct flows with different artifacts (device keypair + attestation vs. pasted key + provisioned VM), different failure modes, and different security surfaces (OS-keystore device key vs. custodial API key). Glossing this as "onboarding-only change" understates the build (it's a *whole new* onboarding flow, §9's own bullet admits "Account / subscription / onboarding UI" is NEW) and risks a UI that tries to unify two genuinely different enrollment models.

**Proposed fix:** Add to §9 (or a short §7.y): an explicit "Two onboarding flows" note distinguishing BYO-attestation from hosted-provisioning, listing the shared substrate (Firebase auth, domain config, CF Access session management) and the divergent parts (device keypair+attestation vs. key-capture+VM-provision). Correct the "onboarding-only change" phrasing to "the data path is config; onboarding is a new second flow."

**Gate phase:** §11 Phase 1 (onboarding) and Phase 3 (productized signup UI).

---

#### Condition 6 (must-fix-in-design): Puncture/qualify the iOS "newly unblocked" claim — removing NAT/tunnel does NOT remove the native-bridge requirement, because CF Access service-token injection remains.

**Why:** §10.9 claims "this design makes the *server* cloud-side, so iOS finally works as a pure client." The premise is that the iOS blocker was NAT/tunnel native machinery. That is **only partly the blocker.** The actual native-code dependency in the existing mobile shell is **CF Access service-token header injection** plus the **Doze/WebView POST bridge** — per project memory ("Capacitor HTTP zombie + CF token failure in WebView shouldInterceptRequest"; "APK v1.5 native POST bridge (HttpURLConnection) … resolves Doze blackhole"; CLAYRUNE.md mobile note on `android.captureInput`). The reasons that machinery is native:

1. **CF Access requires `CF-Access-Client-Id` / `CF-Access-Client-Secret` on every request** (`server.py:14290-14291`; QR carries them at `:14311-14320`). The design **keeps CF Access at the edge** for the app (§7: "for the app, service tokens — reuse the existing mobile-tokens machinery"). A WebView does not natively attach those headers to its own document/subresource fetches → the existing Android shell injects them in native code (MainActivity / Capacitor fetch override, per memory). **Hosted mode does not remove this** — the edge auth is unchanged. So iOS would still need the equivalent native header injection (a WKWebView `URLProtocol` / custom scheme handler or a native HTTP bridge). It is *not* a "pure client."
2. The Doze/background-socket native fix is Android-specific and arguably moot on iOS, so *that* part doesn't carry over — but it was never the CF-Access part.

The honest claim is narrower: **hosted removes the per-device tunnel/attestation native dependency** (no mc-tunnel, no device keypair on the phone — though the phone never ran mc-tunnel anyway, it pairs via service token). It does **not** remove the CF-Access-header native dependency, which is the actual reason the current shell needs native code. iOS is *cheaper* but not "pure client / newly unblocked" unless the edge auth model also changes (e.g., the ingress proxy accepts a Firebase ID token directly instead of CF Access service-token headers — which would be a real simplification, but is NOT what §7 proposes).

**Proposed fix:** Reword §10.9 to: "Hosted removes the per-device *tunnel/attestation* native dependency. It does NOT by itself make iOS a pure client: the edge still uses CF Access service tokens (§7), whose headers a WebView cannot attach natively, so iOS would still need native header injection (WKWebView URLProtocol or an HTTP bridge), exactly as Android does today (`server.py:14290`). iOS becomes *easier*, not unblocked. A true 'pure client' would additionally require replacing CF-Access-service-token edge auth with token-in-app auth at the ingress proxy — out of scope here, but the lever to pull if pure-client iOS is the goal." Keep it flagged as out-of-scope for build, but stop asserting it's unblocked.

**Gate phase:** Not a build gate (iOS is §11 Phase 4 / out of scope). Pure design-accuracy fix → gates v1.1 so the claim isn't carried forward as settled.

---

### Ratifications

- **BYOK env-inheritance is real — verified, not assumed.** §5's "launch the MC process with the user's key in the env; no dispatch change" is code-confirmed. Every Claude-dispatch spawn inherits the parent environment with no curated `env=` that strips `ANTHROPIC_API_KEY`: `agent_runtime.py:1752` (`env = os.environ.copy()` → `:1765` `env=env`), `:2193` → `:2206`, and the follow-up respawns at `:2711` and `:3068` omit `env=` entirely (Popen inherits by default). The terminal pop-out path `server.py:8450` (`{**os.environ, …}`) is the same shape. The "reused unchanged" claim in §9 for the dispatch path holds. This is the single most load-bearing claim in the design and it survives scrutiny — preserve this property; any future curated-env change to dispatch would silently break hosted BYOK.

- **Domain-agnostic client is real for the data path.** `mc_remote/config.py:26` + `control_plane_base_url():37` confirm env-driven domain resolution with no hardcoded `API_BASE`. Pointing the app at a hosted instance IS config at the network layer. Preserve this; do not introduce a hardcoded host.

- **Single-instance invariant → one-VM-per-user is the right call.** Leaning into the invariant (design §4.1) instead of a multi-tenant refactor is correct and keeps the open core unchanged — which is exactly what protects the open-core licensing story (`07-licensing.md` §2.1). The cleaner the in-VM code stays vanilla, the cleaner the §9 boundary stays.

- **Keeping the BYO-machine product on mc-tunnel/attestation while hosted uses a different path is the right boundary** — provided Condition 1 + 3 land. The existing moat (`02-` §3.6, `/attest` step 4.5) is untouched for BYO; hosted simply doesn't need it. Two products, two enrollment models, one shared auth/identity substrate is a coherent platform shape.

- **Discipline preserved.** §12 correctly gates compute-plane code behind committee review (same posture as Memory System §3.A.MID, Skills Curation v2). Good.

---

### Out-of-scope but flagged

- **(→ Seat 2, custody) Custody-liability delta vs. existing CF token holding.** The brief asks how holding a user's *Anthropic API key* differs from already holding *CF service tokens*. Platform-licensing angle worth routing to Seat 2: the existing `control_plane` already custodies CF tunnel tokens and service-token secrets (`devices.cf_tunnel_token` persisted at `routes_account.py:1091`; mobile `client_secret` deliberately NOT persisted, `:625-627`). The ToS/ §7 in `07-licensing.md` is a *binary-distribution + platform-access* TOS, NOT a *data-custody* TOS. Hosted BYOK + persistent volumes (user's codebases at rest on our disk, §6) is a categorically larger custody surface than tunnel brokering. The §10.1 "clear ToS on custody" is hand-waved; this needs a real data-processing/custody addendum, which is a licensing/legal artifact this seat flags but Seat 2 owns the security mechanics of.

- **(→ Seat 3, cost) The reused "$67/mo at 50 users" figure is for the relay-only plane.** `07-`/`06-` figure predates the fleet orchestrator + key vault + per-VM compute; design §8 cites it as if it still bounds the control-plane cost. Flagging for Seat 3 (cost) — it does not, and the design leans on it.

- **(→ Seat 1, lifecycle) The `devices` collection becomes the VM registry.** If Condition 1's hosted path reuses the `devices` Firestore collection with VM semantics, the `online` heuristic (`routes_account.py:96-103`, "last_seen within 15 min") and the CF-webhook `devices.online` update (`api_spec.yaml:812-834`) now describe a *suspended VM* — which will read "offline" by design while perfectly healthy. Lifecycle seat should confirm the orchestrator's idle/awake state is tracked separately from this CF-derived `online` flag, or the UI will misreport asleep-but-healthy VMs as offline.

- **Trademark/repo-split timing unaffected but worth a note.** `07-licensing.md` §9 open question 2 (when `mc-remote/` splits to a private repo) now has a third proprietary citizen — the hosted control-plane components. When the split happens, hosted-plane code should land on the proprietary side from day one (Condition 3), not migrate later.
