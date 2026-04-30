# Mission Control Remote Access — Build & Release Notes

**Status:** Draft (descoped 2026-04-27, open-core update 2026-04-27)
**Owner:** Ron Levy
**Last updated:** 2026-04-27
**Depends on:** `01-architecture.md`, `02-attestation-protocol.md`, `03-control-plane-api.md`, `07-licensing.md`
**Companion docs:** `BUILD_INSTRUCTIONS.md` (existing local-build guide for MC core)

## 0. Scope (revised)

**In scope:** producing a release of MC that includes the `mc-tunnel` binary alongside the existing executable, and registering the release version + the `mc-tunnel` client-secret pubkey with the control plane so attestations are accepted.

**Out of scope** (deferred to a later document, when the broader install flow is resolved):

- OS code-signing (Authenticode / notarytool / Microsoft Store).
- Per-binary SHA256 attestation (the `build_manifest.json` flow described in earlier drafts of this doc and in `02-attestation-protocol.md` §4).
- Reproducible builds.
- Auto-update.
- Multi-platform releases beyond Windows.

This means several pieces of the original attestation design (`02-`) become **unenforced** in v1 — see §1 for the security implications and the simplified flow that replaces them. The **client-secret signing chain (`02-` §3.6)** is in scope and is the v1 platform-binding moat.

---

## 1. What this changes about the attestation security model

The original design (`02-attestation-protocol.md`) assumed a signed build manifest:

- Operator signs `build_manifest.json` with a Cloud KMS key.
- `mc-tunnel` verifies the manifest at startup; checks parent process SHA256 against the manifest.
- Control plane verifies binary hash on every attestation.

With code-signing out of scope, the binary-hash chain offers little adversarial value (anyone can rebuild MC and produce a hash). So the v1 simplification is:

| Original design | v1 simplified |
|---|---|
| Operator-signed `build_manifest.json` shipped with each release | Not shipped |
| `mc-tunnel` verifies manifest signature at startup | Skipped |
| `mc-tunnel` SHA256s the parent MC binary and checks against manifest | Skipped |
| Control plane stores per-build hash in `builds/` collection | Replaced with simple `mc_versions` allowlist (string match on `mc_version`) |
| Attestation verifies `mc_binary_sha256` matches registered build | Attestation verifies `mc_version` is in allowlist + `≥ min_supported` |
| Build revocation = revoke a specific hash | Build revocation = remove `mc_version` from allowlist |

**What we keep:**
- Device keypair + signed attestation envelope (still enforced).
- Enrollment token bound to `device_pub` (still enforced).
- **Client-secret signature** (`02-` §3.6) — Ed25519 keypair baked into each `mc-tunnel` release, signs alongside the device key. **This is the v1 platform-binding moat.** A fork of MC can re-implement everything else; it cannot produce a valid `client_signature_b64` without the proprietary `mc-tunnel` binary.
- Control plane verification of both signatures, nonce, timestamp, rate limits, device cap (still enforced).
- Revocation paths (per-device, per-account; per-version replaces per-build; per-client-key for compromise response).

**What we lose:**
- "This MC binary is the unmodified one we shipped." A modified MC reporting `mc_version: 1.4.2` will be accepted by the control plane as long as `1.4.2` is in the allowlist **and the modified MC was able to produce a valid `client_signature_b64`** — which requires the proprietary `mc-tunnel` binary running alongside. Forking the open MC core does NOT compromise platform access.
- Counter-argument: in v1 with no code-signing, the binary-hash guarantee was always weak — anyone can rebuild from source and produce a hash. The client-secret signature replaces that guarantee with a stronger one (you need to extract a baked-in private key from a stripped Rust binary, not just rebuild from source).

**When does this matter?** It matters when an adversary either (a) extracts `CLIENT_SECRET_PRIV` from a released `mc-tunnel` (rotated per release; revoked on detection), or (b) ships a modified open MC and tries to abuse `*.PLATFORM_DOMAIN`. Defense for (b) is covered. Defense for (a) is "rotate fast; revoke immediately; rely on `04-abuse-prevention.md` Worker caps to bound damage in the meantime."

When code-signing comes back into scope, restore the build-manifest flow described in earlier drafts. The protocol envelope (`02-` §7) already carries `build_manifest_id` and `mc_binary_sha256` as reserved nullable fields; turning enforcement on later doesn't break anything.

---

## 1.5 Two binaries, two licenses, two pipelines

The open-core decision (`07-licensing.md`) splits the release process into two coordinated streams:

### Stream A — MC core (open source, public)

- **What:** the existing PyInstaller build of MC (server, agent system, dashboard).
- **License:** open source (MIT or Apache 2.0 — see `07-`).
- **Repo:** `mission-control/` (public).
- **Pipeline:** existing `pre_build_fix.py` + `pyinstaller build.spec` flow. Unchanged.
- **Output:** `dist/MissionControl/MissionControl.exe` + assets.
- **Anyone can build it.** Forks are explicitly OK; that's the point of open source.

### Stream B — `mc-tunnel` + `mc_remote` glue + bundled `cloudflared` (proprietary, private)

- **What:** the small Rust `mc-tunnel` binary, the `mc_remote/` Python integration module that talks to it, **and a vendored copy of Cloudflare's official `cloudflared` binary**.
- **License:** "All rights reserved" for our code; **Apache 2.0 for `cloudflared`** (Cloudflare permits redistribution; LICENSE shipped alongside).
- **Repo:** `mc-remote/` (private, separate from MC core).
- **Pipeline:** `cargo build --release` for the Rust binary; the Python glue ships as a wheel or `.pyd` (Cython-compiled) so the client secret is not trivially `cat`-able; `cloudflared.exe` is **fetched + verified** at build time (see §1.5.1).
- **Output:** `mc-tunnel.exe` + Python integration wheel + `mc_tunnel/bin/cloudflared.exe` + `mc_tunnel/bin/cloudflared-LICENSE.txt`.
- **Each release embeds a fresh `CLIENT_SECRET_PRIV`** (`02-` §3.6). The corresponding pubkey is registered with the control plane via `POST /v1/admin/client_keys` at release time.
- **Only the operator can build it.** Forks of MC core do not get access.

#### 1.5.1 Bundling cloudflared (the third-party binary)

We ship Cloudflare's official `cloudflared` so the user never has to install anything. `mc_remote/cloudflared.py:find_binary()` looks at `mc_tunnel/bin/cloudflared.exe` *first*, before falling back to PATH lookup.

**Build pipeline step (Windows):**

```powershell
# 1. Download (latest stable; pin a specific version for reproducible builds)
Invoke-WebRequest `
  -Uri https://github.com/cloudflare/cloudflared/releases/download/2026.3.0/cloudflared-windows-amd64.exe `
  -OutFile mc_tunnel\bin\cloudflared.exe

# 2. Verify Authenticode signature — ties the binary to Cloudflare's
#    verified org identity. Stronger than a SHA-256 file (which CF doesn't
#    publish for Windows) because it transitively trusts DigiCert's CA.
$sig = Get-AuthenticodeSignature mc_tunnel\bin\cloudflared.exe
if ($sig.Status -ne 'Valid' -or $sig.SignerCertificate.Subject -notmatch 'Cloudflare') {
    throw "cloudflared.exe is not validly signed by Cloudflare — refusing to bundle"
}

# 3. (Optional belt-and-suspenders) Pin SHA-256 in build_meta and fail if it drifts
$h = (Get-FileHash mc_tunnel\bin\cloudflared.exe -Algorithm SHA256).Hash.ToLower()
if ($h -ne $env:CLOUDFLARED_SHA256_PIN) {
    throw "cloudflared SHA mismatch — expected $env:CLOUDFLARED_SHA256_PIN got $h"
}

# 4. Ship the LICENSE alongside
Invoke-WebRequest `
  -Uri https://raw.githubusercontent.com/cloudflare/cloudflared/master/LICENSE `
  -OutFile mc_tunnel\bin\cloudflared-LICENSE.txt
```

**For macOS / Linux releases (when those platforms come into scope):**

| Asset | Path on disk |
|---|---|
| `cloudflared-darwin-amd64.tgz` (extract `cloudflared`) | `mc_tunnel/bin/darwin-amd64/cloudflared` |
| `cloudflared-darwin-arm64.tgz` (extract `cloudflared`) | `mc_tunnel/bin/darwin-arm64/cloudflared` |
| `cloudflared-linux-amd64` | `mc_tunnel/bin/linux-amd64/cloudflared` |
| `cloudflared-linux-arm64` | `mc_tunnel/bin/linux-arm64/cloudflared` |

`find_binary()` is currently Windows-only; extend it to pick the right per-platform path when those platforms ship.

**Why bundle instead of auto-download on first run:**
- Removes a network-dependent step from the user's first-run UX (corporate firewalls, captive portals, offline first-run all break auto-download)
- Removes ~one class of attack surface (compromised CDN / DNS poisoning) that we'd otherwise need to defend with hash pinning at runtime
- Adds ~30 MB to the release zip — acceptable for a desktop app
- Industry-standard pattern (Tailscale GUI, 1Password, Sentry CLI, ngrok desktop all do it)

**Updating cloudflared:** see `mc_tunnel/bin/README.md` for the per-release recipe. Update on every MC release, or sooner if a CF security advisory drops.

**Source-tree state:** `mc_tunnel/bin/cloudflared.exe` is `.gitignore`'d (64 MB binary, fetched at build time). `mc_tunnel/bin/README.md` and `cloudflared-LICENSE.txt` ARE committed.

### Combined release

The end-user-facing release zip is the union of A and B:

```
MissionControl-<version>-win64.zip
└── MissionControl/
    ├── MissionControl.exe          # Stream A, open source build, unsigned
    ├── mc-tunnel.exe               # Stream B, proprietary, unsigned
    ├── mc_remote/                  # Stream B, proprietary Python wheel, unsigned
    ├── _internal/                  # PyInstaller deps
    ├── static/
    └── ...
```

Stream A users who don't want platform features can build MC core themselves and skip Stream B; they get a fully working local-only MC. Stream B is what makes `clayrune.io` work.

---

## 2. v1 release output

A release is just the combined zip (per §1.5) plus a tiny `release-meta.json` for the operator to use during registration:

```json
{
  "mc_version": "1.4.2",
  "mc_tunnel_version": "1.0.0",
  "client_secret_key_id": "mc-tunnel-2026a",
  "git_sha_mc": "a3b4c5d6e7f8...",
  "git_sha_mc_tunnel": "1f2e3d4c5b6a...",
  "built_at": "2026-04-27T18:42:13Z",
  "platforms": ["win32"]
}
```

No build manifest. No KMS-signed binary. The only signing in v1 is the per-release `mc-tunnel` client-secret pubkey registration (which doesn't sign anything itself; it identifies what the embedded private key signs).

---

## 3. Build steps (manual, v1)

Until CI is wired up, this is the operator's local procedure. Two streams, then a join.

### 3.1 Generate a fresh client-secret keypair (Stream B, once per release)

```bash
# In the proprietary mc-remote repo, NOT in the public mission-control repo.
# Generates a fresh Ed25519 keypair; commits ONLY the pubkey + key_id metadata
# to the public mc-tunnel registration log; the private half is embedded
# directly into the Rust binary via an env var or build script and never
# touches public source.

scripts/gen_client_key.sh mc-tunnel-2026a
   → outputs:
       mc_tunnel/src/client_secret.rs    # contains 32-byte CLIENT_SECRET_PRIV
       client_keys/mc-tunnel-2026a.pub   # base64 pubkey, safe to commit
```

The private key is then **deleted from disk** after the build embeds it; the only place `CLIENT_SECRET_PRIV` exists in human-readable form is the in-memory build-script variable. Once the binary is compiled, the source file is overwritten with a stub. (Specific automation for this lives in `scripts/gen_client_key.sh`; not in this doc.)

### 3.2 Build both streams

```bash
# Stream A: MC core (existing flow, unchanged from BUILD_INSTRUCTIONS.md)
python pre_build_fix.py
pyinstaller build.spec --noconfirm
   → dist/MissionControl/MissionControl.exe

# Stream B: proprietary mc-tunnel (with embedded client secret from §3.1)
cargo build --release --manifest-path mc_tunnel/Cargo.toml
   → mc_tunnel/target/release/mc-tunnel.exe

# Stream B: proprietary mc_remote Python glue (Cython-compiled wheel)
python -m build --wheel --outdir dist/mc_remote_dist mc_remote/
```

### 3.3 Stage and zip

```bash
copy mc_tunnel/target/release/mc-tunnel.exe   dist/MissionControl/
copy dist/mc_remote_dist/*.whl                dist/MissionControl/_internal/wheels/
Compress-Archive -Path dist/MissionControl -DestinationPath dist/MissionControl-1.4.2-win64.zip
```

### 3.4 Register with the control plane (two POSTs)

```bash
# A. Register the mc_version (allowlist entry)
curl -X POST https://api.clayrune.io/v1/admin/versions \
  -H "Authorization: Bearer <operator-jwt>" \
  -H "Idempotency-Key: ver-1.4.2" \
  -H "Content-Type: application/json" \
  -d '{ "mc_version": "1.4.2", "min_protocol": 1, "released_at": "2026-04-27T18:42:13Z" }'

# B. Register the client-secret PUBLIC key for this release
curl -X POST https://api.clayrune.io/v1/admin/client_keys \
  -H "Authorization: Bearer <operator-jwt>" \
  -H "Idempotency-Key: ck-mc-tunnel-2026a" \
  -H "Content-Type: application/json" \
  -d "{
        \"key_id\": \"mc-tunnel-2026a\",
        \"pubkey_b64\": \"$(cat client_keys/mc-tunnel-2026a.pub)\",
        \"mc_tunnel_version\": \"1.0.0\",
        \"released_at\": \"2026-04-27T18:42:13Z\"
      }"
```

### 3.5 Distribute

The zip is distributed via whatever channel MC currently uses (out of scope for this doc — install flow is unresolved per the original scope decision).

That's the whole v1 build procedure for remote access purposes. The only deltas from today's MC build are: (a) a parallel Rust build, (b) a fresh client-keypair generation, and (c) two `curl` calls to the control plane.

---

## 4. Control plane changes (now applied)

The simplified version-only flow + open-core changes have been applied to the protocol and API docs:

- ✅ `POST /v1/admin/builds` → `POST /v1/admin/versions` (deprecated alias kept for one transitional release).
- ✅ `builds/{build_manifest_id}` Firestore collection → `versions/{mc_version}`.
- ✅ New: `POST /v1/admin/client_keys` and `client_secret_keys/{key_id}` collection.
- ✅ Attestation verification step 7: `mc_version` in `versions` and not revoked.
- ✅ Attestation verification step 8: removed (binary hash check).
- ✅ Attestation verification step 4.5 added: `client_signature_b64` valid under active platform key.
- ✅ Attestation envelope carries `client_secret_key_id`; wrapper carries `client_signature_b64`.
- ✅ `mc-tunnel` no longer ships a `build_manifest.json`.

See `02-attestation-protocol.md` §3.6 + §7 + §10 and `03-control-plane-api.md` §3.7 + §3.14 + §4.3 + §4.3a for the canonical text.

---

## 5. When to revisit code-signing

Triggers to bring code-signing back into scope:

- Install flow becomes a designed UX (rather than "send the user a zip and ask them to extract it"). Once installation is intentional, SmartScreen warnings start mattering.
- Audience widens beyond technical early adopters who'll click through warnings.
- An incident or near-miss involves a modified MC variant being used against the platform.
- Microsoft Store distribution becomes feasible (it provides Microsoft-issued signing for free, but requires MSIX packaging and Store policy compatibility — currently uncertain whether MC's terminal pop-out / agent-execution model can pass Store review).

When that happens, restore: KMS build-attestation key, signed `build_manifest.json`, `mc-tunnel` parent-binary hashing, control plane `builds` collection. The protocol fields are already reserved.

---

## 6. Cross-references

- Why this exists: `02-attestation-protocol.md` §4 (originally — see §1 above for what's now skipped)
- Where the version is registered: `03-control-plane-api.md` §3.14 (rename `/v1/admin/builds` → `/v1/admin/versions` pending)
- Local build instructions: existing `BUILD_INSTRUCTIONS.md`
- Scope decision: `feedback_no_paid_code_signing.md` (memory)
