# Clayrune — Data Safety form answers

Pre-filled answers for the Play Console **Data Safety** section
(App content → Data safety). Most questions can be answered "No" /
"None collected" because the app is a self-hosted-client architecture
with no Clayrune backend.

---

## 1. Data collection and security

### Does your app collect or share any of the required user data types?

**No.**

The app sends user-typed content (chat prompts, etc.) to the
**user's own server** at a URL they configured. There is no Clayrune
server that observes this traffic. Per Google's
[Data safety guidance](https://support.google.com/googleplay/android-developer/answer/10787469),
data sent from the device directly to a user-controlled endpoint
without passing through a third-party service is **not** considered
"collection" or "sharing".

> "Data is considered collected when it's transferred off the user's
> device — except in cases where the app processes data on the user's
> device or the data is transferred to and processed only on the
> user's own device."

The CF Access service-token credentials never leave the device except
as HTTP request headers to the user's tunnel URL, which is the user's
own server.

### Is all of the user data collected by your app encrypted in transit?

**Yes.** All traffic to the user's tunnel URL goes over HTTPS via
Cloudflare's Tunnel infrastructure. The app does not permit cleartext
HTTP (`android:usesCleartextTraffic="false"`, Capacitor config
`cleartext: false`).

### Do you provide a way for users to request that their data be deleted?

**Yes.** The user can delete all locally-stored data via:
- Long-press reset gesture in-app (top-left corner, 1.5s)
- Android Settings → Apps → Clayrune → Storage → Clear data
- Uninstall

No off-device data exists to delete; the user controls their own
server.

---

## 2. Data types — each question and answer

When the form asks about each data type, the answer is **"No, my app
doesn't collect or share this type of user data"** for ALL of:

- **Personal info** (name, email, address, phone, etc.) — No
- **Financial info** — No
- **Health and fitness** — No
- **Messages** — No
  - *Rationale*: chat prompts the user types in the app go to the
    user's own server, not a third party. See §1 above.
- **Photos and videos** — No
- **Audio files** — No
- **Files and docs** — No
- **Calendar** — No
- **Contacts** — No
- **App activity** (in-app interactions, search history, etc.) — No
- **Web browsing** — No
- **App info and performance** (crash logs, diagnostics) — No
  - *Rationale*: no Crashlytics, no analytics SDK. Crashes are not
    reported anywhere.
- **Device or other IDs** — No
  - *Rationale*: FCM push token (if user opts in to push
    notifications) is sent to the user's own server, not to us. See
    §1 above.

---

## 3. If Play insists on a "Messages" disclosure because of the chat-input UI

If a reviewer pushes back on "Messages = No" because the app's
primary UI surfaces chat-style input, use this exact response:

> The app does not transmit user messages to any third party,
> including Clayrune. All chat input is sent to the user's own
> self-hosted server at a URL the user configures. Per Google's Data
> Safety policy, data sent only to a user-controlled endpoint without
> passing through a third-party service is not "collected" or
> "shared" by the app. The app has no backend.

If they still insist, mark "Messages — collected — not shared, used
for App functionality, processed ephemerally, user-controlled". That
should satisfy.

---

## 4. Security practices section

When the form asks about security practices:

- **Data is encrypted in transit**: **Yes** (HTTPS via CF Tunnel)
- **You provide a way for users to request that their data be
  deleted**: **Yes** (see §1)
- **Committed to follow the Play Families Policy**: skip — only
  required for apps targeting children
- **Independent security review**: **No** (and that's fine for an
  app with no backend)

---

## 5. Privacy policy URL

`https://clayrune.io/privacy`

(Must be live and reachable before submission. Content lives at
`docs/play-store/PRIVACY_POLICY.md`.)
