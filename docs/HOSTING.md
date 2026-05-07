# Hosting clayrune.io on Cloudflare Pages

The Clayrune installer is designed to be served from `clayrune.io` so users
get short, memorable install commands:

```sh
curl -sSL https://clayrune.io/install.sh | sh
```

Until the domain is live, testing uses raw GitHub URLs via the
`CLAYRUNE_PROMPT_URL` env var. This doc covers the steps to switch to the
real domain, which we've decided to host on **Cloudflare Pages** (free,
GitHub-integrated, same dashboard as the existing Cloudflare Tunnel for
mobile remote access).

## What gets deployed

The contents of the `installer/` directory in the repo, served at the root
of `clayrune.io`:

```
clayrune.io/                    →  installer/index.html        (landing page)
clayrune.io/install.sh          →  installer/install.sh        (macOS/Linux bootstrap)
clayrune.io/install.ps1         →  installer/install.ps1       (Windows bootstrap)
clayrune.io/install-prompt.md   →  installer/install-prompt.md (the prescriptive prompt)
clayrune.io/start.sh            →  installer/start.sh          (Linux launcher template)
clayrune.io/start.command       →  installer/start.command     (macOS launcher template)
clayrune.io/start.bat           →  installer/start.bat         (Windows launcher template)
clayrune.io/clayrune.png        →  installer/clayrune.png      (mascot icon, copied for the landing page)
```

The `_headers` file in `installer/` ensures install.sh / install.ps1 /
install-prompt.md are served as `text/plain` (so curling them works AND
viewing them in a browser doesn't trigger a download or render as HTML).

## One-time setup (≈10 minutes)

### 1. Domain registration

Register `clayrune.io` (or use whichever Clayrune-related domain you have).
If it's already registered through another registrar (e.g. Namecheap), you
can either:
- **Transfer DNS to Cloudflare** (recommended — gives you Cloudflare Pages
  zero-config domain attach + free TLS), OR
- **Point an A/CNAME at Cloudflare Pages** while keeping DNS elsewhere.

For DNS-via-Cloudflare:
1. Cloudflare dashboard → Add a Site → enter `clayrune.io`.
2. Cloudflare scans your existing DNS records. Approve.
3. Update nameservers at your current registrar to the two Cloudflare
   nameservers shown.
4. Wait for propagation (usually < 1 hour).

### 2. Cloudflare Pages project

1. Cloudflare dashboard → Workers & Pages → Create → Pages → Connect to Git.
2. Authorize Cloudflare to read your GitHub repos, pick `ronle/mission-control`.
3. Configure build:
   - **Project name**: `clayrune`
   - **Production branch**: `master`
   - **Build command**: *(leave blank — pure static, no build step)*
   - **Build output directory**: `installer`
4. Click "Save and Deploy". First deploy takes ~30 seconds.
5. Once green, visit the auto-assigned `*.pages.dev` URL to verify the
   landing page renders + `https://<your-project>.pages.dev/install.sh`
   returns the bootstrap.

### 3. Custom domain

In the Pages project settings → Custom domains → Set up a custom domain →
enter `clayrune.io`. Cloudflare creates the necessary CNAME records.
TLS provisioned automatically.

A few minutes later, `https://clayrune.io` resolves to the landing page
and `https://clayrune.io/install.sh` returns the bootstrap.

### 4. Verify the live install

On any clean machine:

```sh
# macOS/Linux
curl -sSL https://clayrune.io/install.sh | sh

# Windows PowerShell
iwr https://clayrune.io/install.ps1 -useb | iex
```

The bootstrap fetches `https://clayrune.io/install-prompt.md` by default
(no env var override needed).

## Auto-deploy on every push to master

Cloudflare Pages auto-redeploys whenever you push to `master`. Whatever's
in `installer/` at HEAD becomes the live site within ~30 seconds. So
updating the install prompt = `git push`. No build step, no CDN purge.

For PR previews (Cloudflare assigns a unique URL per branch/PR), enable
"preview deployments" in Pages settings. Useful when iterating on the
prompt or landing page.

## What's NOT deployed

The rest of the repo (`server.py`, `static/index.html`, `data/`, etc.)
is the application itself — that gets installed locally on the user's
machine by the bootstrap. Only `installer/` is the public-facing site.

## Troubleshooting

### `clayrune.io/install.sh` returns the file as HTML / triggers a download

Check that `_headers` is present in the build output. Cloudflare Pages reads
`_headers` from the output directory (`installer/`), so the file must be at
`installer/_headers` in the repo.

### Landing page shows a broken icon

The mascot image is at `installer/clayrune.png` (copied from `assets/clayrune.png`
during repo setup). If the deploy doesn't include it, re-copy and push.

### DNS hasn't propagated

`dig clayrune.io NS` should return Cloudflare nameservers (e.g.
`bob.ns.cloudflare.com`). If not, recheck the nameserver update at your
registrar.

### Bootstrap fails after the domain is live

The bootstrap respects `CLAYRUNE_PROMPT_URL` env var. If `clayrune.io`
is having issues, point the env var at the GitHub raw URL to keep
testing while you debug:

```sh
CLAYRUNE_PROMPT_URL=https://raw.githubusercontent.com/ronle/mission-control/master/installer/install-prompt.md \
  curl -sSL https://raw.githubusercontent.com/ronle/mission-control/master/installer/install.sh | sh
```

## Future enhancements

- **Versioned install prompts**: serve `install-prompt-v1.md`,
  `install-prompt-v2.md` etc. so updates can ship without breaking
  in-flight installs. The bootstrap would default to the latest version
  and accept a pinned override.
- **Telemetry**: count installs (privacy-respecting; just an aggregate
  counter via Cloudflare Analytics on the install.sh request).
- **Multi-domain**: `clayrune.com`, `clayrune.dev` could redirect to
  `clayrune.io` via Cloudflare's bulk redirects.
