#!/usr/bin/env python3
"""
Hosted Clayrune — managed-token SaaS income model.

A parameterized forecast for the hosted-compute product under the LOCKED-IN
shape from the 2026-06-01 design + the 2026-06-02 pricing discussion:

  - Buyer: non-technical, mobile-first, no-PC users.
  - Billing: MANAGED tokens (we resell), flat tiers, included allowance,
    NO automatic overage (cap = throttle/upgrade, never a surprise bill).
  - Model policy: Sonnet-default, occasional Opus elevation (held per session).
  - Cost levers: prompt caching + the per-tier allowance that caps the tail.

Everything below is an ASSUMPTION you can edit. The point is the *framework*
and the *ratios*, not false precision. Token $/Mtok are approximate public
rates — verify current Anthropic pricing before trusting absolute dollars.

Run:  python docs/poc/income_model.py
"""

# ─────────────────────────────────────────────────────────────────────────────
# 1. Model token pricing ($ per million tokens). VERIFY current rates.
# ─────────────────────────────────────────────────────────────────────────────
PRICE = {
    # model:   (input, output, cached_input)   $/Mtok
    "sonnet": (3.00, 15.00, 0.30),   # cached input ~= 10% of input
    "opus":   (15.00, 75.00, 1.50),
}
CACHE_HIT = 0.80   # fraction of INPUT tokens served from cache on a typical turn
                   # (system prompt + conversation prefix + stable context)

# ─────────────────────────────────────────────────────────────────────────────
# 2. Usage patterns for the non-technical / mobile persona.
#    input/output tokens are PER TURN; turns_mo is turns per month.
#    opus_share = fraction of this user's turns that run on Opus.
# ─────────────────────────────────────────────────────────────────────────────
PATTERNS = {
    #            input/turn  output/turn  opus_share  turns/mo
    "light":  dict(inp=12_000, out=1_500, opus=0.05, turns=60),    # ~2 turns/day
    "medium": dict(inp=25_000, out=2_000, opus=0.15, turns=200),   # ~7 turns/day
    "heavy":  dict(inp=60_000, out=3_000, opus=0.30, turns=500),   # ~17 turns/day
}

# Infra cost per user per month (NON-token): storage (GB * $/GB) + compute.
STORAGE_PER_GB = 0.15
INFRA = {
    "light":  dict(gb=3,  compute=0.50),
    "medium": dict(gb=8,  compute=1.00),
    "heavy":  dict(gb=20, compute=2.00),
}

# ─────────────────────────────────────────────────────────────────────────────
# 3. Tiers (price) and the paying-user MIX. Allowance is DERIVED (see report).
# ─────────────────────────────────────────────────────────────────────────────
TIERS = {
    "basic": dict(price=20,  pattern="light"),
    "pro":   dict(price=49,  pattern="medium"),
    "power": dict(price=99,  pattern="heavy"),
}
MIX = {"basic": 0.60, "pro": 0.30, "power": 0.10}   # of PAYING users

# Free/trial: cost-bearing, no revenue. 75% of signups => 25% conversion.
CONVERSION = 0.25
FREE_TURNS_MO = 25
FREE_GB = 2

# Fixed control-plane (auth/relay/orchestrator), monthly, + tiny per-user.
FIXED_BASE = 150.0
FIXED_PER_USER = 0.05

USER_COUNTS = [100, 1_000, 10_000, 50_000]   # PAYING users


# ─────────────────────────────────────────────────────────────────────────────
# Engine
# ─────────────────────────────────────────────────────────────────────────────
def turn_cost(inp, out, model, cache_hit):
    p_in, p_out, p_cached = PRICE[model]
    eff_in = (1 - cache_hit) * p_in + cache_hit * p_cached          # $/Mtok
    return (inp * eff_in + out * p_out) / 1_000_000


def blended_turn_cost(pat, cache_hit=CACHE_HIT):
    p = PATTERNS[pat]
    s = turn_cost(p["inp"], p["out"], "sonnet", cache_hit)
    o = turn_cost(p["inp"], p["out"], "opus", cache_hit)
    return (1 - p["opus"]) * s + p["opus"] * o


def token_cogs(pat, cache_hit=CACHE_HIT):
    return blended_turn_cost(pat, cache_hit) * PATTERNS[pat]["turns"]


def infra_cogs(pat):
    i = INFRA[pat]
    return i["gb"] * STORAGE_PER_GB + i["compute"]


def tier_economics(tier, cache_hit=CACHE_HIT):
    t = TIERS[tier]
    pat = t["pattern"]
    tok = token_cogs(pat, cache_hit)
    inf = infra_cogs(pat)
    cogs = tok + inf
    contrib = t["price"] - cogs
    margin = contrib / t["price"] if t["price"] else 0
    return dict(price=t["price"], pattern=pat, token=tok, infra=inf,
                cogs=cogs, contrib=contrib, margin=margin)


def free_user_cogs(cache_hit=CACHE_HIT):
    # Free user ~ light per-turn profile, fewer turns.
    per_turn = blended_turn_cost("light", cache_hit)
    return per_turn * FREE_TURNS_MO + FREE_GB * STORAGE_PER_GB + 0.30


def fixed_cost(total_users):
    return FIXED_BASE + FIXED_PER_USER * total_users


def forecast(n_paying, cache_hit=CACHE_HIT, mix=MIX, leak=0.0):
    """leak = fraction of paying users who behave one pattern HEAVIER than
    their tier's allowance assumes (allowance not enforced). Models the tail."""
    mrr = cogs_tok = cogs_inf = 0.0
    for tier, share in mix.items():
        cnt = n_paying * share
        e = tier_economics(tier, cache_hit)
        mrr += cnt * e["price"]
        cogs_tok += cnt * e["token"]
        cogs_inf += cnt * e["infra"]
    # leakage: `leak` of users consume HEAVY token cogs regardless of tier
    if leak:
        heavy_tok = token_cogs("heavy", cache_hit)
        leaked = n_paying * leak
        # replace their (avg) token cogs with heavy
        avg_tok = cogs_tok / n_paying
        cogs_tok += leaked * (heavy_tok - avg_tok)

    n_free = n_paying * (1 - CONVERSION) / CONVERSION
    free_cogs = n_free * free_user_cogs(cache_hit)
    total_users = n_paying + n_free
    fixed = fixed_cost(total_users)

    cogs = cogs_tok + cogs_inf + free_cogs + fixed
    gross = mrr - cogs
    return dict(n_paying=n_paying, n_free=n_free, mrr=mrr, cogs_tok=cogs_tok,
                cogs_inf=cogs_inf, free_cogs=free_cogs, fixed=fixed,
                cogs=cogs, gross=gross,
                margin=(gross / mrr if mrr else 0))


# ─────────────────────────────────────────────────────────────────────────────
# Report
# ─────────────────────────────────────────────────────────────────────────────
def money(x):
    return f"${x:,.0f}"


def main():
    print("=" * 78)
    print("HOSTED CLAYRUNE — MANAGED-TOKEN INCOME MODEL")
    print("=" * 78)

    print("\n[1] PER-TIER UNIT ECONOMICS (cache hit = {:.0%}, Sonnet-default)\n"
          .format(CACHE_HIT))
    hdr = f"{'tier':7} {'price':>6} {'pattern':8} {'turns/mo':>8} " \
          f"{'$/turn':>8} {'token$':>8} {'infra$':>7} {'COGS$':>7} " \
          f"{'contrib$':>9} {'margin':>7}"
    print(hdr)
    print("-" * len(hdr))
    for tier in TIERS:
        e = tier_economics(tier)
        p = PATTERNS[e["pattern"]]
        print(f"{tier:7} {money(e['price']):>6} {e['pattern']:8} "
              f"{p['turns']:>8} {blended_turn_cost(e['pattern']):>8.3f} "
              f"{e['token']:>8.2f} {e['infra']:>7.2f} {e['cogs']:>7.2f} "
              f"{e['contrib']:>9.2f} {e['margin']:>6.0%}")
    print("\n  Derived allowance (turns/mo to keep 50% gross margin):")
    for tier in TIERS:
        e = tier_economics(tier)
        budget = e["price"] * 0.50 - e["infra"]      # token budget at 50% margin
        per_turn = blended_turn_cost(e["pattern"])
        allow = budget / per_turn if per_turn > 0 else 0
        flag = "  <-- below this user's typical usage!" \
            if allow < PATTERNS[e["pattern"]]["turns"] else ""
        print(f"    {tier:7} {money(e['price']):>5} -> ~{allow:>5.0f} turns/mo"
              f"  (typical {PATTERNS[e['pattern']]['turns']}){flag}")

    print("\n[2] BLENDED PER-PAYING-USER (mix: "
          + ", ".join(f"{k} {v:.0%}" for k, v in MIX.items()) + ")")
    arpu = sum(TIERS[t]["price"] * s for t, s in MIX.items())
    wcogs = sum((tier_economics(t)["cogs"]) * s for t, s in MIX.items())
    print(f"    ARPU            {money(arpu)}/user/mo")
    print(f"    blended COGS    {money(wcogs)}/user/mo")
    print(f"    contribution    {money(arpu - wcogs)}/user/mo  "
          f"({(arpu - wcogs) / arpu:.0%} margin, pre-free, pre-fixed)")

    print("\n[3] FORECAST by paying-user count (base case)\n")
    hdr = f"{'paying':>8} {'free':>8} {'MRR':>12} {'tokenCOGS':>12} " \
          f"{'freeCOGS':>10} {'fixed':>8} {'grossProfit':>13} {'margin':>7}"
    print(hdr)
    print("-" * len(hdr))
    for n in USER_COUNTS:
        f = forecast(n)
        print(f"{n:>8,} {f['n_free']:>8,.0f} {money(f['mrr']):>12} "
              f"{money(f['cogs_tok']):>12} {money(f['free_cogs']):>10} "
              f"{money(f['fixed']):>8} {money(f['gross']):>13} "
              f"{f['margin']:>6.0%}")
    print("    (annualized gross at each: "
          + ", ".join(f"{n:,}=>{money(forecast(n)['gross']*12)}"
                      for n in USER_COUNTS) + ")")

    print("\n[4] SENSITIVITY at 1,000 paying users (what moves the needle)\n")
    base = forecast(1_000)
    rows = [
        ("base case", base),
        ("caching OFF (cache_hit=0)", forecast(1_000, cache_hit=0.0)),
        ("great caching (cache_hit=0.92)", forecast(1_000, cache_hit=0.92)),
        ("mix skews heavy (40/35/25)", forecast(1_000,
            mix={"basic": 0.40, "pro": 0.35, "power": 0.25})),
        ("mix skews light (75/20/5)", forecast(1_000,
            mix={"basic": 0.75, "pro": 0.20, "power": 0.05})),
        ("5% allowance leakage to heavy", forecast(1_000, leak=0.05)),
        ("15% allowance leakage to heavy", forecast(1_000, leak=0.15)),
    ]
    hdr = f"{'scenario':32} {'MRR':>10} {'COGS':>10} {'gross/mo':>10} {'margin':>7}"
    print(hdr)
    print("-" * len(hdr))
    for name, f in rows:
        print(f"{name:32} {money(f['mrr']):>10} {money(f['cogs']):>10} "
              f"{money(f['gross']):>10} {f['margin']:>6.0%}")

    print("\n[5] CACHING LEVER — token COGS per pattern, cached vs uncached\n")
    hdr = f"{'pattern':8} {'turns/mo':>8} {'cached$/mo':>11} " \
          f"{'uncached$/mo':>13} {'cache saves':>12}"
    print(hdr)
    print("-" * len(hdr))
    for pat in PATTERNS:
        c = token_cogs(pat, CACHE_HIT)
        u = token_cogs(pat, 0.0)
        print(f"{pat:8} {PATTERNS[pat]['turns']:>8} {c:>11.2f} "
              f"{u:>13.2f} {1 - c / u:>11.0%}")

    print("\n" + "=" * 78)
    print("Edit the ASSUMPTIONS at the top and re-run. Ratios are robust;")
    print("absolute $ depend on turns/mo, context size, and verified token rates.")
    print("=" * 78)


if __name__ == "__main__":
    main()
