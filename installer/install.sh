#!/usr/bin/env sh
# Clayrune installer bootstrap (macOS / Linux)
#
# Usage:
#   curl -sSL https://clayrune.io/install.sh | sh
#
# What this script does:
#   1. Verifies Claude CLI is installed (or installs it via npm / Anthropic's
#      installer if missing).
#   2. Fetches the install prompt from clayrune.io.
#   3. Discloses what is about to happen, with a short Ctrl-C abort window.
#   4. Pipes the prompt into `claude --dangerously-skip-permissions`.
#
# After authorization, Claude itself executes the install — clones the repo,
# installs Python and Node deps, creates a Desktop / Applications launcher,
# and opens the app in the user's browser.
#
# Read the install prompt before running:
#   curl -sSL https://clayrune.io/install-prompt.md
#
# Override URLs (for testing):
#   CLAYRUNE_BOOTSTRAP_URL=...     (this file's URL — informational only)
#   CLAYRUNE_PROMPT_URL=...        (where to fetch the install prompt)
#   CLAYRUNE_NO_CONFIRM=1          (skip the 5-second abort window)

set -e

PROMPT_URL="${CLAYRUNE_PROMPT_URL:-https://clayrune.io/install-prompt.md}"

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

# Returns 0 iff `claude --version` exits 0 AND emits non-empty output.
# This is the *real* working-state check — `command -v claude` only proves a
# binary exists, not that it actually runs (the WSL Node-version mismatch
# scenario produces a `claude` binary that crashes with a SyntaxError on every
# invocation).
_validate_claude() {
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
if ! _check_claude_auth; then
  printf "\n%sClaude CLI is installed but not authenticated.%s\n\n" "$Y" "$R"
  printf "%sStep 1.%s Open a NEW terminal window so PATH picks up Claude CLI.\n" "$B" "$R"
  printf "         (Or in this shell, force a login-shell reload: %sexec bash -l%s)\n" "$C" "$R"
  printf "         (This sources ~/.profile for ~/.local/bin AND ~/.bashrc for nvm.)\n\n"
  printf "%sStep 2.%s Log in to Claude:\n" "$B" "$R"
  printf "         %sclaude /login%s\n" "$C" "$R"
  printf "         (Follow the OAuth prompts, or paste an Anthropic API key.)\n"
  printf "         (After you see \"Logged in\", type %sexit%s or press Ctrl+D to leave the Claude REPL.)\n\n" "$C" "$R"
  printf "%sStep 3.%s Re-run this installer:\n" "$B" "$R"
  printf "         %scurl -sSL https://raw.githubusercontent.com/ronle/mission-control/master/installer/install.sh | CLAYRUNE_PROMPT_URL=https://raw.githubusercontent.com/ronle/mission-control/master/installer/install-prompt.md sh%s\n" "$C" "$R"
  printf "         (Once clayrune.io is up: %scurl -sSL https://clayrune.io/install.sh | sh%s)\n" "$C" "$R"
  exit 1
fi
printf "%sOK%s Authenticated\n\n" "$G" "$R"

# ── Step 2: Fetch install prompt ───────────────────────────────────────────
printf "Fetching install instructions from %s\n" "$PROMPT_URL"
PROMPT=$(curl -fsSL "$PROMPT_URL" 2>/dev/null) || {
  printf "%sFailed to fetch install prompt.%s\n" "$E" "$R"
  printf "URL: %s\n" "$PROMPT_URL"
  exit 1
}
printf "%sOK%s Got install prompt (%d bytes)\n\n" "$G" "$R" "${#PROMPT}"

# ── Step 3: Disclosure ─────────────────────────────────────────────────────
printf "%s──────────────────────────────────────%s\n" "$Y" "$R"
printf "%sAbout to run:%s\n" "$B" "$R"
printf "  claude --dangerously-skip-permissions \"<install prompt>\"\n\n"
printf "Claude will execute shell commands on your machine to install Clayrune.\n"
printf "Estimated time: 3-5 minutes.\n"
printf "Read the prompt: %s\n" "$PROMPT_URL"
printf "%s──────────────────────────────────────%s\n\n" "$Y" "$R"

if [ -z "${CLAYRUNE_NO_CONFIRM:-}" ]; then
  printf "Press Ctrl+C in the next 5 seconds to abort, or wait...\n"
  sleep 5
fi

# ── Step 4: Hand off to Claude ─────────────────────────────────────────────
printf "\n%s>>> Handing off to Claude%s\n\n" "$B" "$R"

# Stream Claude's progress in real time so the user can see what's happening
# during the 3-5 minute install. Without this, the terminal looks frozen.
#
# `claude -p` alone prints only the final assistant response. Adding
# `--output-format stream-json --print --verbose` emits one JSON object per
# line covering every step (assistant text blocks, tool_use calls, results).
# We pipe through python3 to surface the human-readable bits and dim the
# tool-call lines.
#
# If python3 isn't available, fall back to plain `-p` mode so the install
# still works (just silently for a few minutes).
if command -v python3 >/dev/null 2>&1; then
  # Capture claude's exit code via a temp file (POSIX sh has no PIPESTATUS).
  EXIT_FILE=$(mktemp 2>/dev/null || echo "/tmp/clayrune-exit.$$")
  trap 'rm -f "$EXIT_FILE"' EXIT
  # `set +e` so a non-zero exit from claude doesn't abort this LHS-of-pipe
  # subshell before we capture $? into EXIT_FILE. Restored implicitly when
  # the subshell ends.
  {
    set +e
    claude --dangerously-skip-permissions \
           -p "$PROMPT" \
           --print --verbose \
           --output-format stream-json
    echo "$?" > "$EXIT_FILE"
  } | python3 -c '
import json, sys
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        obj = json.loads(line)
    except Exception:
        print(line)
        continue
    t = obj.get("type")
    if t == "assistant":
        msg = obj.get("message") or {}
        for block in (msg.get("content") or []):
            bt = block.get("type")
            if bt == "text":
                txt = block.get("text") or ""
                if txt:
                    print(txt, flush=True)
            elif bt == "tool_use":
                name = block.get("name") or "?"
                print(f"  [tool: {name}]", flush=True)
    elif t == "result" and obj.get("is_error"):
        print(f"  [error] {obj.get(\"result\", \"\")}", flush=True)
'
  CLAUDE_EXIT=$(cat "$EXIT_FILE" 2>/dev/null || echo 0)
  exit "$CLAUDE_EXIT"
else
  printf "%sNote:%s python3 not found; running without progress streaming.\n\n" "$Y" "$R"
  exec claude --dangerously-skip-permissions -p "$PROMPT"
fi
