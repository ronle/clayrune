# Clayrune — Privacy Policy

**Effective date:** 2026-05-27
**App name:** Clayrune
**Developer:** Clayrune
**Contact:** leviran1@gmail.com

This privacy policy explains what data the Clayrune Android app
("the app") handles and how.

## Summary

**The app collects nothing.** It is a thin, authenticated client to a
server you operate yourself. We have no servers that observe your
activity, no analytics, no advertising, no third-party SDKs that
phone home.

## What you provide to the app

To connect, you supply the following on first launch (typically by
scanning a QR code from your own Clayrune dashboard):

- **Tunnel URL** — the address of your self-hosted Mission Control
  server (e.g. `https://yourname.clayrune.io`).
- **Cloudflare Access service-token credentials** (Client ID + Client
  Secret) — used by the app to authenticate every request to your
  server.

These values are stored locally on your device using Android's
EncryptedSharedPreferences, backed by the Android Keystore. They never
leave your device except as HTTP headers sent to the tunnel URL you
configured.

## What the app sends, and where

Every request the app makes goes to the tunnel URL you configured.
That URL points at a Cloudflare Tunnel that you (or the Clayrune
control plane on your behalf) provisioned, which forwards traffic to
your own Mission Control server. The app does not contact any other
host.

Specifically, the app does **not** send data to:
- Clayrune (us). We do not run a backend that the app talks to.
- Analytics services (Firebase Analytics, Google Analytics, etc.).
- Advertising networks.
- Crash reporting services.

## Permissions

- **INTERNET** — required to reach your tunnel URL.
- **POST_NOTIFICATIONS** (Android 13+) — used only if you opt in to
  push notifications from your own server.

The app does **not** request:
- Camera (the QR scanner is provided by Google Play Services in a
  separate process; the app itself never accesses the camera).
- Location.
- Contacts.
- Microphone (except for the optional dashboard voice-input feature,
  which is gated behind a runtime prompt; recordings are processed by
  Android's on-device speech recognizer and never sent to us).
- Storage.

## Third-party services

The app depends on the following Google Play Services modules that
run in their own sandboxed processes outside the app:

- **Google Play Services Code Scanner** (`play-services-code-scanner`)
  — used only when you tap "Scan QR" on the setup screen. The scanner
  runs in a separate process; the QR's contents are returned to the
  app but no image data is transmitted off the device.
- **Firebase Cloud Messaging** (`firebase-messaging`) — used only if
  you opt in to push notifications. Your device's FCM token is sent
  to YOUR Mission Control server (the tunnel URL you configured), not
  to us.

## Data deletion

You can fully remove all data the app stores by:
1. Long-pressing the top-left corner of the dashboard for 1.5 seconds
   to surface the reset dialog and clearing your credentials, OR
2. Going to Android Settings → Apps → Clayrune → Storage → **Clear
   data**, OR
3. Uninstalling the app.

Any of these completely wipes the locally stored tunnel URL and CF
Access credentials.

## Children

The app is not directed at children under 13. Because the app
collects no data, no special handling for children is needed beyond
this disclosure.

## Changes to this policy

If we ever start collecting data (we don't plan to), we'll publish a
new version of this policy at the same URL and bump the "Effective
date" above. There is no in-app mechanism to notify you of changes —
this is consistent with the no-backend posture.

## Contact

Questions or concerns: **leviran1@gmail.com**
