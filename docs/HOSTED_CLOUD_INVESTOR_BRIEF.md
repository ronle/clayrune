# Clayrune Cloud — Investor Brief

**Draft 2026-06-02.** A business-story distillation of the engineering design
(`HOSTED_CLOUD_README.md` for the technical docs). Each `## Slide` maps to one
deck slide. **Forward-looking figures are projections on stated assumptions, not
results** — the product is at design/spec stage; the underlying platform is built.

---

## Slide 1 — Vision (one line)

**Your own AI agent — always on, on your phone, with no computer to babysit.**
Clayrune Cloud runs a personal, autonomous AI workspace for you in the cloud.

---

## Slide 2 — Problem

AI agents can now do real work — write code, run automations, manage projects —
but using one today means **running it on your own PC**: install tools, keep a
machine on, technical setup. That locks out everyone who isn't a developer, and
ties even developers to a desktop. The people who'd benefit most from "an agent
that just does things for me" can't get one.

---

## Slide 3 — Solution

A hosted Clayrune workspace: a full agent stack running in the cloud, reachable
from any phone. The user **brings their own AI key** (or starts free in two
minutes with Google Gemini); we provide the always-on home, the orchestration,
the memory, and the mobile experience. No PC, no setup, no babysitting.

---

## Slide 4 — Why now

- Agents crossed the threshold from "chatbot" to "does multi-step work" in
  2025–26.
- Phones are the only computer most of the world uses daily.
- The hard parts — the multi-provider agent runtime, mobile app, scheduler,
  cross-session memory — **already exist** (Clayrune's shipped platform). The
  cloud layer is additive, not a rebuild.

---

## Slide 5 — Who we win first (the wedge)

**Non-technical, mobile-first users who literally cannot self-host.** For them
the alternative isn't "run it cheaper myself" — it's *nothing*. Convenience is
the entire product, so willingness-to-pay is real. Developers are a later,
broader market; we start where the need is sharpest and the competition is
weakest.

---

## Slide 6 — Business model (the honest, durable one)

- **Bring-Your-Own-Key (BYOK).** The user pays their AI provider directly for
  tokens. **We never touch tokens, never mark up, never meter.** This removes the
  single most dangerous line in AI-SaaS economics — unbounded, volatile model
  cost — from *our* P&L entirely.
- **We charge a transparent hosting fee** per workspace, bucketed across the real
  cost drivers (storage / network / compute). The user sees exactly what they pay
  for. No metering, no bill-shock, nothing hidden — which is why it doesn't feel
  like a "meter running."
- **Tiers $10 / $19 / $39 / $79** (GCP-rate-verified), deliberately consumer-priced.

---

## Slide 7 — Unit economics (the strength)

Because tokens are the user's cost, **our COGS is bounded infrastructure** —
storage (the dominant line), compute (scales toward zero when idle), network.
No token tail, no margin compression when model prices swing.

- Blended **~30% gross margin** at consumer prices (verified GCP rates).
- Marginal cost of an *active* user is cents; the real cost is *storage of
  dormant* users — a controllable line (cold-tier archiving).
- Predictable, defensible unit economics in a category where most players ride a
  volatile token-margin rollercoaster.

---

## Slide 8 — Revenue projection

Mix: most users on the entry tiers; ARPU ~$20/mo. **Projection, not a forecast of
demand** — it sizes the model, not the market.

| Paying users | ARR (revenue) | Gross profit/yr | Margin |
|---|---|---|---|
| 10,000 | ~$2.5M | ~$0.73M | ~30% |
| 50,000 | ~$12.3M | ~$3.7M | ~30% |
| 100,000 | ~$24.6M | ~$7.4M | ~30% |

Levers: tier mix (a premium skew adds ~50% gross) and free-tier discipline.

---

## Slide 9 — Moat (clear-eyed)

We don't claim a technical moat — the core is open and the orchestration is
commodity cloud. The defensibility that *compounds* is:

- **Data gravity.** A user's projects, work product, and the agent's accumulated
  memory live on the platform. Switching cost rises every week they use it — the
  same lock-in that makes Notion/Airtable sticky.
- **Convenience.** Our wedge users can't self-host; "we run it for you" is the value.
- **Brand + provider-neutrality.** Any model, one home, on your phone — and no
  dependence on a single AI vendor's pricing or terms.

The moat grows with usage, not with a patent.

---

## Slide 10 — Onboarding edge

BYOK's usual killer is "create an API key" — a developer chore. We remove it:
**start free in ~2 minutes with Google Gemini (no credit card)**, add a Claude
key later for top quality via a guided, validated wizard. Two-minute activation
for a non-technical user is the difference between a funnel and a wall.

---

## Slide 11 — Status & roadmap

- **Built (today):** the Clayrune platform — multi-provider agent runtime, mobile
  app, scheduler, hivemind, cross-session memory, remote access. Real, shipped.
- **Designed & reviewed:** the cloud layer — committee-ratified design, verified
  pricing, onboarding spec.
- **Build path:** Phase 0 spike (one host, Gemini) → control plane → sleep/wake →
  productize → scale. Capital accelerates this; it is not blocked on invention.

---

## Slide 12 — Risks (and how they're handled)

- **Thin technical moat** → answered by data-gravity lock-in + convenience wedge.
- **Provider dependency / terms** (e.g., Anthropic's OAuth restrictions) →
  mitigated by **provider-neutral BYOK** and API-key-only compliance.
- **Dormant-user storage cost** → cold-tier archiving; no open free tier until it
  ships.
- **Network-egress tail** → bucketed + capped; the one usage-correlated line.
- **Execution risk on assisted onboarding** → de-risked by the Gemini-free path.

---

## Slide 13 — The ask

*(To complete — funding amount, use of funds: build-out + GCP infra + go-to-market,
and milestone/target the round gets us to.)*

---

### Notes for turning this into a deck
- Slides 6–8 are the heart: BYOK removes token risk → bounded cost → predictable
  ~30% margin → scalable ARR. Lead the financial story there.
- Keep the moat slide (9) honest — sophisticated investors reward clarity over a
  fake "we have a patent" claim, and data-gravity is a moat they recognize.
- Numbers trace to `docs/poc/revenue_forecast.py` + `bucket_pricing.py` (GCP rates
  verified 2026-06-02). Re-run with real telemetry before the raise to replace
  assumed user-mix with measured.
