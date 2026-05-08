#!/usr/bin/env sh
# ============================================================
#  Clayrune Installer (macOS)
#
#  Double-click this file in Finder to install Clayrune on your
#  Mac. Wraps install.sh so users don't need to use Terminal
#  manually.
#
#  Note: macOS Gatekeeper will block this file the first time
#  you double-click it ("unidentified developer"). Right-click
#  it and choose "Open" to bypass the warning. Once allowed
#  once, future double-clicks just work.
# ============================================================

set -e

cat <<'EOF'

============================================================
  Clayrune Installer
============================================================

This will install Clayrune on this Mac.

It will:
  1. Install Claude CLI (if missing) — Anthropic's installer or npm
  2. Install Node.js 20 via nvm (user-space, no sudo)
  3. Ask you to log in once (browser opens for OAuth)
  4. Clone Clayrune to ~/Clayrune
  5. Set up Python venv + a launcher in ~/Applications/
  6. Open the dashboard in your browser

Estimated time: 5-10 minutes.
Disk space: about 500 MB.

You can audit what runs by reading:
  https://raw.githubusercontent.com/ronle/mission-control/master/installer/install-prompt.md

EOF

printf 'Press Enter to continue, or Ctrl+C to abort... '
read _ < /dev/tty || true
echo

# Run the bootstrap. CLAYRUNE_PROMPT_URL points at the GitHub raw URL until
# clayrune.io DNS is configured. Once the domain is live, drop the env var.
curl -sSL https://raw.githubusercontent.com/ronle/mission-control/master/installer/install.sh | \
  CLAYRUNE_PROMPT_URL=https://raw.githubusercontent.com/ronle/mission-control/master/installer/install-prompt.md sh

EXIT_CODE=$?

echo
echo "============================================================"
if [ "$EXIT_CODE" = "0" ]; then
  echo "  Done."
  echo
  echo "  If installation succeeded, you'll find Clayrune in your"
  echo "  Applications folder. Double-click it any time to launch."
else
  echo "  Installer exited with error code $EXIT_CODE."
  echo
  echo "  The output above shows what went wrong. To retry, just"
  echo "  double-click this file again — the installer is idempotent"
  echo "  and will pick up where it left off."
fi
echo "============================================================"
echo

printf 'Press Enter to close this window... '
read _ < /dev/tty || true
