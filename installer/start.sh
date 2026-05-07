#!/usr/bin/env sh
# Clayrune launcher (Linux)
# Activates the venv, starts the Flask server, opens the browser.
#
# Invoked by the .desktop file the installer placed in
# ~/.local/share/applications/clayrune.desktop. Can also be run by hand.

set -e

# Resolve the install directory (parent of this script's directory).
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CLAYRUNE_DIR="$(dirname "$SCRIPT_DIR")"
cd "$CLAYRUNE_DIR"

if [ ! -f ".venv/bin/activate" ]; then
  echo "[Clayrune] No .venv found at $CLAYRUNE_DIR/.venv"
  echo "[Clayrune] Re-run the installer:"
  echo "[Clayrune]   curl -sSL https://clayrune.io/install.sh | sh"
  exit 1
fi

# shellcheck disable=SC1091
. .venv/bin/activate

echo "[Clayrune] Starting server on http://localhost:5199"

# Open the browser in the background — server bind takes a beat. Most browsers
# retry on connection-refused so opening before the bind is fine.
if command -v xdg-open >/dev/null 2>&1; then
  ( sleep 1 && xdg-open http://localhost:5199 >/dev/null 2>&1 ) &
fi

# Run the server in the foreground so closing the launcher window stops it.
exec python server.py
