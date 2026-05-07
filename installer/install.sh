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

# ── Step 1: Claude CLI present? ────────────────────────────────────────────
if ! command -v claude >/dev/null 2>&1; then
  printf "%sClaude CLI not found. Attempting to install...%s\n\n" "$Y" "$R"

  if command -v npm >/dev/null 2>&1; then
    printf "Trying npm install -g @anthropic-ai/claude-code\n"
    if ! npm install -g @anthropic-ai/claude-code; then
      printf "\n%snpm install failed.%s\n" "$E" "$R"
      printf "Fall back to: %scurl -fsSL https://claude.ai/install.sh | sh%s\n" "$C" "$R"
      exit 1
    fi
  elif command -v curl >/dev/null 2>&1; then
    printf "npm not found. Trying Anthropic's curl-installer...\n"
    if ! curl -fsSL https://claude.ai/install.sh | sh; then
      printf "\n%sInstall failed.%s\n" "$E" "$R"
      printf "Manual install: https://docs.anthropic.com/claude-code\n"
      exit 1
    fi
  else
    printf "%sNeither npm nor curl found — cannot auto-install Claude CLI.%s\n" "$E" "$R"
    printf "Install it manually first:\n"
    printf "  https://docs.anthropic.com/claude-code\n"
    printf "Then re-run: %scurl -sSL https://clayrune.io/install.sh | sh%s\n" "$C" "$R"
    exit 1
  fi

  if ! command -v claude >/dev/null 2>&1; then
    printf "%sClaude CLI installed but not on PATH.%s\n" "$Y" "$R"
    printf "Open a new terminal session and re-run this installer.\n"
    exit 1
  fi
fi

CLAUDE_VERSION=$(claude --version 2>&1 | head -n1 || echo "unknown")
printf "%sOK%s Claude CLI: %s\n\n" "$G" "$R" "$CLAUDE_VERSION"

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

# Pipe the prompt as a single user message via stdin. The CLI accepts a prompt
# on stdin when run with `-` or with no positional arg in interactive mode, but
# the most reliable cross-version path is `-p <prompt>` on the command line.
# We use heredoc-via-env-var to avoid shell quoting issues with multi-line text.
exec claude --dangerously-skip-permissions -p "$PROMPT"
