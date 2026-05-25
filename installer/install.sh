#!/usr/bin/env sh
# Clayrune installer bootstrap (macOS / Linux)
#
# Usage:
#   curl -sSL https://clayrune.io/install.sh | sh
#
# What this script does:
#   1. Sets up Node 18+ via nvm (needed for Claude CLI itself).
#   2. Installs Claude CLI if missing (Anthropic curl-installer or npm).
#   3. Verifies Claude CLI is authenticated.
#   4. Clones the Clayrune repo to ~/Clayrune.
#   5. Sets up a Python 3.11+ venv + installs dependencies.
#   6. Creates a launcher (~/Applications/Clayrune.command on macOS,
#      ~/.local/share/applications/clayrune.desktop on Linux).
#   7. Launches the server and opens the dashboard in your browser.
#
# Steps 4-7 used to be done by handing off to `claude -p` with a markdown
# install prompt. That broke on newer Claude models because the
# "you are an automated installer, do not ask for confirmation" framing
# is the textbook shape of a prompt-injection attack and Claude refuses
# to run it. The install steps don't need an LLM anyway -- this shell
# script does them directly.
#
# Override:
#   CLAYRUNE_HOME=...        (override default ~/Clayrune install dir)
#   CLAYRUNE_NO_CONFIRM=1    (skip the 5-second abort window)

set -e

# ANSI colors only when stdout is a tty
if [ -t 1 ]; then
  B=$(printf '\033[1m');  R=$(printf '\033[0m')
  C=$(printf '\033[36m'); Y=$(printf '\033[33m')
  G=$(printf '\033[32m'); E=$(printf '\033[31m')
else
  B=''; R=''; C=''; Y=''; G=''; E=''
fi

printf "%s======================================%s\n" "$C" "$R"
printf "%s  Clayrune Installer%s\n" "$B" "$R"
printf "%s======================================%s\n\n" "$C" "$R"

# ── Helpers ────────────────────────────────────────────────────────────────

# Refresh PATH so a freshly-installed `claude` is discoverable without forcing
# the user to start a new shell. Covers the common destinations Anthropic's
# curl-installer and npm-global use.
_refresh_claude_path() {
  for d in "$HOME/.local/bin" "$HOME/.claude/bin" "$HOME/.npm-global/bin" "/usr/local/bin"; do
    case ":$PATH:" in
      *":$d:"*) ;;
      *) [ -d "$d" ] && PATH="$d:$PATH" ;;
    esac
  done
  if command -v npm >/dev/null 2>&1; then
    np=$(npm config get prefix 2>/dev/null || echo '')
    if [ -n "$np" ] && [ -d "$np/bin" ]; then
      case ":$PATH:" in
        *":$np/bin:"*) ;;
        *) PATH="$np/bin:$PATH" ;;
      esac
    fi
  fi
  hash -r 2>/dev/null || true
  export PATH
}

# Print the Node major version on PATH (or "0" if missing/invalid).
_node_major() {
  command -v node >/dev/null 2>&1 || { echo "0"; return; }
  v=$(node --version 2>/dev/null | sed 's/^v//' | cut -d. -f1)
  case "$v" in ''|*[!0-9]*) echo "0" ;; *) echo "$v" ;; esac
}

# Ensure a Node 18+ runtime is on PATH. Strategy: nvm (user-space, no sudo).
# Already-good Node → no-op. Old Node → install Node 20 via nvm and switch
# the current shell + the user's nvm default to it. This DOES install nvm if
# it's missing (modifies ~/.bashrc / ~/.zshrc as nvm's installer normally does).
_setup_node() {
  m=$(_node_major)
  if [ "$m" -ge 18 ] 2>/dev/null; then
    return 0
  fi

  if [ "$m" = "0" ]; then
    printf "%sNode.js not found.%s Need 18+ for Claude CLI.\n" "$Y" "$R"
  else
    printf "%sNode.js v%s found%s — too old for Claude CLI (need 18+).\n" "$Y" "$(node --version 2>/dev/null | sed 's/^v//')" "$R"
  fi
  printf "Setting up Node 20 via nvm (user-space, no sudo)...\n\n"

  # Install nvm if it isn't already
  if [ ! -s "$HOME/.nvm/nvm.sh" ]; then
    if ! command -v curl >/dev/null 2>&1; then
      printf "%scurl is required to install nvm.%s\n" "$E" "$R"
      return 1
    fi
    printf "Installing nvm (sets up ~/.nvm + adds sourcing line to your shell rc)...\n"
    # Let nvm's installer add `[ -s "$NVM_DIR/nvm.sh" ] && . "$NVM_DIR/nvm.sh"`
    # to the user's shell rc so freshly-opened terminals can find `node` and
    # `claude` (which lives under nvm's node bin dir). Suppressing this with
    # PROFILE=/dev/null leaves the user with `command not found` after the
    # bootstrap exits — which trapped us on WSL.
    if ! curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.39.7/install.sh | bash; then
      printf "%snvm install failed.%s\n" "$E" "$R"
      return 1
    fi
  fi

  # Source nvm into this shell
  export NVM_DIR="$HOME/.nvm"
  # shellcheck disable=SC1091
  if [ -s "$NVM_DIR/nvm.sh" ]; then . "$NVM_DIR/nvm.sh"; fi
  if ! command -v nvm >/dev/null 2>&1; then
    printf "%snvm not loaded into this shell. Open a new terminal and re-run.%s\n" "$E" "$R"
    return 1
  fi

  # Ensure every rc file the user might open a shell from picks up nvm.
  # nvm's curl-installer detects the calling shell from $0, which is `bash`
  # when piped through `| bash` (as we do). On macOS the default shell is
  # zsh, so a fresh Terminal tab reads ~/.zshrc — which nvm never touched
  # — and `claude` / `node` are nowhere on PATH. Result: install.sh exits
  # successfully, the user opens a new terminal, runs `claude /login`, and
  # gets "command not found." (Hit on Keegan's MBP 2026-05-25.) Backfill
  # the sourcing line into every rc that exists; idempotent via grep guard.
  _nvm_source_line='export NVM_DIR="$HOME/.nvm"; [ -s "$NVM_DIR/nvm.sh" ] && . "$NVM_DIR/nvm.sh"'
  # On macOS, default shell is zsh — ensure ~/.zshrc exists (a fresh Mac
  # often doesn't have one) so the sourcing line below has somewhere to land.
  if [ "$(uname)" = "Darwin" ] && [ ! -f "$HOME/.zshrc" ]; then
    touch "$HOME/.zshrc"
  fi
  for _rc in "$HOME/.zshrc" "$HOME/.bashrc" "$HOME/.bash_profile" "$HOME/.profile"; do
    [ -f "$_rc" ] || continue
    if ! grep -q 'NVM_DIR' "$_rc" 2>/dev/null; then
      printf '\n# nvm (added by Clayrune installer)\n%s\n' "$_nvm_source_line" >> "$_rc"
      printf "  Added nvm sourcing to %s\n" "$_rc"
    fi
  done

  # Install Node 20, use it now, set as default for future shells
  printf "Installing Node 20 via nvm...\n"
  if ! nvm install 20 >/dev/null 2>&1; then
    printf "%snvm install 20 failed.%s\n" "$E" "$R"
    return 1
  fi
  nvm use 20 >/dev/null 2>&1
  nvm alias default 20 >/dev/null 2>&1
  hash -r 2>/dev/null || true

  m=$(_node_major)
  if [ "$m" -ge 18 ] 2>/dev/null; then
    printf "%sOK%s Node $(node --version)\n\n" "$G" "$R"
    return 0
  fi
  printf "%sNode setup completed but `node --version` still reports v%s.%s\n" "$E" "$m" "$R"
  return 1
}

# Persist the directory containing `claude` into the user's shell rc files
# so a brand-new terminal can find it WITHOUT them having to remember any
# PATH commands. Anthropic's curl-installer drops claude in ~/.local/bin,
# which is on PATH in our script's PATH-refreshed shell but never in a
# fresh login shell. (Anthropic's installer prints its own warning about
# this and tells the user to edit .zshrc; we shouldn't make them do that.)
# Idempotent — guarded by grep so re-runs don't double-append.
_persist_claude_path() {
  cl=$(command -v claude 2>/dev/null) || return 0
  [ -n "$cl" ] || return 0
  dir=$(dirname "$cl")
  # Skip if it's already in a directory every shell already has on PATH
  # (avoids polluting rc files with /usr/local/bin etc.).
  case ":$dir:" in
    *:/usr/local/bin:*|*:/usr/bin:*|*:/bin:*) return 0 ;;
  esac
  if [ "$(uname)" = "Darwin" ] && [ ! -f "$HOME/.zshrc" ]; then
    touch "$HOME/.zshrc"
  fi
  for _rc in "$HOME/.zshrc" "$HOME/.bashrc" "$HOME/.bash_profile" "$HOME/.profile"; do
    [ -f "$_rc" ] || continue
    if ! grep -q "PATH.*$dir" "$_rc" 2>/dev/null; then
      printf '\n# Claude CLI (added by Clayrune installer)\nexport PATH="%s:$PATH"\n' "$dir" >> "$_rc"
      printf "  Added %s to PATH in %s\n" "$dir" "$_rc"
    fi
  done
}

# Returns 0 iff `claude --version` exits 0 AND emits non-empty output.
# This is the *real* working-state check — `command -v claude` only proves a
# binary exists, not that it actually runs (the WSL Node-version mismatch
# scenario produces a `claude` binary that crashes with a SyntaxError on every
# invocation).
#
# Refresh PATH first: a re-run of install.sh may be invoked from a parent
# shell that hasn't sourced the rc files our previous run modified (e.g.
# a fresh `curl | sh` subshell whose parent zsh never sourced ~/.zshrc
# because the user interacted with claude via its absolute path). Without
# this refresh, _validate_claude returns 1, the install loop fires again,
# and Anthropic's installer either re-runs from scratch or hangs prompting
# for overwrite — both are visible as "stuck on Trying Anthropic installer"
# to the user. Refresh resolves the existing install before the loop.
_validate_claude() {
  _refresh_claude_path
  command -v claude >/dev/null 2>&1 || return 1
  out=$(claude --version 2>/dev/null) || return 1
  [ -n "$out" ] || return 1
  return 0
}

# Returns 0 iff Claude CLI is authenticated. Costs a few tokens for users who
# ARE logged in; for users who aren't, the CLI prints the "Not logged in"
# sentinel without calling the API at all. We grep for that sentinel rather
# than relying on exit codes (transient errors / rate limits also non-zero).
_check_claude_auth() {
  out=$(claude -p "ok" --max-turns 1 </dev/null 2>&1 || true)
  if echo "$out" | grep -qiE 'not logged in|please run /login'; then
    return 1
  fi
  return 0
}

# ── Preflight: macOS Xcode Command Line Tools ─────────────────────────────
#
# On a clean macOS, neither `git` nor any other compiler is on PATH until
# the Xcode Command Line Tools are installed. nvm's curl-installer needs
# `git` to clone itself — without CLT it fails opaquely with "You may be on
# a Mac, and need to install the Xcode Command Line Developer Tools."
# (We hit this on Keegan's MBP 2026-05-25.) Detect and bail with a clear
# instruction BEFORE we burn five minutes of the user's time and bandwidth
# in nvm-install land.
if [ "$(uname)" = "Darwin" ]; then
  if ! xcode-select -p >/dev/null 2>&1; then
    printf "%sXcode Command Line Tools are not installed.%s\n\n" "$Y" "$R"
    printf "macOS needs them before nvm / git / Python can be set up.\n\n"
    printf "%sStep 1.%s Run this in your Terminal:\n" "$B" "$R"
    printf "         %sxcode-select --install%s\n\n" "$C" "$R"
    printf "         A system dialog will pop up. Click %sInstall%s and\n" "$B" "$R"
    printf "         wait for the download to finish (~5-10 min on a fast\n"
    printf "         connection). Apple downloads ~1 GB.\n\n"
    printf "%sStep 2.%s Re-run the Clayrune installer:\n" "$B" "$R"
    printf "         %scurl -sSL https://clayrune.io/install.sh | sh%s\n" "$C" "$R"
    exit 1
  fi
fi

# ── Step 0: Ensure Node 18+ is available ───────────────────────────────────

# This must run BEFORE any Claude CLI install attempt because npm-installed
# Claude CLI requires Node 18+ to even parse its own source (uses optional
# chaining etc.). Without this check, we'd hit the WSL/old-nvm trap where
# `npm install -g @anthropic-ai/claude-code` "succeeds" but every invocation
# crashes with `SyntaxError: Unexpected token '?'`.
if ! _setup_node; then
  printf "%sCould not set up a working Node 18+ runtime automatically.%s\n\n" "$E" "$R"
  printf "Please install Node 20+ manually, then re-run:\n"
  printf "  Via nvm:  %scurl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.39.7/install.sh | bash%s\n" "$C" "$R"
  printf "            %snvm install 20 && nvm alias default 20%s\n" "$C" "$R"
  printf "  Via apt:  %ssudo apt-get install -y nodejs npm%s (Ubuntu 22.04+ has Node 18)\n" "$C" "$R"
  printf "  Direct:   %shttps://nodejs.org/%s\n" "$C" "$R"
  exit 1
fi

# ── Step 1: Ensure a working Claude CLI ────────────────────────────────────

# Skip install entirely if a working claude is already on PATH.
if _validate_claude; then
  CLAUDE_VERSION=$(claude --version 2>&1 | head -n1 || echo "unknown")
  printf "%sOK%s Claude CLI already installed: %s\n\n" "$G" "$R" "$CLAUDE_VERSION"
else
  if command -v claude >/dev/null 2>&1; then
    printf "%sFound 'claude' on PATH but it doesn't run cleanly.%s\n" "$Y" "$R"
    printf "Will attempt a clean reinstall.\n\n"
  else
    printf "%sClaude CLI not found. Attempting to install...%s\n\n" "$Y" "$R"
  fi

  installed=0

  # Method 1: Anthropic's official installer. Self-contained — bundles its own
  # runtime, sidesteps Node version mismatches (the failure mode we hit on WSL
  # where npm + nvm + system Node disagreed).
  # Pipe to bash explicitly: their installer uses bash-only syntax (subshell
  # `(...)` constructs etc.) that dash on Ubuntu chokes on with `Syntax error`.
  if [ "$installed" -eq 0 ] && command -v curl >/dev/null 2>&1 && command -v bash >/dev/null 2>&1; then
    printf "Trying Anthropic's official installer (curl)...\n"
    if curl -fsSL https://claude.ai/install.sh | bash; then
      _refresh_claude_path
      if _validate_claude; then
        printf "%s✓ Anthropic installer succeeded%s\n\n" "$G" "$R"
        _persist_claude_path
        installed=1
      else
        printf "%s✗ Installer ran but 'claude --version' doesn't work; trying next method...%s\n\n" "$Y" "$R"
      fi
    else
      printf "%s✗ Anthropic installer failed; trying next method...%s\n\n" "$Y" "$R"
    fi
  fi

  # Method 2: npm. Some users have an existing Node setup where npm is the
  # path of least resistance. We validate after — npm "succeeding" without a
  # working binary is exactly the WSL case.
  if [ "$installed" -eq 0 ] && command -v npm >/dev/null 2>&1; then
    printf "Trying npm install -g @anthropic-ai/claude-code...\n"
    if npm install -g @anthropic-ai/claude-code; then
      _refresh_claude_path
      if _validate_claude; then
        printf "%s✓ npm install succeeded%s\n\n" "$G" "$R"
        _persist_claude_path
        installed=1
      else
        printf "%s✗ npm completed but 'claude --version' doesn't work%s\n" "$Y" "$R"
        printf "  (often a Node version / nvm mismatch; the curl-installer above is more reliable).\n\n"
      fi
    else
      printf "%s✗ npm install failed%s\n\n" "$Y" "$R"
    fi
  fi

  if [ "$installed" -eq 0 ]; then
    printf "\n%sCould not install a working Claude CLI automatically.%s\n\n" "$E" "$R"
    printf "Manual install options:\n"
    printf "  Anthropic:  %scurl -fsSL https://claude.ai/install.sh | sh%s\n" "$C" "$R"
    printf "  npm:        %snpm install -g @anthropic-ai/claude-code%s\n" "$C" "$R"
    printf "  Docs:       https://docs.anthropic.com/claude-code\n\n"
    printf "After installing, verify with: %sclaude --version%s\n" "$C" "$R"
    printf "Then re-run: %scurl -sSL https://clayrune.io/install.sh | sh%s\n" "$C" "$R"
    exit 1
  fi

  CLAUDE_VERSION=$(claude --version 2>&1 | head -n1 || echo "unknown")
  printf "%sOK%s Claude CLI: %s\n\n" "$G" "$R" "$CLAUDE_VERSION"
fi

# ── Step 1.5: Verify Claude CLI is authenticated ───────────────────────────

# A freshly-installed CLI isn't logged in. Without this check, the install
# prompt gets handed off to a CLI that responds with "Not logged in · Please
# run /login" and silently exits. Catch that here and tell the user clearly.
printf "Checking Claude CLI authentication...\n"
# CI override: skip the OAuth check so smoke tests can exercise the later
# steps (Python/uv setup, venv, launcher). Production install never sets
# this; documented in .github/workflows/install-smoke.yml.
if [ -n "${CLAYRUNE_SKIP_AUTH_CHECK:-}" ]; then
  printf "%sSkip%s auth check (CLAYRUNE_SKIP_AUTH_CHECK set)\n\n" "$Y" "$R"
elif ! _check_claude_auth; then
  # Resolve the actual claude binary path so the user can run it directly,
  # without having to fight PATH in a new terminal. Anthropic's installer
  # puts claude in ~/.local/bin which isn't on a fresh shell's PATH by
  # default (Anthropic prints its own warning, but users don't always act
  # on it). Using the absolute path here means Step 2 just works regardless
  # of which shell rc has or hasn't been sourced.
  _claude_abs=$(command -v claude 2>/dev/null || echo "claude")
  printf "\n%sClaude CLI is installed but not authenticated.%s\n\n" "$Y" "$R"
  printf "%sStep 1.%s Log in to Claude (run this exact command — uses absolute path,\n" "$B" "$R"
  printf "         no PATH or new-terminal dance needed):\n"
  printf "         %s%s /login%s\n" "$C" "$_claude_abs" "$R"
  printf "         (Follow the OAuth prompts, or paste an Anthropic API key.)\n"
  printf "         (After you see \"Logged in\", type %sexit%s or press Ctrl+D to leave the Claude REPL.)\n\n" "$C" "$R"
  printf "%sStep 2.%s Re-run this installer:\n" "$B" "$R"
  printf "         %scurl -sSL https://clayrune.io/install.sh | sh%s\n" "$C" "$R"
  exit 1
else
  printf "%sOK%s Authenticated\n\n" "$G" "$R"
fi

# ── Direct deterministic install (no Claude handoff) ──────────────────────
#
# We previously fetched install-prompt.md and asked Claude to run the install
# steps via `claude --dangerously-skip-permissions -p "<24KB markdown>"`.
# That broke for two reasons:
#   1. The 24 KB user-message-styled "you are an automated installer, do not
#      ask for confirmation" prompt is the textbook shape of a prompt-injection
#      attack. Newer Claude models flag it and refuse, then exit 0 — leaving
#      the wrapper to mistakenly declare success.
#   2. None of the steps actually need an LLM. git clone, venv setup,
#      pip install, launcher creation, and starting the server are all
#      deterministic shell commands. The shell is already running on the
#      user's machine; we don't need to ask Claude permission to run what
#      we wrote ourselves.
# So we skip Claude entirely from here. Clayrune still uses Claude AT RUNTIME
# (that's the product), but installing Clayrune doesn't.

INSTALL_DIR="${CLAYRUNE_HOME:-$HOME/Clayrune}"
REPO_URL="https://github.com/ronle/mission-control.git"

# Detect OS — macOS and Linux take different paths for venv install + launcher.
case "$(uname)" in
  Darwin) OS="macos" ;;
  Linux)  OS="linux" ;;
  *)
    printf "%sUnsupported OS: %s%s\n" "$E" "$(uname)" "$R"
    exit 1
    ;;
esac

printf "%s──────────────────────────────────────%s\n" "$Y" "$R"
printf "%sAbout to install Clayrune to:%s\n" "$B" "$R"
printf "  %s%s%s\n" "$C" "$INSTALL_DIR" "$R"
printf "Steps: clone repo, set up Python venv, create launcher, start dashboard.\n"
printf "%s──────────────────────────────────────%s\n\n" "$Y" "$R"

if [ -z "${CLAYRUNE_NO_CONFIRM:-}" ]; then
  printf "Press Ctrl+C in the next 5 seconds to abort, or wait...\n"
  sleep 5
fi
printf "\n"

# ── [STEP 1/5] Clone or update the repository ─────────────────────────────
printf "%s[STEP 1/5]%s Cloning repository...\n" "$B" "$R"
if [ -d "$INSTALL_DIR" ]; then
  if [ -d "$INSTALL_DIR/.git" ]; then
    printf "  Existing checkout at %s — pulling latest.\n" "$INSTALL_DIR"
    if ! git -C "$INSTALL_DIR" pull --ff-only; then
      printf "%s[STEP 1/5] FAIL%s git pull failed\n" "$E" "$R"
      exit 2
    fi
  else
    printf "%s[STEP 1/5] FAIL%s %s exists but is not a git checkout.\n" "$E" "$R" "$INSTALL_DIR"
    printf "          Remove it or set CLAYRUNE_HOME to a different path, then re-run.\n"
    exit 2
  fi
else
  if ! command -v git >/dev/null 2>&1; then
    printf "  git not found. Attempting auto-install...\n"
    if [ "$OS" = "linux" ]; then
      if command -v apt-get >/dev/null 2>&1; then
        sudo apt-get update -qq >/dev/null 2>&1 || true
        sudo apt-get install -y -qq git || true
      elif command -v dnf >/dev/null 2>&1; then
        sudo dnf install -y -q git || true
      elif command -v pacman >/dev/null 2>&1; then
        sudo pacman -S --noconfirm git || true
      fi
    elif [ "$OS" = "macos" ]; then
      printf "%s[STEP 1/5] FAIL%s git not on PATH. Run %sxcode-select --install%s, then re-run.\n" "$E" "$R" "$C" "$R"
      exit 2
    fi
    if ! command -v git >/dev/null 2>&1; then
      printf "%s[STEP 1/5] FAIL%s could not auto-install git\n" "$E" "$R"
      exit 2
    fi
  fi
  if ! git clone "$REPO_URL" "$INSTALL_DIR"; then
    printf "%s[STEP 1/5] FAIL%s git clone failed\n" "$E" "$R"
    exit 2
  fi
fi
printf "%s[STEP 1/5] OK%s\n\n" "$G" "$R"

# ── [STEP 2/5] Python 3.11+ + venv + dependencies ─────────────────────────
printf "%s[STEP 2/5]%s Setting up Python 3.11+...\n" "$B" "$R"

_find_python() {
  for cmd in python3.12 python3.11 python3 python; do
    command -v "$cmd" >/dev/null 2>&1 || continue
    ver=$("$cmd" --version 2>&1 | grep -oE '[0-9]+\.[0-9]+' | head -1)
    [ -n "$ver" ] || continue
    major=$(echo "$ver" | cut -d. -f1)
    minor=$(echo "$ver" | cut -d. -f2)
    case "$major" in ''|*[!0-9]*) continue ;; esac
    case "$minor" in ''|*[!0-9]*) continue ;; esac
    if [ "$major" -gt 3 ] 2>/dev/null; then echo "$cmd"; return 0; fi
    if [ "$major" -eq 3 ] && [ "$minor" -ge 11 ] 2>/dev/null; then echo "$cmd"; return 0; fi
  done
  return 1
}

PYTHON_CMD=$(_find_python || true)
if [ -z "$PYTHON_CMD" ]; then
  printf "  Python 3.11+ not found. Attempting auto-install...\n"
  if [ "$OS" = "linux" ]; then
    if command -v apt-get >/dev/null 2>&1; then
      sudo apt-get update -qq >/dev/null 2>&1 || true
      # python3.11 may not be in default repos on older Ubuntu — fall back to
      # whatever python3 + venv the distro provides. Ubuntu 22.04 ships 3.10;
      # the version check below will reject if too old, then user installs
      # manually.
      sudo apt-get install -y -qq python3.11 python3.11-venv python3-pip 2>/dev/null || \
        sudo apt-get install -y -qq python3 python3-venv python3-pip || true
    elif command -v dnf >/dev/null 2>&1; then
      sudo dnf install -y -q python3.11 python3-pip 2>/dev/null || \
        sudo dnf install -y -q python3 python3-pip || true
    elif command -v pacman >/dev/null 2>&1; then
      sudo pacman -S --noconfirm python python-pip || true
    fi
  elif [ "$OS" = "macos" ]; then
    if command -v brew >/dev/null 2>&1; then
      brew install python@3.11 || brew install python@3.12 || true
    else
      # No Homebrew — fall back to uv (Astral). uv ships a fully self-
      # contained CPython into ~/.local/share/uv/python/, with no sudo, no
      # brew, and no Xcode rebuild. Total cost is ~30 sec of download +
      # extract. Tried this path on Keegan's MBP after Step 2 bailed with
      # "Homebrew not found. Install from https://brew.sh, then re-run."
      # — telling a user to leave the installer and go install brew defeats
      # the whole "smooth install" goal.
      printf "  Homebrew not found; installing Python via uv (Astral)...\n"
      if curl -LsSf https://astral.sh/uv/install.sh | sh >/dev/null 2>&1; then
        export PATH="$HOME/.local/bin:$PATH"
        if command -v uv >/dev/null 2>&1; then
          uv python install 3.12 >/dev/null 2>&1 || \
            uv python install 3.11 >/dev/null 2>&1 || true
          # uv python find prints the absolute path of the installed
          # interpreter — use that directly as PYTHON_CMD instead of
          # relying on PATH discovery (uv doesn't add a python3.12 shim).
          PYTHON_CMD=$(uv python find 3.12 2>/dev/null || \
                       uv python find 3.11 2>/dev/null || \
                       echo "")
        fi
      fi
      if [ -z "$PYTHON_CMD" ]; then
        printf "  uv install failed or produced no usable Python.\n"
      fi
    fi
  fi
  # Only re-discover when we didn't get an explicit path (uv branch sets one
  # directly; brew/apt rely on PATH).
  if [ -z "$PYTHON_CMD" ]; then
    PYTHON_CMD=$(_find_python || true)
  fi
fi

if [ -z "$PYTHON_CMD" ]; then
  printf "%s[STEP 2/5] FAIL%s Python 3.11+ not found and could not auto-install.\n" "$E" "$R"
  printf "          Install manually then re-run:\n"
  printf "            %sUbuntu/Debian:  sudo apt install python3.11 python3.11-venv%s\n" "$C" "$R"
  printf "            %sFedora/RHEL:    sudo dnf install python3.11%s\n" "$C" "$R"
  printf "            %smacOS (uv):     curl -LsSf https://astral.sh/uv/install.sh | sh && uv python install 3.12%s\n" "$C" "$R"
  printf "            %smacOS (brew):   brew install python@3.11%s\n" "$C" "$R"
  exit 2
fi
printf "  Using: %s ($("$PYTHON_CMD" --version 2>&1))\n" "$PYTHON_CMD"

VENV_DIR="$INSTALL_DIR/.venv"
if [ ! -x "$VENV_DIR/bin/python" ]; then
  if ! "$PYTHON_CMD" -m venv "$VENV_DIR" 2>/dev/null; then
    # Ubuntu's "minimal" Python ships without the venv module; you have to
    # install python3.11-venv (or the distro-equivalent) separately. Try.
    if [ "$OS" = "linux" ] && command -v apt-get >/dev/null 2>&1; then
      printf "  venv creation failed; installing python3-venv via apt...\n"
      sudo apt-get install -y -qq python3-venv python3.11-venv 2>/dev/null || \
        sudo apt-get install -y -qq python3-venv
      "$PYTHON_CMD" -m venv "$VENV_DIR" || true
    fi
  fi
  if [ ! -x "$VENV_DIR/bin/python" ]; then
    printf "%s[STEP 2/5] FAIL%s could not create venv at %s\n" "$E" "$R" "$VENV_DIR"
    exit 2
  fi
fi

REQ_PATH="$INSTALL_DIR/requirements.txt"
if [ -f "$REQ_PATH" ]; then
  if ! "$VENV_DIR/bin/pip" install --quiet --disable-pip-version-check -r "$REQ_PATH"; then
    printf "%s[STEP 2/5] FAIL%s pip install -r requirements.txt failed\n" "$E" "$R"
    exit 2
  fi
fi
printf "%s[STEP 2/5] OK%s\n\n" "$G" "$R"

# ── [STEP 3/5] Launcher entry ─────────────────────────────────────────────
printf "%s[STEP 3/5]%s Creating launcher...\n" "$B" "$R"
if [ "$OS" = "macos" ]; then
  START_CMD="$INSTALL_DIR/installer/start.command"
  if [ ! -f "$START_CMD" ]; then
    printf "%s[STEP 3/5] FAIL%s %s not found in checkout\n" "$E" "$R" "$START_CMD"
    exit 2
  fi
  chmod +x "$START_CMD" 2>/dev/null || true
  mkdir -p "$HOME/Applications"
  cp "$START_CMD" "$HOME/Applications/Clayrune.command"
  chmod +x "$HOME/Applications/Clayrune.command"
  printf "  Created %s\n" "$HOME/Applications/Clayrune.command"
elif [ "$OS" = "linux" ]; then
  START_SH="$INSTALL_DIR/installer/start.sh"
  if [ ! -f "$START_SH" ]; then
    printf "%s[STEP 3/5] FAIL%s %s not found in checkout\n" "$E" "$R" "$START_SH"
    exit 2
  fi
  chmod +x "$START_SH" 2>/dev/null || true
  APPS_DIR="$HOME/.local/share/applications"
  mkdir -p "$APPS_DIR"
  ICON_PATH="$INSTALL_DIR/assets/clayrune.png"
  cat > "$APPS_DIR/clayrune.desktop" << EOF
[Desktop Entry]
Type=Application
Version=1.0
Name=Clayrune
Comment=Operator console for long-running Claude agents
Exec=$START_SH
Icon=$ICON_PATH
Terminal=true
Categories=Development;
EOF
  chmod +x "$APPS_DIR/clayrune.desktop" 2>/dev/null || true
  printf "  Created %s\n" "$APPS_DIR/clayrune.desktop"
  if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "$APPS_DIR" 2>/dev/null || true
  fi
fi
printf "%s[STEP 3/5] OK%s\n\n" "$G" "$R"

# ── [STEP 4/5] Launch the server in a background process ──────────────────
printf "%s[STEP 4/5]%s Launching server in the background...\n" "$B" "$R"
if [ "$OS" = "macos" ]; then
  START_SCRIPT="$INSTALL_DIR/installer/start.command"
else
  START_SCRIPT="$INSTALL_DIR/installer/start.sh"
fi
LOG_FILE="$INSTALL_DIR/install-launch.log"
# Daemonize properly. On Ubuntu under `curl | sh`, the parent shell's stdin
# is the curl pipe — when curl finishes, the pipe closes, the parent shell
# exits, and `nohup ... &` children receive SIGHUP / SIGPIPE and die unless
# they're in a brand new session. setsid creates that new session, fully
# detaching from the controlling terminal. Also redirect stdin from /dev/null
# so the server isn't waiting on a closed pipe. Capture stdout+stderr to a
# log file so a startup crash leaves a forensic trail (the Polling loop
# below catches "server didn't come up", but knowing WHY needs the log).
if command -v setsid >/dev/null 2>&1; then
  setsid "$START_SCRIPT" </dev/null >"$LOG_FILE" 2>&1 &
else
  nohup "$START_SCRIPT" </dev/null >"$LOG_FILE" 2>&1 &
fi
SERVER_PID=$!
printf "  PID %s, log: %s\n" "$SERVER_PID" "$LOG_FILE"
printf "  Polling http://localhost:5199/ for up to 30s...\n"
server_up=0
i=0
while [ $i -lt 30 ]; do
  sleep 1
  i=$((i + 1))
  if curl -s -o /dev/null --max-time 2 http://localhost:5199/ 2>/dev/null; then
    server_up=1
    break
  fi
done
if [ "$server_up" -eq 1 ]; then
  printf "%s[STEP 4/5] OK%s\n\n" "$G" "$R"
else
  printf "%s[STEP 4/5] WARN%s server did not respond within 30s.\n" "$Y" "$R"
  if [ -s "$LOG_FILE" ]; then
    printf "          Last 20 lines of %s:\n" "$LOG_FILE"
    tail -n 20 "$LOG_FILE" 2>/dev/null | sed 's/^/            /'
  fi
  printf "          Install completed; you can launch manually via the launcher created above.\n\n"
fi

# ── [STEP 5/5] Open the dashboard in the default browser ──────────────────
printf "%s[STEP 5/5]%s Opening dashboard in your browser...\n" "$B" "$R"
if [ "$OS" = "macos" ]; then
  open http://localhost:5199 2>/dev/null || true
elif command -v xdg-open >/dev/null 2>&1; then
  xdg-open http://localhost:5199 >/dev/null 2>&1 &
else
  printf "  No xdg-open found. Open this URL manually: http://localhost:5199\n"
fi
printf "%s[STEP 5/5] OK%s\n\n" "$G" "$R"

# ── Final verification ────────────────────────────────────────────────────
printf "[install] Verifying install at: %s\n" "$INSTALL_DIR"
missing=""
for f in "$INSTALL_DIR/server.py" "$INSTALL_DIR/installer/start.sh" "$INSTALL_DIR/.venv/bin/python"; do
  [ -e "$f" ] || missing="$missing $f"
done
if [ -n "$missing" ]; then
  printf "\n%s============================================================%s\n" "$E" "$R"
  printf "%s  Install verification FAILED%s\n" "$E" "$R"
  printf "%s============================================================%s\n" "$E" "$R"
  printf "  Missing:\n"
  for f in $missing; do printf "    - %s\n" "$f"; done
  printf "  This should not happen — please report this output as an issue.\n"
  exit 2
fi

printf "\n%s============================================================%s\n" "$G" "$R"
printf "%s  Clayrune is installed and running.%s\n" "$G" "$R"
printf "%s============================================================%s\n" "$G" "$R"
printf "  Open:     http://localhost:5199\n"
printf "  Location: %s\n" "$INSTALL_DIR"
if [ "$OS" = "macos" ]; then
  printf "  Relaunch: open ~/Applications/Clayrune.command\n"
else
  printf "  Relaunch: launch \"Clayrune\" from your application menu, or run\n"
  printf "            %s\n" "$INSTALL_DIR/installer/start.sh"
fi
printf "%s============================================================%s\n" "$G" "$R"
exit 0
