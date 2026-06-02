# Hosted Clayrune — Managed-Token Income Model

**Status:** v1 (2026-06-02). Companion to `docs/HOSTED_CLOUD_PLATFORM_DESIGN.md`
(the platform) and the 2026-06-02 pricing discussion. Computed by the
parameterized model at **`docs/poc/income_model.py`** — edit the assumptions
there and re-run (`python docs/poc/income_model.py`) to test your own numbers.

> **Read this as a framework + ratios, not a promise.** The absolute dollars
> depend on three swing assumptions — turns/month per user, context size per
> turn, and the (verify-current) token rates. The *shape* of the conclusions is
> robust; the exact figures are illustrative.

---

## Decision brief — pricing & cost strategy

**Recommendation (one line):** launch **managed-token** (we resell, one bill) on
**flat phone-plan tiers with hard allowances**, **Sonnet-default / Opus held per
session**, **prompt caching on**, **terse-by-default on mobile**, and **no
automatic overage**. Base case **~35% gross margin**, **~46–55%** with the two
experience-neutral levers (caching + output budgeting) fully used.

**Why managed, not BYOK.** The launch buyer is non-technical and mobile-first.
BYOK (paste your own Anthropic key) is a conversion-killer for them — they don't
have a key and won't create one. Managed = "one signup, one bill, just works."
The cost: we become a reseller and carry the token tail (mitigated by the levers
below). BYOK stays as a power-user on-ramp / alternative, not the mainstream path.

**The shape of the business** (illustrative — see §3):

| Paying users | Gross/mo (base) | Gross/yr |
|---|---|---|
| 1,000 | ~$13K | ~$156K |
| 10,000 | ~$131K | ~$1.6M |
| 50,000 | ~$657K | ~$7.9M |

Linear at ~35% gross; the levers lift that to ~46–55% **without raising price**.

**The four levers that decide margin** (§4):

| Lever | Effect | Type |
|---|---|---|
| Prompt caching | OFF = −27% (loss) → ON = +35% | make-or-break |
| Allowance enforcement | 15% leakage → break-even | make-or-break |
| Output budgeting (terse-on-mobile) | +11 pts (35→46%) | rescue, experience-neutral |
| Usage mix | light 44% vs heavy 19% | positioning |

Two can sink it (caching, allowance); two are upside. The two experience-neutral
levers — caching + terse-on-mobile — carry most of the lift at zero quality cost.

**Locked decisions:**
- Managed tokens, flat tiers, hard allowances, **no automatic overage** (cap =
  graceful slow / user-chosen top-up — a surprise bill must be structurally
  impossible).
- **Sonnet-default; Opus held per session** — never mid-thread model switching
  (it breaks memory/continuity — the constraint we hit and reversed).
- Show **friendly units** (tasks / agent-time), never tokens.
- **Caching on**, with the per-turn verbosity hint in the **uncached tail** so it
  stacks with (doesn't bust) the cache.

**Open decisions (need your call):**
1. **Price ladder** — base model uses $20 / $49 / $99. Power at $99 is a *loss* on
   a true heavy user; reprice to ~$140–160 **or** cap its allowance to ~350–400
   turns/mo. Pick one.
2. **Free tier at launch?** Free COGS is a real P&L line (~$4.7K/mo at 1K payers).
   Recommend invite-only or paid-only until archive-and-detach ships.
3. **Token backend** — Anthropic-direct vs Google Vertex (GCP credits, but verify
   caching parity first). Gated on the Phase-0 caching test.

**Hard gates before build:** (a) prove **prompt caching** on the chosen backend in
Phase 0 — it's a go/no-go (caching-off is a money-losing business); (b)
**allowance enforcement** must be real, not cosmetic.

**Biggest uncertainty → cheapest fix.** The model rests on three guesses —
turns/month, context size/turn, cache-hit rate. A week of POC telemetry replaces
all three and turns this brief from a framework into a forecast.

---

## Scenario modeled

The locked product shape from the design + pricing talks:
- **Buyer:** non-technical, mobile-first, no-PC.
- **Billing:** **managed tokens** (we resell), flat tiers, included allowance,
  **no automatic overage** (cap = throttle/upgrade, never a surprise bill).
- **Model policy:** Sonnet-default, occasional Opus elevation (held per session).
- **Cost levers:** prompt caching + the per-tier allowance that caps the tail.

Key assumptions (all editable in the script):

| Tier | Price | Persona | Turns/mo | Opus share | Context/turn |
|---|---|---|---|---|---|
| Basic | $20 | light | 60 (~2/day) | 5% | 12K in / 1.5K out |
| Pro | $49 | medium | 200 (~7/day) | 15% | 25K in / 2K out |
| Power | $99 | heavy | 500 (~17/day) | 30% | 60K in / 3K out |

Token rates ($/Mtok, verify): Sonnet 3 / 15 / 0.30-cached · Opus 15 / 75 /
1.50-cached. Cache hit on input = 80%. Paying mix 60/30/10. Conversion 25%
(so 3 free/trial users per paying user). Fixed control-plane $150/mo + $0.05/user.

---

## [1] Per-tier unit economics (cache 80%, Sonnet-default)

| Tier | Price | Token COGS | Infra | Total COGS | Contribution | Margin |
|---|---|---|---|---|---|---|
| Basic | $20 | $2.35 | $0.95 | $3.30 | **$16.70** | **84%** |
| Pro | $49 | $16.32 | $2.20 | $18.52 | **$30.48** | **62%** |
| Power | $99 | $104.94 | $5.00 | $109.94 | **−$10.94** | **−11%** |

**The single most important row is Power.** A *true* heavy agentic user
(~500 turns/mo, 30% Opus, big contexts) costs **~$105/mo in tokens alone** —
more than a $99 tier collects. Heavy users are a **loss** at any consumer price
unless their usage is capped.

**Allowance is therefore not optional — it's the product.** Derive each tier's
allowance from the economics (turns that keep ~50% gross margin):

| Tier | Price | 50%-margin allowance | Typical usage | Verdict |
|---|---|---|---|---|
| Basic | $20 | ~231 turns/mo | 60 | huge headroom ✅ |
| Pro | $49 | ~273 turns/mo | 200 | comfortable ✅ |
| Power | $99 | ~212 turns/mo | 500 | **below typical — must cap or reprice** ⚠️ |

So either **reprice Power to ~$140–160**, or **cap Power's allowance at ~350–400
turns** (≈$75–85 COGS) and let true power users hit a throttle / opt-in add-on
beyond it. Given the no-bill-shock rule, the allowance-cap path is cleaner.

---

## [2] Blended per paying user (mix 60/30/10)

- **ARPU:** $37/user/mo
- **Blended COGS:** $19/user/mo
- **Contribution:** **$18/user/mo (~49%)** — before free-user drag and fixed cost.

---

## [3] Forecast by paying-user count (base case)

| Paying | Free | MRR | Token COGS | Free COGS | Fixed | **Gross/mo** | Margin | **Gross/yr** |
|---|---|---|---|---|---|---|---|---|
| 100 | 300 | $3,660 | $1,680 | $473 | $170 | **$1,164** | 32% | $14K |
| 1,000 | 3,000 | $36,600 | $16,797 | $4,732 | $350 | **$12,990** | 35% | $156K |
| 10,000 | 30,000 | $366,000 | $167,975 | $47,322 | $2,150 | **$131,253** | 36% | $1.58M |
| 50,000 | 150,000 | $1,830,000 | $839,873 | $236,610 | $10,150 | **$656,867** | 36% | $7.9M |

A ~**35–36% gross margin** business that scales linearly — *if* the assumptions
hold. Two things in this table deserve a stare: **token COGS is ~46% of MRR**
(it's the whole game), and **free-user COGS (~$4.7K/mo at 1K paying) is bigger
than the entire fixed control plane** — the free tier is a real cost, not a
rounding error.

---

## [4] Sensitivity at 1,000 paying users — what actually moves the needle

| Scenario | MRR | COGS | Gross/mo | Margin |
|---|---|---|---|---|
| **base case** | $36,600 | $23,610 | **$12,990** | 35% |
| **caching OFF** | $36,600 | $46,502 | **−$9,902** | **−27%** |
| great caching (92%) | $36,600 | $20,176 | $16,424 | 45% |
| mix skews heavy (40/35/25) | $49,900 | $40,368 | $9,532 | 19% |
| mix skews light (75/20/5) | $29,750 | $16,755 | $12,995 | 44% |
| 5% allowance leakage → heavy | $36,600 | $28,017 | $8,583 | 23% |
| **15% allowance leakage → heavy** | $36,600 | $36,831 | **−$231** | **−1%** |
| **terse mobile default (prose ×0.4)** | $36,600 | $19,852 | **$16,748** | **46%** |
| **caching 92% + terse** | $36,600 | $16,418 | **$20,182** | **55%** |
| 15% leakage BUT terse | $36,600 | $31,994 | $4,606 | 13% |

Four levers dominate; two can sink the business outright, two can rescue it:

1. **Caching is make-or-break.** With caching OFF the model loses money
   (−27%). With caching it's +35%. This is the difference between a business and
   a bonfire — and it's exactly why the §HOSTED `[C:S2.2]`/Vertex-caching-parity
   question matters: **if your token backend doesn't do prompt caching well, the
   whole model is underwater.** Validate caching in Phase 0.
2. **Allowance enforcement is existential, not nice-to-have.** Just **15% of
   users behaving heavier than their tier** (unenforced caps) takes the whole
   thing to break-even. The cap must be real.
3. **Mix matters.** Skew-light is healthy (44%); skew-heavy crushes it (19%).
   Marketing/positioning should attract light/medium users, and heavy users must
   be priced or capped — never silently subsidized.
4. **Output budgeting is a free +11 margin points.** Terse-by-default on mobile
   (trim the visible prose, leave tool/code alone) lifts margin 35% → **46%**,
   stacks with caching to **55%**, and even *cushions the leakage risk* (the −1%
   leakage case recovers to +13% under terse). Detail + how to do it on the fly
   in §[6]. Nearly free, and it's the lever that turns "real business" into "good
   business."

---

## [5] The caching lever, quantified

| Pattern | Turns/mo | Cached $/mo | Uncached $/mo | Caching saves |
|---|---|---|---|---|
| light | 60 | $2.35 | $4.21 | 44% |
| medium | 200 | $16.32 | $33.60 | 51% |
| heavy | 500 | $104.94 | $247.50 | 58% |

Caching saves more the heavier the user (bigger resent context = more cache
hits), so it disproportionately protects you exactly where the tail risk lives.

---

## [6] Output budgeting — the third lever (adaptive reply length)

Output tokens cost ~5x input, and once caching makes input cheap, **output is
the majority of per-turn cost.** But only the *visible prose* is trimmable — tool
calls, code, and file edits are not. So the model splits output into
`prose` + `tool`, and a "be terse" instruction only shrinks the prose:

| Pattern | prose/tool tokens | Full $/mo | Terse $/mo (prose ×0.4) | Saves |
|---|---|---|---|---|
| light | 1300 / 200 | $2.35 | $1.50 | **36%** |
| medium | 1400 / 600 | $16.32 | $12.29 | **25%** |
| heavy | 1000 / 2000 | $104.94 | $95.04 | **9%** |

Terseness helps **conversational** users (light/medium — mostly prose) a lot, and
**code-heavy** users (heavy — mostly tool output) barely. Since the launch
persona is conversational + mobile, this lever is aimed exactly right — hence the
+11 margin points in §[4].

### Doing it on the fly (adaptive output budgeting)

You can decide reply length *per turn*, and — unlike model-switching — it's
**safe**: it doesn't switch models or break continuity, it's just a varying
instruction in the turn.

- **The dial is prompt steering, not `max_tokens`.** `max_tokens` is a guillotine
  (truncates mid-sentence); use it only as a backstop. The *target* comes from a
  per-turn instruction ("1–2 sentences" / "full detail").
- **Decide cheaply, in order:** (1) **surface signal** — mobile view → terse
  default, desktop → fuller (free, MC already knows the client); (2)
  **heuristics** — message length, "quick q" vs "explain", whether code/tools are
  implicated (free); (3) a **tiny Haiku pre-classifier** for ambiguous turns →
  terse/normal/detailed (~$0.0005, ~0.4s) — same shape as the dispatch router but
  it picks *verbosity*, not model.
- **CRITICAL caching interaction:** put the per-turn verbosity hint at the **end
  of the turn** (user message / per-turn suffix), **never in the cached system
  prompt.** Mutating the cached prefix each turn busts the prompt cache — and
  §[4] shows caching is make-or-break. Hint-in-the-tail keeps the cache warm
  *and* tunes each reply. (This is load-bearing — getting it wrong trades the
  caching lever for the output lever instead of stacking them.)
- **The one risk — don't under-answer.** Clip a reply that needed depth and the
  user fires a follow-up; every new turn re-pays the full context on input. Three
  clipped turns can cost more than one tight-but-complete answer. Bias "when
  unsure, normal length."

---

## Takeaways

1. **The cost center flipped.** In the BYOK design, *storage* dominated COGS
   (committee `[C:S3.1]`). In the managed-token model, **tokens are ~90%+ of
   COGS** and storage/compute are a rounding error. The whole margin game is now
   token cost, and the whole defense is **caching + Sonnet-default + allowance +
   output budgeting** (the four levers in §[4]).
2. **Light users are gold, heavy users are a liability** at consumer prices.
   The business works by skewing the mix light and *capping* the heavy tail —
   not by pricing heavy use accurately (no consumer pays $300/mo).
3. **The allowance is the product, derived from economics.** Set
   `allowance = (price × (1 − target_margin) − infra) / blended_cost_per_turn`,
   expose it in friendly units (tasks/agent-time, never tokens), and degrade
   gracefully at the cap — no overage bill.
4. **Caching is a Phase-0 gating requirement**, not an optimization. Confirm it
   on your chosen token backend (Anthropic-direct or Vertex/Bedrock) before
   committing — caching-off is a money-losing business.
5. **Free-tier discipline is a P&L line, not UX polish.** Free COGS rivals the
   fixed control plane; pair the free tier with a small allowance + archive of
   dormant accounts (committee `[C:S3.2]`) and watch conversion.
6. **~35% gross in the base case, ~46–55% with caching + output budgeting fully
   exploited.** Still not an 80%-margin SaaS — consistent with the thin moat
   (Seat 4: convenience sold to people who can't self-host) — but the two
   experience-neutral levers (caching, terse-on-mobile) are what move it from
   "thin" to "healthy" without raising the price.

**Next refinement:** the biggest uncertainty is **turns/month and context size**
for the real persona. A week of POC telemetry (turns/session, tokens/turn, cache
hit rate) replaces the three load-bearing guesses and turns this from a framework
into a forecast.
