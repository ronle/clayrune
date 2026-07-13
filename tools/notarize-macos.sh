#!/usr/bin/env bash
#
# notarize-macos.sh — sign, notarize, staple, and zip Clayrune.app for release.
#
# Turns the ten-command Apple signing dance into one command. Run it on a Mac
# after building the app:
#
#     pyinstaller build-macos.spec --noconfirm     # produces dist/Clayrune.app
#     tools/notarize-macos.sh                        # -> Clayrune-macOS.zip
#
# The output Clayrune-macOS.zip is the notarized, Gatekeeper-clean
# artifact to upload to the website / GitHub release. It is a drop-in
# replacement for the UNSIGNED zip that .github/workflows/build-macos.yml
# currently produces and auto-attaches to releases — always replace that one.
#
# ── One-time setup (do this once, ever) ─────────────────────────────────────
# Full walkthrough: docs/MACOS_NOTARIZATION.md. The short version:
#   1. Apple Developer Program membership ($99/yr).
#   2. A "Developer ID Application" certificate in your login keychain
#      (Keychain Access CSR -> developer.apple.com -> download -> double-click).
#   3. Store a notarytool credential profile (name it whatever you like):
#        xcrun notarytool store-credentials "my-notary-profile" \
#          --apple-id "you@example.com" \
#          --team-id "YOURTEAMID" \
#          --password "<app-specific-password from appleid.apple.com>"
#   4. Tell this script who you are. Your signing identity is specific to YOUR
#      Apple account, so it is not hardcoded here — put it in tools/signing.env
#      (gitignored), which this script sources automatically:
#        CLAYRUNE_SIGN_IDENTITY="Developer ID Application: Your Name (TEAMID)"
#        CLAYRUNE_NOTARY_PROFILE="my-notary-profile"
#      Or export the same vars in your shell.
#
# Nothing secret lives in this script or in signing.env. The app-specific
# password is stored in your login keychain by store-credentials, not here.
#
# Usage:  tools/notarize-macos.sh [path/to/Clayrune.app]   (default: dist/Clayrune.app)

set -euo pipefail

# ── Config ──────────────────────────────────────────────────────────────────
# Per-developer settings come from tools/signing.env (gitignored) or the env.
_SIGNING_ENV="$(dirname "$0")/signing.env"
# shellcheck source=/dev/null
[ -f "$_SIGNING_ENV" ] && . "$_SIGNING_ENV"

APP="${1:-dist/Clayrune.app}"
IDENTITY="${CLAYRUNE_SIGN_IDENTITY:-}"
PROFILE="${CLAYRUNE_NOTARY_PROFILE:-clayrune-notary}"
OUT_ZIP="${CLAYRUNE_OUT_ZIP:-Clayrune-macOS.zip}"

# Note: die() is defined below, so this preflight prints its own error.
if [ -z "$IDENTITY" ]; then
  printf '\n\033[1;31mERROR:\033[0m %s\n' "No signing identity configured.
Set CLAYRUNE_SIGN_IDENTITY in tools/signing.env or your environment, e.g.
  CLAYRUNE_SIGN_IDENTITY=\"Developer ID Application: Your Name (TEAMID)\"
List the identities in your keychain with:
  security find-identity -v -p codesigning
See docs/MACOS_NOTARIZATION.md." >&2
  exit 1
fi

say() { printf '\n\033[1;36m==>\033[0m %s\n' "$1"; }
die() { printf '\n\033[1;31mERROR:\033[0m %s\n' "$1" >&2; exit 1; }

# ── Preflight ───────────────────────────────────────────────────────────────
[ -d "$APP" ] || die "App not found: $APP
Build it first:  pyinstaller build-macos.spec --noconfirm"

command -v xcrun >/dev/null 2>&1 || die "Xcode Command Line Tools missing. Run: xcode-select --install"

if ! grep -qF "$IDENTITY" <<<"$(security find-identity -v -p codesigning)"; then
  die "Signing identity not found in your keychain:
  $IDENTITY
Set up the Developer ID cert first — see docs/MACOS_NOTARIZATION.md"
fi

# ── 1. Entitlements (PyInstaller needs these under the hardened runtime) ─────
# No <!DOCTYPE> line on purpose — it's not required and a stray character there
# silently corrupts the plist (codesign: "invalid length in entitlement blob").
say "Writing entitlements"
ENTITLEMENTS="$(mktemp -t clayrune-entitlements)"
cat > "$ENTITLEMENTS" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<plist version="1.0"><dict>
  <key>com.apple.security.cs.allow-unsigned-executable-memory</key><true/>
  <key>com.apple.security.cs.disable-library-validation</key><true/>
  <key>com.apple.security.cs.allow-jit</key><true/>
</dict></plist>
PLIST
plutil -lint "$ENTITLEMENTS" >/dev/null || die "Generated entitlements plist is invalid (should never happen)."

# ── 2. Sign (deep, hardened runtime, secure timestamp) ──────────────────────
say "Signing $APP"
codesign --force --deep --options runtime --timestamp \
  --entitlements "$ENTITLEMENTS" \
  --sign "$IDENTITY" \
  "$APP"

# Confirm it's REALLY signed with the Developer ID identity. `codesign --verify`
# alone is not enough — it passes on PyInstaller's pre-existing ad-hoc signature
# and gives a false "valid on disk". The Authority line is the real proof.
say "Verifying signature"
SIG_INFO="$(codesign -dvv "$APP" 2>&1)"
grep -qF "Authority=$IDENTITY" <<<"$SIG_INFO" \
  || die "App is not signed with the Developer ID identity after signing."
codesign --verify --strict "$APP" || die "codesign --verify failed."

# ── 3. Notarize ─────────────────────────────────────────────────────────────
say "Zipping for notarization"
SUBMIT_ZIP="$(mktemp -d)/Clayrune-submit.zip"
ditto -c -k --keepParent "$APP" "$SUBMIT_ZIP"

say "Submitting to Apple's notary service (waits ~2-5 min)…"
SUBMIT_OUT="$(xcrun notarytool submit "$SUBMIT_ZIP" --keychain-profile "$PROFILE" --wait 2>&1)" || true
echo "$SUBMIT_OUT"
if ! grep -q "status: Accepted" <<<"$SUBMIT_OUT"; then
  SUB_ID="$(grep -m1 '  id:' <<<"$SUBMIT_OUT" | awk '{print $2}')"
  [ -n "${SUB_ID:-}" ] && xcrun notarytool log "$SUB_ID" --keychain-profile "$PROFILE" || true
  die "Notarization failed (see log above)."
fi

# ── 4. Staple the ticket + final Gatekeeper check ───────────────────────────
say "Stapling ticket"
xcrun stapler staple "$APP"
GK_INFO="$(spctl -a -t exec -vvv "$APP" 2>&1)"
grep -q "source=Notarized Developer ID" <<<"$GK_INFO" \
  || die "Gatekeeper assessment did not report 'Notarized Developer ID'."

# ── 5. Zip the STAPLED app for distribution ─────────────────────────────────
# (the zip we submitted is stale — the ticket got stapled into the .app after.)
say "Zipping notarized app -> $OUT_ZIP"
rm -f "$OUT_ZIP"
ditto -c -k --sequesterRsrc --keepParent "$APP" "$OUT_ZIP"

say "Done — this is Gatekeeper-clean. Upload it over the website / release asset:"
ls -lh "$OUT_ZIP"
