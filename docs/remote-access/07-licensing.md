# Mission Control Remote Access — Licensing & Open-Core Model

**Status:** Draft
**Owner:** Ron Levy
**Last updated:** 2026-04-27
**Depends on:** `01-architecture.md`, `02-attestation-protocol.md`, `04-abuse-prevention.md`, `05-build-pipeline.md`
**Companion memory:** `feedback_no_paid_code_signing.md`

This document specifies the licensing model for Mission Control + the `clayrune.io` platform: which parts are open source, which are proprietary, why, and what that means for forks, contributors, and platform users.

---

## 1. The model: open-core

Mission Control is **open core**:

- **MC core** (the app users run on their PC — dashboard, agent system, server, UI) is **open source** under a permissive license (MIT or Apache 2.0; final pick in §3).
- The **remote-access integration** (the proprietary `mc-tunnel` Rust binary + the `mc_remote` Python glue that talks to it) is **proprietary**, distributed only as compiled binaries under the platform Terms of Service.

This is the same shape as Sentry, GitLab, Mattermost, Cal.com, n8n, and many others. It's a well-trodden licensing pattern.

### What this gives us

- **The community can audit, fork, contribute to, and self-host MC core.** No reliance on a closed product. The dashboard you run on your PC is open and inspectable.
- **The platform (`clayrune.io`) cannot be used by forks without permission.** The proprietary remote-access module is the gate. Forks who want their own remote access build their own provider (or use Tailscale, ngrok, etc.).
- **A clear contribution path.** Outside contributors work on MC core. The proprietary module is operator-only.
- **A clean monetization path** *(deferred to v2)*. Paid-tier features (higher caps, team features, enterprise SSO) live in the proprietary module or the control plane, not in MC core.

### What this does *not* solve

- Open-core + tamper-proof are still in tension. The proprietary binary's `CLIENT_SECRET_PRIV` can theoretically be extracted by a determined adversary. The defense is rotation (every release) + revocation (instant on detection) + server-side enforcement (`04-abuse-prevention.md` Worker caps), not "perfect tamper-proofing."

---

## 2. The two halves, in detail

### 2.1 MC core — open source

**Repo:** `mission-control/` (public, eventually hosted on GitHub).

**License:** **MIT** (recommended) or Apache 2.0.

**Includes:**

- `server.py` — Flask backend
- `static/` — frontend SPA (`index.html` + assets)
- `data/` — empty default data tree
- `mc_tty_shim/` — terminal pop-out support
- `github_sync.py`, `interactive_agent.py` (when re-merged), all agent / hivemind / scheduler / cron logic
- `BUILD_INSTRUCTIONS.md`, `CHANGELOG.md`, all docs in `docs/` **except** `docs/remote-access/` (debatable — see §6).
- `pre_build_fix.py`, `build.spec`, all build scripts that produce the unsigned MC executable

**Does NOT include:**

- `mc-tunnel` Rust crate
- `mc_remote/` Python integration (the runtime piece that drives `mc-tunnel`)
- The control-plane code (`control_plane/`)
- Any baked-in client secrets, platform keys, or attestation logic

**A fork can:**

- Build and run MC locally with all features that don't depend on remote access.
- Replace `mc_remote/` with their own implementation backed by their own infrastructure (Tailscale, ngrok, etc.) — see §4.
- Distribute their fork under the MIT license (with attribution).

**A fork cannot:**

- Use `clayrune.io` for remote access (the proprietary `mc_remote` + `mc-tunnel` artifacts are not in their tree).
- Use the name "Mission Control Cloud" or "Clayrune" or any of the platform's trademarks (see §5).

### 2.2 `mc-tunnel` + `mc_remote` — proprietary

**Repo:** `mc-remote/` (private; operator-only; eventually a separate GitHub repo with restricted access).

**License:** "All rights reserved. Distributed only as compiled artifacts under the Mission Control Cloud Terms of Service."

**Includes:**

- `mc_tunnel/` — the Rust crate that runs the tunnel
- `mc_remote/` — the Python module shipped as a Cython-compiled wheel (so the embedded client secret is not trivially `cat`-able)
- `scripts/gen_client_key.sh` — generates a fresh `CLIENT_SECRET_PRIV` per release (per `05-` §3.1)
- `client_keys/*.pub` — registry of pubkeys (the **public** halves are tracked here for audit; the private halves never live on disk past the build that embedded them)
- Build scripts, signing scripts (when code-signing eventually returns)

**End users receive only the compiled artifacts.** The source is operator-only. The license under which they receive the binaries is the platform TOS, which permits use of the binary alongside MC core for the purpose of accessing `clayrune.io`.

### 2.3 Control plane — proprietary

**Repo:** `control_plane/` (lives in `mission-control/control_plane/` for v1, might split later).

**License:** "All rights reserved." Operator-only. Never distributed to end users (it runs as a hosted service at `api.clayrune.io`).

This is conceptually similar to `mc-remote/` — operator-only — but distinct because it never ships to user devices. The control plane is *the platform service*; the remote access module is *the platform's client*.

---

## 3. License choice for MC core: MIT vs Apache 2.0

| Factor | MIT | Apache 2.0 |
|---|---|---|
| Length | Short (~17 lines) | Long (~200 lines) |
| Patent grant | None (implicit at best) | Explicit grant from contributors |
| Contributor patent retaliation clause | None | Yes (terminates patent grant if contributor sues) |
| GPL-compatibility | Compatible with GPLv2 and v3 | Compatible with GPLv3 only |
| Industry preference for permissive open-core | Common (Cal.com, n8n) | Common (GitLab, Mattermost) |
| Familiarity to non-lawyer readers | Highest | Moderate |

**Recommendation: MIT.** Simplicity wins for v1. Patent risk is low for a personal-productivity tool with no novel patents in play. Easy for non-lawyer contributors to read. Re-license to Apache 2.0 later if you collect contributor patent risk you want to formalize.

(Cite: this is not legal advice. If you take outside investment or hire counsel, the choice gets re-evaluated then.)

---

## 4. The "remote access provider" interface (forkability)

To make the open-core split honest, MC core should expose a **documented interface** for remote-access providers, so forks can plug in their own without having to fork the whole runtime.

### 4.1 Interface shape

```python
# In MC core, in module `mc_remote_iface/` (open source, just an interface):

class RemoteAccessProvider(Protocol):
    """Implemented by mc_remote/ (proprietary) or any fork's replacement."""

    def is_enabled(self) -> bool: ...
    def status(self) -> dict:    # { online, hostname, last_seen, error_code? }
        ...
    def enable(self) -> str:     # returns enrollment URL to open in browser
        ...
    def disable(self) -> None: ...
    def disconnect_this_device(self) -> None: ...
    def get_caps(self) -> dict:  # { bandwidth_remaining, rate_limit_rps, ... }
        ...

def register_provider(p: RemoteAccessProvider) -> None: ...
def get_provider() -> RemoteAccessProvider | None: ...
```

MC core's frontend Settings panel calls `get_provider()`. If `None`, it shows "No remote access provider installed. [Learn more about Mission Control Cloud]" with a link to `clayrune.io`. If present, it renders the panel as designed.

### 4.2 Default vs platform implementation

- **No remote access provider** (default for self-built MC core): Settings panel shows the marketing CTA. Everything else works normally.
- **Mission Control Cloud provider** (`mc_remote` package, proprietary): plugs into the interface; talks to `mc-tunnel`; uses `clayrune.io`.
- **A fork's provider**: implements the same interface against Tailscale, ngrok, their own infrastructure, etc.

### 4.3 Why this matters

It keeps the open-source claim honest. If MC core *only* worked with the proprietary platform, calling it "open source" would be misleading — it'd be open-source-but-crippled. The interface ensures the open-source build is genuinely useful and replaceable.

---

## 5. Trademarks and naming

Names that need to be reserved (do not put in MC core's open-source LICENSE):

- "Mission Control" (the product) — open to be ambiguous; many other things use this name (NASA, Slack, Apple).
- "Mission Control Cloud" (the platform service) — should be more distinctive; consider trademarking eventually.
- "Clayrune" (the company / domain) — should be trademarked when the project has commercial traction.
- The "M" favicon, color palette, logo, design system — keep under the platform's brand guidelines, not in the open-source repo's `static/`.

Forks may call themselves whatever they like, **as long as they don't use the trademarked names** to imply official affiliation. This is standard open-source trademark practice (Mozilla / Firefox does this; Linux distros do this).

A short **TRADEMARKS.md** at the root of the open MC repo will spell this out: forks are welcome, "Mission Control" the name is informal and not strongly defended, "Clayrune" / "Mission Control Cloud" are reserved.

---

## 6. Doc placement: `docs/remote-access/` — public or private?

Open question. The remote-access design docs (this directory, `01-` through `07-`) describe the protocol that the proprietary `mc-tunnel` implements.

**Argument for keeping them public** (with MC core):
- The protocol is the contract a fork would need to know to write a competing provider.
- Transparency about how the platform works is good for community trust.
- Someone reverse-engineering `mc-tunnel` would discover the protocol anyway.

**Argument for moving them private** (with `mc-remote/`):
- Some details (rotation cadence, abuse-detection signals) are easier to abuse if openly documented.
- "Security through obscurity isn't security, but obscurity is a fine speed bump."

**Recommendation: split.** Move to public:
- `01-architecture.md`, `02-attestation-protocol.md`, `03-control-plane-api.md`, `error_codes.md`, `07-licensing.md`, `control_plane/api_spec.yaml`

Move to private:
- `04-abuse-prevention.md` (specific thresholds, risk-score weights are actively useful to abusers)
- `05-build-pipeline.md` (specifics of client-key generation, embedding, rotation are operator-internal)
- `06-rollout-plan.md` (operator-internal)

Public docs can reference private ones with a note like "(operator-internal; not published)."

This is something to act on at the same time as the repo split (when `mc-remote/` becomes its own private repo). For v1 in a single tree, leave everything where it is.

---

## 7. The TOS posture (skeleton; legal review later)

A complete TOS is out of v1 scope (per `04-` §8). The licensing-related clauses to lock in early:

1. **MC core** is licensed as open source under MIT (or Apache 2.0). Users who build MC themselves get the open-source license; nothing else.
2. **Mission Control Cloud** (the binaries: `mc-tunnel`, `mc_remote`, plus access to `clayrune.io`) is licensed *separately* under the platform TOS. Use of the platform requires accepting the TOS at signup time.
3. **The platform TOS reserves the right to revoke binary distribution and platform access at any time** for cause (abuse, payment failure, TOS violation).
4. **Reverse engineering** of the proprietary binaries is not licensed and may be additionally protected by anti-circumvention law (DMCA §1201 in the US, equivalent elsewhere). The TOS doesn't grant a license to reverse-engineer.
5. **Forks of MC core** are explicitly permitted; the platform doesn't claim any rights over forks under the open-source license.
6. **Trademark** boundaries (per §5).

A short legal-counsel review pass should happen before the public alpha (M6 in `06-`).

---

## 8. License-related action items (v1 timeline)

In rough priority order:

1. **Add `LICENSE` (MIT) to `mission-control/`** when the repo flips to public.
2. **Add `TRADEMARKS.md` to the root** describing the trademark posture (per §5).
3. **Create `mc_remote_iface/`** in MC core: the Protocol class + `register_provider` / `get_provider` (per §4.1). Even before any provider exists, the interface is the open-source contract.
4. **Move `mc_remote/` and `mc_tunnel/` into a private repo `mc-remote/`** when their content stabilizes.
5. **Add `LICENSE.proprietary`** at the root of `mc-remote/`: a single short file stating "All rights reserved. Distributed under the Mission Control Cloud TOS."
6. **Add an `ALL CAPS NOTICE`** at the top of every proprietary source file: a copyright header + "PROPRIETARY AND CONFIDENTIAL" line. Standard practice.
7. **Get a TOS draft** before public alpha. Use a template (Termly, GetTerms, or borrow from a similarly-shaped open-core SaaS). Have a lawyer review.

---

## 9. Open questions

1. **Apache vs MIT for MC core** — settle before flipping the repo public.
2. **Repo split timing** — when does `mc-remote/` move out of the main tree? Recommend: as soon as the proprietary code grows past a few hundred lines, before any contributor sees the open repo.
3. **Contributor License Agreement (CLA)?** Some open-core companies use a CLA so contributors assign rights and the company can re-license later. Adds friction to contribution. Recommend: skip for v1, revisit if you take investment.
4. **Source-available license** (e.g. BUSL, FSL) instead of fully proprietary for `mc-remote/`? BUSL allows source visibility but restricts commercial use; converts to true open source after N years. Tradeoff: visibility helps trust, but the whole point of the proprietary module is that the secret-bearing piece is opaque. Recommend: **stay fully proprietary** for `mc-remote/` in v1.

---

## 10. Cross-references

- Why open-core is the chosen answer to the tamper-resistance question: this conversation, plus `feedback_no_paid_code_signing.md`
- The technical mechanism of the proprietary moat: `02-attestation-protocol.md` §3.6 (client secret) + §7 (dual signatures)
- How the build pipeline produces and registers per-release client keys: `05-build-pipeline.md` §3.1 + §3.4
- Server-side enforcement that bounds damage from a compromised client secret: `04-abuse-prevention.md` (Worker, traffic caps, risk score)
- Operational rotation procedure: `05-build-pipeline.md` §7 (renumbered) + `02-attestation-protocol.md` §3.6
