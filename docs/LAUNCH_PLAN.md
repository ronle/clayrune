# Clayrune — Launch Plan

**Owner:** Steward. **v2, 2026-07-13.** Supersedes the strategy half of
`MARKETING_CAMPAIGN_PLAN.md` (which is retained for its competitive landscape but
had two rule violations — see §0).
**Constraints:** `clayrune-cloud/docs/STEWARD_HANDOFF.md` is binding. Cloud tier is
PARKED. Messaging rules in its §2 are legal, not stylistic.

---

## 0. Corrections to the existing marketing docs (do these first — they are live)

`MARKETING_CAMPAIGN_PLAN.md` v1 is being executed by future steward cycles and
currently contains claims we are now forbidden to make:

| Line | Problem | Fix |
|---|---|---|
| §Positioning, §1 | *"Your own always-on AI agent… no computer to babysit"* — used as the headline pitch, **twice** | Delete. Violates handoff §2.4. That is the parked Cloud tier's promise. |
| §3.6 | *"free start via Gemini key"* as an on-ramp pillar | Demote. Claude-first is the position; Gemini/Codex are *supported*, not the pitch. |
| §7 | Success metrics = GitHub stars, downloads, HN rank | Replace with WAU / D7-D30 / % remote-enabled. Handoff §9 explicitly rejects stars. |

Everything else in that doc (the category argument, the competitor table) stands.

---

## 1. The hook problem — I think the framing is wrong, and there is a structural fix

Vector's §4: *"remote access is both the hook and the paywall, so the funnel has no
top."* Two objections.

### 1.1 It is not a launch problem. It is a billing-day problem.

**Connect ships as a free beta with no paywall** (already decided). So on launch day
the hook is available to every single user. There is no paywall to sit behind. The
funnel top at launch is *content* (§3), not a product tier. Solving the paywall's
funnel geometry before we have one user is premature.

Where it becomes real is the day billing turns on — and by then we will have
retention data telling us whether the hook is even the hook. **Do not distort the
launch to pre-solve a problem that only exists in a future we haven't earned yet.**

### 1.2 When it does become real, don't paywall remote access. Paywall the *plumbing*.

This is the structural fix, and it is the only split that is honest for an MIT
project:

| | Free (MIT, forever) | **Connect $6.99** |
|---|---|---|
| Reach your agent from your phone | ✅ **yes** — bring your own tunnel: Tailscale, ngrok, your own CF tunnel, or plain LAN | ✅ yes |
| What you do to get it | DNS, a tunnel daemon, TLS, an auth story. An afternoon, and you own it. | Click **Enable**. `<you>.clayrune.io`. Done. |
| Works on cellular, off your LAN | only if you set it up | ✅ out of the box |
| Push notifications, device revoke, PWA install | you build it | ✅ |

**The hook is free. The convenience is paid.** That is the self-host/managed split
every successful open-core dev tool uses, and it inverts the problem: people
*discover* phone control for free, feel it, and then pay to stop maintaining a
tunnel. They are not paying for a capability we withheld — they are paying to not do
plumbing.

It also disarms the single most dangerous launch reaction: **r/selfhosted and HN will
flame an MIT project that paywalls remote access.** They will *applaud* one that ships
the seam and sells the hosted convenience. `mc_remote_iface` already IS that seam —
the provider Protocol, the registry, `dev_stub`. We are one documented
"here's how to point Clayrune at your own Tailscale/CF tunnel" guide away from this
being true today.

**Product ask (small):** a `docs/SELF_HOSTED_REMOTE.md` + a first-party
`mc_remote_lan` provider (bind + passcode, which mostly exists). Not a launch gate;
a billing-day gate.

### 1.3 The free tier's hook is not "a dashboard." It is *"it works while you don't."*

Nobody wakes up wanting a UI for Claude Code. "The missing UI for Claude Code" is a
*category* line, not a hook — it describes us, it doesn't make anyone install
anything.

The free, local product already has a genuinely visceral hook and we are not using
it:

> **Scheduler + steward + hivemind: your agents keep working when you close the
> chat.**

That is emotionally the same story as always-on — *and for the local product on an
awake machine it is completely true.* It is free, it is shipped, and no competitor
in the Cursor/Cline/Aider set has it. Memory ("agents don't start cold") is the
second free hook. The grid is third — it's the *proof*, not the pitch.

**So: lead the free product on unattended work, not on the dashboard.**

---

## 2. What we sell (one page, memorize)

- **Clayrune** — free, MIT. Mission control for the Claude Code agents you already
  run. Many projects, one grid. Schedules, standing charters, multi-agent, memory
  that persists.
- **Clayrune Connect** — free beta, later $6.99/mo. `<you>.clayrune.io`: reach the
  agent on **your own machine** from any browser, any phone. Zero config.

**Your machine. Your Claude subscription. Your code. We never touch any of it.**

**Never:** we host Claude · sign in with Claude · we hold your credentials ·
always-on · works while your laptop is closed.

**The awake caveat, said out loud, in the product and the copy:**
> *"Clayrune runs on your machine. If your machine sleeps, so does your agent — and
> we'll tell you it did."*

**→ The single highest-value product ask on this page:** *keep the machine awake
while an agent is running.* `SetThreadExecutionState` (Win) / `caffeinate` (mac) /
`systemd-inhibit` (Linux). It is ~50 lines behind a setting. It converts our biggest
caveat from an apology into a feature line — **"Clayrune keeps your machine awake
while an agent is working"** — without touching the parked Cloud tier and without a
single dishonest word. Filed to backlog.

---

## 3. The launch is a piece of writing, not an ad

Agreed with Vector's §5: **lead with the Anthropic post.** But not as written. Three
corrections, and one of them is important.

### 3.1 ⚠️ Do not frame it as a grievance. We are a guest in their house.

*"I tried to build hosted Claude Code. Anthropic said no."* reads as a callout. Two
ways that backfires:

1. **We are a parasite on Claude Code** — our entire product depends on their CLI's
   stability and their goodwill. Picking a public fight with the vendor whose
   subprocess we shell out to is a strategic error, and it is a bad look for a tool
   asking devs to trust it.
2. **HN's reflex read** will be *"guy is annoyed he can't resell someone else's
   product."* That comment will be at the top and it will define the thread.

**Reframe to what actually happened, which is more interesting anyway:**

> **"I spent a month finding out you're not allowed to host Claude Code for other
> people. Here's the ToS, here's what it means for every BYOL project, and here's
> the thing I built instead."**

Same information. Same primary sources. Same verbatim quotes. But the posture is
*"I did the research this community needs and nobody else has done"* — which is
true, is generous, and is unattackable. And the conclusion — *"the rule is
actually right, and here's the legal shape"* — is what makes it credible rather
than sour.

### 3.2 ⚠️ Do not name-and-shame OpenClaw / OpenCode / Roo / Goose.

They are **our community**, and their users are our users. Citing them as
*"projects that hit this same wall"* is useful. Citing them as violators invites a
brigade and makes enemies of the exact people who would otherwise upvote us.

### 3.3 The post's audience wanted hosted Claude Code. We're selling the opposite.

This is the gap Vector didn't flag. The post *distributes*; it does not *convert* —
the reader came for a legal finding, and the CTA is a dashboard. **The 45-second clip
is the entire conversion mechanism.** It is the last section of the post. Which means:

> **The demo video gates the post, and the post gates the launch. The video is the
> only thing on the critical path.**

---

## 4. Sequence

### Phase A — the gate (nothing else matters until this is done)
1. **The 45s demo clip.** Spec: `docs/DEMO_VIDEO_SPEC.md`. **Human capture step.**
2. Connect working end-to-end without CF Access (backlog `02b487c3`, `1e5feb38`,
   `ee94a17e`) — the demo must be real, not staged.
3. **README = storefront.** Clip at the top, 3-line what-is-this, install in one
   block. It currently sells nothing (1 star, 0 forks).
4. Landing page: clip above the fold + the awake caveat stated plainly.
5. Sleep-inhibit setting (§2). Small, and it changes what we're allowed to say.

### Phase B — fire (one shot each, same day)
Post the essay first, product links inside it.

| Channel | The angle |
|---|---|
| **Show HN** | The essay. Title per §3.1. Answer every comment for 3 hours. |
| **r/ClaudeAI** | The clip + "I built the console I wanted." |
| **r/selfhosted** | *"Your machine, your data, MIT, bring your own tunnel."* Their religion. |
| **X** | The clip, muted-captioned. It travels on its own. |
| **`awesome-claude-code` PRs** | Cheap, durable long tail. |
| **Claude Code Discord** | Be a member. Do not advertise. |

Product Hunt is optional and weak for dev tools. Do not spend the week on it.

### Phase C — talk to every user, then price
- Literally every one. At this scale it beats any analytics.
- Watch **D7**. If D7 is bad, marketing is irrelevant and this whole document is
  wasted effort — fix the product.
- Billing on only after the hook is proven. **Grandfather the beta cohort forever.**
- Native apps here, as retention polish. **Not a launch gate** — we are already an
  installable PWA with push (`static/manifest.json`, `sw.js`,
  `mc/blueprints/push_mobile.py`).

---

## 5. Metrics

**Track:** WAU · **D7 / D30 retention** · % who enable remote · time-to-first-agent-turn.
**Ignore:** stars · signups · page views · MRR.

---

## 6. The two risks I'd actually lose sleep over

1. **Anthropic ships this.** *Claude Code on the web* exists, and being first-party
   they can do the hosted thing we legally cannot. Our ground is **the machine being
   the user's** — their repo, their env, their tools — plus orchestration a sandbox
   doesn't have. Never compete on "hosted Claude Code."
2. **D7 is bad and the hook isn't real.** Everything above assumes people come back.
   Nothing in this plan tests that. The beta exists to find out.
