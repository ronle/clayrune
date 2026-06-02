# Hosted Clayrune — POC Runbook (one host, many containers)

**Status:** sketch (2026-06-01). Companion to
`docs/HOSTED_CLOUD_PLATFORM_DESIGN.md` §3.1 (POC topology). This is the concrete
"how to stand it up" for the small-scale POC: **one rented host, a container per
trusted tester, local volume for live work, an object-storage bucket behind it
for durability.** Not production — adjust versions/pins before any real use.

Grounded in the actual MC launch shape:
- MC is a Flask app started with `python server.py`, binds `:5199`
  (`installer/start.sh`).
- Data dir is env-overridable: `MC_DATA_DIR` (`server.py` `_resolve_dirs`) — so a
  mounted volume + `MC_DATA_DIR=/data` is all it takes.
- `server.py` is headless-safe — it does **not** import `pywebview`/`pythonnet`
  (those are the desktop launcher's deps), so the container drops them.
- MC dispatches the **Claude Code CLI**, so the image also needs Node + the
  `claude` CLI + `git` on PATH.

---

## 1. The container image

`server.py` runs headless, so the image is small. Drop the desktop/.NET deps.

```dockerfile
# docs/poc/Dockerfile — POC MC container (Linux, headless). Sketch; pin for real use.
FROM python:3.11-slim

# System deps: git (project repos) + Node (the Claude Code CLI MC dispatches)
RUN apt-get update && apt-get install -y --no-install-recommends \
      git curl ca-certificates && \
    curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && \
    apt-get install -y --no-install-recommends nodejs && \
    rm -rf /var/lib/apt/lists/*

# The binary MC actually launches per turn
RUN npm install -g @anthropic-ai/claude-code

WORKDIR /app
# Slimmed deps: requirements.txt minus pywebview + pythonnet (desktop/.NET only;
# server.py never imports them). Keep: flask, cryptography, keyring, requests,
# rfc8785, pywebpush, Pillow, firebase-admin.
COPY requirements.container.txt requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
ENV MC_DATA_DIR=/data
EXPOSE 5199
CMD ["python", "server.py"]
```

---

## 2. The compose shape (per-user container + reverse proxy)

Each tester = one MC container with its **own** volume + **own** injected key,
behind a reverse proxy that routes `<user>.poc.clayrune.io` → that container's
`:5199`. Single-instance invariant is preserved for free: each container has its
own network namespace, so every MC binds *its own* `localhost:5199` — no
collision, no code change.

```yaml
# docs/poc/docker-compose.yml — sketch
services:
  caddy:                          # TLS + per-user routing (CF Access sits in front of this)
    image: caddy:2
    ports: ["443:443"]
    volumes:
      - ./Caddyfile:/etc/caddy/Caddyfile
      - caddy_data:/data

  mc-alice:
    build: .
    environment:
      ANTHROPIC_API_KEY: ${ALICE_ANTHROPIC_KEY}   # inject via env — NOT the in-app UI (see §4)
      MC_DATA_DIR: /data
    volumes: ["alice_data:/data"]
    expose: ["5199"]
    # isolation ladder: plain docker now. Untrusted users later → add:
    #   runtime: runsc        # gVisor
    #   (or a Kata/Firecracker runtime) — same topology, stronger walls, no app change.

volumes:
  alice_data:
  caddy_data:
```

```caddyfile
# Caddyfile — one block per tester
alice.poc.clayrune.io { reverse_proxy mc-alice:5199 }
```

Auth: keep **Cloudflare Access** in front of Caddy (reuse the existing
mobile-tokens machinery — the phone presents `CF-Access-Client-*` headers exactly
as today). The reverse proxy only sees already-authenticated requests.

---

## 3. Provision a new tester (e.g. Bob)

1. `docker volume create poc_bob_data` (or declare `bob_data:` in compose).
2. Copy the `mc-alice` block → `mc-bob`, point it at `bob_data`, set
   `BOB_ANTHROPIC_KEY` in your `.env`.
3. Add to the Caddyfile: `bob.poc.clayrune.io { reverse_proxy mc-bob:5199 }`.
4. `docker compose up -d mc-bob && docker compose exec caddy caddy reload`.
5. Add Bob to the CF Access app; send him the URL + his pairing token.

That's the whole "fleet orchestrator" at POC scale — a template + four commands.
(Scale-out: this is exactly what the real orchestrator automates against
microVMs instead of containers.)

---

## 4. Storage — local volume for live, bucket for durable

Do **not** run the live workspace off a bucket-fuse mount (s3fs/gcsfuse): MC
drives git working trees + thousands of small files and git over fuse is slow and
flaky. Instead:

- **Live:** each user's `/data` is a local Docker volume (fast POSIX fs).
- **Durable/cold:** a nightly `rclone`/`restic` sync of each `/data/<user>` →
  an object-storage bucket (S3 / GCS / Cloudflare R2). Cheap, off-host, survives
  the box dying. This *is* the committee's archive-and-detach (design §8
  `[C:S3.2]`), just at POC scale.
- **Restore / migrate:** pull the bucket prefix back into a fresh volume before
  first start (`rclone copy <bucket>:poc/<user> /data`). Same move re-homes a
  user onto a microVM at scale-out.

```bash
# durability sidecar / host cron — sketch
rclone sync /var/lib/docker/volumes/poc_bob_data/_data  r2:clayrune-poc/bob  --fast-list
```

---

## 5. Gotchas (grounded in the code)

- **Inject the key via container env, never the in-app Settings → Agent Providers
  UI.** That UI writes the key *plaintext* to `data/provider_env.json` on the
  volume (`server.py` `_save_provider_env_file`) **and** overrides the injected
  env key (`os.environ[...]=...`). Both break custody — design §5.3 `[C:S2.1]`.
  For the POC, set `ANTHROPIC_API_KEY` in the container env and leave the UI
  field alone.
- **Slim the Python deps.** `requirements.container.txt` = root `requirements.txt`
  minus `pywebview` + `pythonnet` (desktop/.NET; `pythonnet` won't pip-install
  cleanly on slim Linux and is pointless headless).
- **The image needs Node + `claude` + `git` on PATH** — MC shells out to the
  Claude Code CLI per turn; without it every dispatch fails.
- **One MC per container.** The single-instance port bind (`_check_port_conflict`)
  is satisfied per-container via the network namespace; don't try to run two MCs
  in one container.
- **BYOK is free here too** — MC inherits `ANTHROPIC_API_KEY` from the process
  env (design §5, verified `server.py:6938`/`:7033` pass no `env=`). The env var
  in the compose file is the entire wiring.

---

## 6. Scale-out path (when the POC graduates)

The swap is **configuration, not a rewrite** (design §3.1): container → microVM,
one-host → fleet, local-volume → per-VM-volume, docker-compose → fleet
orchestrator, plain-docker → Firecracker/Kata. The in-VM MC core is byte-identical
either way. At that point the deferred design machinery turns on: sleep/wake
(§4.3/§4.4), the external scheduler mirror, the key vault, and the per-user
compute caps (§10.4).
