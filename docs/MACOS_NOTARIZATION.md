# macOS code-signing & notarization

How we make the Mac download open **without** the Gatekeeper
"Apple could not verify Clayrune is free of malware… Move to Trash" block.

That block appears because browsers attach a `com.apple.quarantine` flag to
downloads, and macOS refuses to launch a quarantined app that isn't signed by
a known developer and notarized by Apple. The fix is to sign the app with an
Apple **Developer ID** certificate and run it through Apple's notary service.

This reverses the earlier "no paid code-signing" stance **for the Mac `.app`
only** — the closed-source Rust `mc-tunnel` moat is unaffected.

---

## TL;DR (per release)

```bash
pyinstaller build-macos.spec --noconfirm     # builds dist/Clayrune.app
tools/notarize-macos.sh                        # -> MissionControl-macOS.zip (signed + notarized)
```

Then **upload `MissionControl-macOS.zip` over the website / GitHub release
asset.** The CI workflow (`build-macos.yml`) auto-attaches an *unsigned* zip on
every release — you must replace it with the signed one, or users still hit the
warning.

---

## One-time setup (do this once, ever)

1. **Apple Developer Program membership** — $99/yr at
   [developer.apple.com](https://developer.apple.com). Enrollment is usually
   approved within 24–48h. This gives you a **Team ID** (`<TEAMID>`).

2. **Xcode Command Line Tools** (you do *not* need the full ~15 GB Xcode app):
   ```bash
   xcode-select --install
   ```

3. **A "Developer ID Application" certificate** in your login keychain.
   - Open **Keychain Access** → menu bar **Certificate Assistant → Request a
     Certificate From a Certificate Authority…** → enter your email, leave CA
     Email blank, choose **Saved to disk**.
   - At **developer.apple.com/account → Certificates → +**, pick
     **Developer ID Application** (under the *Software* group — **not** "Apple
     Development", which is the default and cannot notarize), upload the CSR,
     download the `.cer`, and double-click it to install.
   - Confirm:
     ```bash
     security find-identity -v -p codesigning
     # want: "Developer ID Application: <Your Name> (<TEAMID>)"
     ```

4. **Store a notarytool credential profile** named `clayrune-notary`. First make
   an **app-specific password** at [appleid.apple.com](https://appleid.apple.com)
   → Sign-In & Security → App-Specific Passwords (this is *not* your Apple ID
   password). Then:
   ```bash
   xcrun notarytool store-credentials "clayrune-notary" \
     --apple-id "you@example.com" \
     --team-id "<TEAMID>" \
     --password "xxxx-xxxx-xxxx-xxxx"
   ```
   The password lives in your login keychain after this — never in the repo.

---

## What the script does

`tools/notarize-macos.sh` runs these steps and fails loudly if any check trips:

1. Writes a hardened-runtime entitlements plist (PyInstaller bundles need
   `allow-unsigned-executable-memory`, `disable-library-validation`, `allow-jit`
   or the app won't launch once signed).
2. `codesign --force --deep --options runtime --timestamp` with the Developer ID
   identity.
3. Verifies via `codesign -dvv` that the **Authority** is the Developer ID cert
   (not the ad-hoc signature PyInstaller leaves behind).
4. Zips with `ditto`, submits to the notary service with `notarytool --wait`,
   and aborts + prints the log if the status isn't `Accepted`.
5. `stapler staple`s the ticket into the `.app` and confirms `spctl` reports
   `source=Notarized Developer ID`.
6. Re-zips the **stapled** app to `MissionControl-macOS.zip`.

---

## Gotchas (learned the hard way, 2026-06-04)

- **Wrong cert type.** "Apple Development" is the portal's default and is
  useless here — you need **Developer ID Application**.
- **`codesign --verify` lies.** It passes on PyInstaller's ad-hoc signature, so
  a build that was never signed looks "valid on disk". Always check the
  `Authority=` line from `codesign -dvv` instead.
- **Entitlements plist corruption.** A stray character on the `<!DOCTYPE>` line
  produces `invalid length in entitlement blob`. The script omits the DOCTYPE
  entirely (it's optional) and `plutil -lint`s the file before signing.
- **Multiline paste eats characters.** Running `codesign` as one long line
  avoids the `\`-continuation paste bug that turned `codesign` into `odesign`.
- **The submitted zip is stale.** Stapling happens *after* notarization, so the
  distributable must be re-zipped from the stapled `.app`.

---

## Deferred: folding this into CI

Right now signing is a manual post-build step. To make every release ship
notarized automatically, `build-macos.yml` would need to, on a macОS runner:

1. Import the Developer ID cert from a base64-encoded `.p12` GitHub secret into
   a temporary keychain (`security create-keychain` / `import` / `unlock`).
2. Run the same `codesign` + `notarytool` + `stapler` steps (notarytool creds
   as `APPLE_ID` / `TEAM_ID` / `APP_SPECIFIC_PASSWORD` secrets).

Secrets needed: `MACOS_CERT_P12_BASE64`, `MACOS_CERT_PASSWORD`,
`MACOS_NOTARY_APPLE_ID`, `MACOS_NOTARY_TEAM_ID` (`<TEAMID>`),
`MACOS_NOTARY_PASSWORD`. Tracked as a follow-up; not yet wired.
