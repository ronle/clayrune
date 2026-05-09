#!/usr/bin/env sh
# Clayrune launcher (macOS)
# Activates the venv, starts the Flask server, opens the browser.
#
# .command files open in Terminal.app when double-clicked from Finder.
# Invoked from ~/Applications/Clayrune.command (copy made by the installer).

set -e

# Resolve the install directory (parent of this script's directory).
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CLAYRUNE_DIR="$(dirname "$SCRIPT_DIR")"
cd "$CLAYRUNE_DIR"

if [ ! -f ".venv/bin/activate" ]; then
  echo "[Clayrune] No .venv found at $CLAYRUNE_DIR/.venv"
  echo "[Clayrune] Re-run the installer:"
  echo "[Clayrune]   curl -sSL https://clayrune.io/install.sh | sh"
  read -r -p "Press Enter to close..."
  exit 1
fi

# shellcheck disable=SC1091
. .venv/bin/activate

echo "[Clayrune] Starting server on http://localhost:5199"

# Open the browser in the background — server bind takes a beat. Safari /
# Chrome both retry on connection-refused so opening before the bind is fine.
( sleep 1 && open http://localhost:5199 ) &

# Run the server in the foreground. Closing the Terminal window stops it.
exec python server.py
