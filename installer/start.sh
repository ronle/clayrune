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

# Open Clayrune as a STANDALONE app window (its own taskbar icon = the Clayrune
# mascot favicon), not a tab grouped with the user's other browser tabs. A
# Chromium browser in "--app=" mode gives that; we detect one, else fall back to
# a normal tab via xdg-open (old behaviour).
MC_BROWSER=""
for b in google-chrome google-chrome-stable chromium chromium-browser brave-browser microsoft-edge microsoft-edge-stable; do
  if command -v "$b" >/dev/null 2>&1; then MC_BROWSER="$b"; break; fi
done

if [ -n "$MC_BROWSER" ]; then
  (
    # Unlike a tab, an --app window shows a hard error on connection-refused and
    # won't auto-retry, so wait for the port to come up before opening it.
    if command -v curl >/dev/null 2>&1; then
      i=0
      while [ "$i" -lt 150 ]; do
        curl -s -o /dev/null "http://localhost:5199" && break
        i=$((i + 1)); sleep 0.2
      done
    else
      sleep 2
    fi
    "$MC_BROWSER" --app=http://localhost:5199 --no-first-run --no-default-browser-check >/dev/null 2>&1
  ) &
elif command -v xdg-open >/dev/null 2>&1; then
  ( sleep 1 && xdg-open http://localhost:5199 >/dev/null 2>&1 ) &
fi

# Run the server in the foreground so closing the launcher window stops it.
exec python server.py
