# Clayrune Installer

A Claude-driven installer. The user runs one command; Claude executes the install.

## Architecture

```
                user runs:
                curl -sSL https://clayrune.io/install.sh | sh
                       │
                       ▼
        ┌─────────────────────────────────┐
        │  install.sh / install.ps1       │  bootstrap (~100 lines)
        │  ──────────────────────────────  │  ── verifies / installs Claude CLI
        │                                  │  ── fetches install-prompt.md
        │                                  │  ── pipes it into:
        │   claude --dangerously-skip-     │
        │           permissions -p "..."   │
        └────────────────┬────────────────┘
                         │
                         ▼
        ┌─────────────────────────────────┐
        │  Claude CLI (the user's own)    │  executes the install prompt
        │                                  │
        │  STEP 1/6  detect environment   │
        │  STEP 2/6  clone or pull repo   │
        │  STEP 3/6  python venv + deps   │
        │  STEP 4/6  node.js              │
        │  STEP 5/6  desktop launcher     │
        │  STEP 6/6  start server +       │
        │            open browser         │
        └────────────────┬────────────────┘
                         │
                         ▼
        ┌─────────────────────────────────┐
        │  Clayrune running at            │
        │  http://localhost:5199          │
        │                                  │
        │  Desktop / Start Menu /         │
        │  Applications has a Clayrune    │
        │  shortcut for relaunching.      │
        └─────────────────────────────────┘
```

## Files in this directory

| File | Purpose |
|---|---|
| `install.sh` | Bootstrap for macOS / Linux. The user runs `curl -sSL https://clayrune.io/install.sh \| sh`. |
| `install.ps1` | Bootstrap for Windows. The user runs `iwr https://clayrune.io/install.ps1 -useb \| iex` from PowerShell. |
| `install-prompt.md` | The actual installer logic, written as a prescriptive prompt for Claude. The bootstrap fetches this and pipes it into `claude --dangerously-skip-permissions`. |
| `start.sh` | Per-user launcher (Linux). Activates `.venv`, starts `python server.py`, opens the browser. The installer registers this as a `.desktop` file in `~/.local/share/applications/`. |
| `start.command` | Per-user launcher (macOS). Same role as `start.sh`. The installer copies it to `~/Applications/Clayrune.command`. |
| `start.bat` | Per-user launcher (Windows). Same role. The installer wraps it in a `.lnk` shortcut on the Desktop and in the Start Menu. |

## Why this design

- **No build pipeline**: zero per-platform installers to compile, sign, and publish. The bootstrap is two ~100-line shell scripts; the install logic is plain Markdown.
- **Cross-platform "for free"**: Claude figures out OS, package manager, and Python/Node install paths. We don't write per-distro shell logic.
- **Self-healing**: when winget hiccups or apt is locked, Claude can diagnose and try a different approach. A scripted installer can't.
- **No bundling, no licensing review**: we never redistribute Claude CLI, Node, Python, or any other dependency.
- **On-brand**: "an AI agent installs the AI-agent operator console." It's a demo as much as an install.

## Disclosure model

The bootstrap clearly prints the exact `claude --dangerously-skip-permissions` line it's about to execute and gives the user 5 seconds to Ctrl-C out before handing off. The install prompt is publicly hosted at `https://clayrune.io/install-prompt.md` so anyone can audit what they're authorizing before running the bootstrap. The prompt is conservative: it does `git`, `pip install`, package-manager calls, and launches the app. It does NOT modify dotfiles, change the system PATH, write outside the install dir, or run `sudo` unless absolutely required (and explains why one line earlier).

## Hosting

| URL | What it serves | Source |
|---|---|---|
| `https://clayrune.io/install.sh` | the bootstrap (macOS/Linux) | this repo: `installer/install.sh` |
| `https://clayrune.io/install.ps1` | the bootstrap (Windows) | this repo: `installer/install.ps1` |
| `https://clayrune.io/install-prompt.md` | the install prompt | this repo: `installer/install-prompt.md` |

For testing before the domain is up, the same files can be served from
`https://raw.githubusercontent.com/ronle/mission-control/master/installer/<file>`.
The bootstraps respect a `CLAYRUNE_PROMPT_URL` env var so you can point them at
any URL.

## Testing checklist

A new install on a clean VM should:

- [ ] Complete in under 5 minutes with no manual intervention beyond the initial `curl … | sh`
- [ ] End with the browser open at `http://localhost:5199`
- [ ] Place a clickable launcher on the Desktop and in the OS app menu
- [ ] Survive a re-run (idempotent — clone becomes pull, venv is recreated, deps re-installed)
- [ ] Leave nothing in `/etc`, `/usr`, or system-wide locations
- [ ] Not modify `.bashrc`, `.zshrc`, or system PATH

Run on at least: Windows 11, macOS 14+, Ubuntu 22.04. Each test should start from a snapshot with only Claude CLI pre-installed (or nothing pre-installed — the bootstrap handles that case too).

## Updates

The same model handles updates. After the install, the user can run:

```sh
claude "update Clayrune in ~/Clayrune by running git pull, reinstalling Python deps if requirements.txt changed, and restarting the server"
```

A future enhancement may add `clayrune.io/update.sh` that scripts this more formally.
