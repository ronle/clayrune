# The manual shots — shooting script

**Who:** Ron. **Time:** ~1 hour including retakes. **What it buys:** the one beat
no capture harness can fake, and the thing the whole launch converts on.

Shots 1–3 (grid, streaming thread, back to grid) are **already captured** —
`docs/assets/demo-desktop.mp4`. This document covers only what needs a human, a
real phone, and a real desk: **shots 4 and 5.**

If you'd rather shoot the whole thing in one continuous physical take, do — the
script below runs 0–45s end to end and the captured desktop footage becomes the
backup.

---

## 0. Before you roll (5 min)

```bash
python tools/demo-shoot/prep.py
```

One command. It brings up an **isolated** Clayrune on port 5200 with its own data
dir, resets the throwaway repos, and starts **three real Claude agents** so the
grid is genuinely live. It prints the phone URL when it's done.

**Then, within ~2 minutes, roll.** The agents finish in a couple of minutes; if
you dawdle, the grid goes idle and the shot is dead. Re-run `prep.py` for every
take — that's what it's for.

### The one thing that will ruin the take

> **On the phone, open `http://192.168.86.4:5200` — the LAN IP. NEVER the tunnel.**

The tunnel (`<you>.clayrune.io`) points at your **real** instance on :5199 and
would put your real project names, your real work, and your real backlog on
camera. Port 5200 is the fake one. Type the IP by hand; do not use a bookmark.

### Set the stage

- **Phone:** Do Not Disturb ON. Brightness to max (a dim phone screen reads as
  grey mush on video). Clear the notification shade. Close every other app.
- **Desktop:** browser fullscreen (F11), no other tabs visible, no bookmarks bar.
  Notifications off. Nothing personal in frame.
- **Desk:** the desktop screen must be **visible and obviously awake** behind you
  in shot 5. That's not decoration — it is the honesty that does the work no
  caption can (we never claim the machine can sleep).
- **Camera:** phone-on-tripod or a second phone is fine. 1080p minimum, 60fps for
  the pickup. Landscape.

---

## 1. The shots

### Shot 4 — walk away (22–28s) · **~6 seconds**

**Frame:** you at the desk, the grid visible on the monitor, tiles clearly
"IN PROGRESS."

**Action:** stand up. Push the chair back. Walk out of frame. Leave the monitor
running and in shot for a beat after you've gone.

**Why it's here:** it's physical. A UI transition says "the app has a mobile
view." A human standing up and leaving says *"the work continues without me,"*
which is the actual product.

**Do not:** narrate. Do not gesture at the screen. Just leave.

---

### Shot 5 — the phone (28–40s) · **~12 seconds. This is the clip.**

If you only get one shot right, this is it. Do not cut it for length. Do not speed
it up.

**Frame:** the phone in your hand, and — at least once — **the desktop visibly in
the same shot**, still running. One continuous physical space is what makes this
read as real rather than composited. That single frame is worth more than any
caption.

**Action, in order:**

1. Pick the phone up. Unlock it. (Leave the unlock in — it costs a second and it
   proves the phone is a phone.)
2. Open the browser. **The Clayrune mobile view is already there** — the project
   list, "Working 3 / Resting 2", the same agents, live.
3. Tap into **Orchard**. The thread is mid-stream — the same conversation that is
   on the monitor behind you.
4. **Type something into it and send.** Anything real and short:
   > `also add a dark mode toggle while you're in there`
5. Hold on the phone for a beat while the agent picks it up.
6. **Pan to the desktop.** It has updated. Same session. Same message.

**Step 6 is the money.** The pan from phone to desktop, with your message on both,
is the entire pitch. If the pan is shaky, do it again — this is the one to burn
takes on.

**Do not:** hide the latency. If the agent takes three seconds to respond, let it
take three seconds. Real beats fast; a viewer who smells a fake stops believing
everything else in the clip.

---

### Shot 6 — the close (40–45s) · **~5 seconds**

Cut to black. One line, then `clayrune.io`.

Suggested line (matches the free product's actual hook, and claims nothing we're
not allowed to claim):

> **Your agents keep working when you close the chat.**

---

## 2. The rules (from `DEMO_VIDEO_SPEC.md`)

- **Real UI. Real agents. Real network.** No mockups, no sped-up fakery.
- **Silent + captioned.** Most X/Reddit views are muted. Any narration is a bonus
  track, not the carrier. The clip must work with the sound off.
- **Never on camera:** API keys · real client or project names · token-cost figures
  · the Cloudflare hostname · anything that looks like a credential.
- **Never implied:** that we run Claude · that the machine can be asleep.

---

## 3. Definition of done

Show the cut **on mute** to one Claude Code user who has never heard of Clayrune.

If they cannot say what the product is, **the clip is wrong and the launch is not
ready** — no amount of copy fixes that.

---

## 4. After you shoot

Drop the footage anywhere and tell me where. I'll cut the 45s master, re-cut the
README GIF around the real phone beat (replacing the screen-only one at
`docs/assets/demo.gif`), and pull the gallery stills from the same capture.

Everything else — the landing page, the Show HN essay, the Product Hunt gallery —
is already written and waiting on exactly this.
