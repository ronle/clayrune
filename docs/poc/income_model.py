#!/usr/bin/env python3
"""
Hosted Clayrune — managed-token SaaS income model.

A parameterized forecast for the hosted-compute product under the shape settled
across the 2026-06-01 design + 2026-06-02 pricing/output discussions:

  - Buyer: non-technical, mobile-first, no-PC users.
  - Billing: MANAGED tokens (we resell), flat tiers, included allowance,
    NO automatic overage (cap = throttle/upgrade, never a surprise bill).
  - Model policy: Sonnet-default, occasional Opus elevation (held per session).
  - Cost levers: prompt caching + per-tier allowance + ADAPTIVE OUTPUT BUDGETING.

Everything below is an ASSUMPTION you can edit. The point is the *framework*
and the *ratios*, not false precision. Token $/Mtok are approximate public
rates — verify current Anthropic pricing before trusting absolute dollars.

OUTPUT MODEL (2026-06-02): output tokens are split into `out_prose` (the visible
reply — trimmable by a "be terse" instruction) and `out_tool` (tool calls / code
/ file edits — NOT trimmable by terseness). CONCISE_PROSE is the keep-fraction of
prose (1.0 = no trim, 0.4 = keep 40%). This is why terseness helps conversational
(light/medium) users far more than code-heavy (heavy) ones.

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
CACHE_HIT = 0.80     # fraction of INPUT tokens served from cache on a typical turn
CONCISE_PROSE = 1.0  # keep-fraction of PROSE output (1.0=full, 0.4=terse). Global
                     # default; per-scenario overrides below.

# ─────────────────────────────────────────────────────────────────────────────
# 2. Usage patterns for the non-technical / mobile persona.
#    inp = input tokens/turn; out_prose + out_tool = output tokens/turn.
#    opus = fraction of this user's turns on Opus; turns = turns/month.
# ─────────────────────────────────────────────────────────────────────────────
PATTERNS = {
    #            input/turn  prose/turn  tool/turn  opus   turns/mo
    "light":  dict(inp=12_000, out_prose=1_300, out_tool=200,   opus=0.05, turns=60),
    "medium": dict(inp=25_000, out_prose=1_400, out_tool=600,   opus=0.15, turns=200),
    "heavy":  dict(inp=60_000, out_prose=1_000, out_tool=2_000, opus=0.30, turns=500),
}

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
    "basic": dict(price=20, pattern="light"),
    "pro":   dict(price=49, pattern="medium"),
    "power": dict(price=99, pattern="heavy"),
}
MIX = {"basic": 0.60, "pro": 0.30, "power": 0.10}

CONVERSION = 0.25
FREE_TURNS_MO = 25
FREE_GB = 2

FIXED_BASE = 150.0
FIXED_PER_USER = 0.05

USER_COUNTS = [100, 1_000, 10_000, 50_000]


# ─────────────────────────────────────────────────────────────────────────────
# Engine
# ─────────────────────────────────────────────────────────────────────────────
def eff_output(pat, concise):
    p = PATTERNS[pat]
    return p["out_prose"] * concise + p["out_tool"]


def turn_cost(inp, out, model, cache_hit):
    p_in, p_out, p_cached = PRICE[model]
    eff_in = (1 - cache_hit) * p_in + cache_hit * p_cached          # $/Mtok
    return (inp * eff_in + out * p_out) / 1_000_000


def blended_turn_cost(pat, cache_hit=CACHE_HIT, concise=None):
    if concise is None:
        concise = CONCISE_PROSE
    p = PATTERNS[pat]
    out = eff_output(pat, concise)
    s = turn_cost(p["inp"], out, "sonnet", cache_hit)
    o = turn_cost(p["inp"], out, "opus", cache_hit)
    return (1 - p["opus"]) * s + p["opus"] * o


def token_cogs(pat, cache_hit=CACHE_HIT, concise=None):
    return blended_turn_cost(pat, cache_hit, concise) * PATTERNS[pat]["turns"]


def infra_cogs(pat):
    i = INFRA[pat]
    return i["gb"] * STORAGE_PER_GB + i["compute"]


def tier_economics(tier, cache_hit=CACHE_HIT, concise=None):
    t = TIERS[tier]
    pat = t["pattern"]
    tok = token_cogs(pat, cache_hit, concise)
    inf = infra_cogs(pat)
    cogs = tok + inf
    contrib = t["price"] - cogs
    return dict(price=t["price"], pattern=pat, token=tok, infra=inf,
                cogs=cogs, contrib=contrib,
                margin=(contrib / t["price"] if t["price"] else 0))


def free_user_cogs(cache_hit=CACHE_HIT, concise=None):
    per_turn = blended_turn_cost("light", cache_hit, concise)
    return per_turn * FREE_TURNS_MO + FREE_GB * STORAGE_PER_GB + 0.30


def fixed_cost(total_users):
    return FIXED_BASE + FIXED_PER_USER * total_users


def forecast(n_paying, cache_hit=CACHE_HIT, mix=MIX, leak=0.0, concise=None):
    mrr = cogs_tok = cogs_inf = 0.0
    for tier, share in mix.items():
        cnt = n_paying * share
        e = tier_economics(tier, cache_hit, concise)
        mrr += cnt * e["price"]
        cogs_tok += cnt * e["token"]
        cogs_inf += cnt * e["infra"]
    if leak:
        heavy_tok = token_cogs("heavy", cache_hit, concise)
        leaked = n_paying * leak
        avg_tok = cogs_tok / n_paying
        cogs_tok += leaked * (heavy_tok - avg_tok)

    n_free = n_paying * (1 - CONVERSION) / CONVERSION
    free_cogs = n_free * free_user_cogs(cache_hit, concise)
    fixed = fixed_cost(n_paying + n_free)
    cogs = cogs_tok + cogs_inf + free_cogs + fixed
    gross = mrr - cogs
    return dict(n_paying=n_paying, n_free=n_free, mrr=mrr, cogs_tok=cogs_tok,
                cogs_inf=cogs_inf, free_cogs=free_cogs, fixed=fixed,
                cogs=cogs, gross=gross, margin=(gross / mrr if mrr else 0))


# ─────────────────────────────────────────────────────────────────────────────
# Report
# ─────────────────────────────────────────────────────────────────────────────
def money(x):
    return f"${x:,.0f}"


def main():
    print("=" * 78)
    print("HOSTED CLAYRUNE - MANAGED-TOKEN INCOME MODEL")
    print("=" * 78)

    print("\n[1] PER-TIER UNIT ECONOMICS (cache={:.0%}, Sonnet-default, full prose)\n"
          .format(CACHE_HIT))
    hdr = f"{'tier':7} {'price':>6} {'pattern':8} {'turns/mo':>8} " \
          f"{'$/turn':>8} {'token$':>8} {'infra$':>7} {'COGS$':>7} " \
          f"{'contrib$':>9} {'margin':>7}"
    print(hdr); print("-" * len(hdr))
    for tier in TIERS:
        e = tier_economics(tier)
        p = PATTERNS[e["pattern"]]
        print(f"{tier:7} {money(e['price']):>6} {e['pattern']:8} "
              f"{p['turns']:>8} {blended_turn_cost(e['pattern']):>8.3f} "
              f"{e['token']:>8.2f} {e['infra']:>7.2f} {e['cogs']:>7.2f} "
              f"{e['contrib']:>9.2f} {e['margin']:>6.0%}")

    print("\n[2] BLENDED PER-PAYING-USER (mix: "
          + ", ".join(f"{k} {v:.0%}" for k, v in MIX.items()) + ")")
    arpu = sum(TIERS[t]["price"] * s for t, s in MIX.items())
    wcogs = sum(tier_economics(t)["cogs"] * s for t, s in MIX.items())
    print(f"    ARPU {money(arpu)} | blended COGS {money(wcogs)} | "
          f"contribution {money(arpu - wcogs)} ({(arpu - wcogs)/arpu:.0%})")

    print("\n[3] FORECAST by paying-user count (base case)\n")
    hdr = f"{'paying':>8} {'free':>8} {'MRR':>12} {'tokenCOGS':>12} " \
          f"{'freeCOGS':>10} {'grossProfit':>13} {'margin':>7}"
    print(hdr); print("-" * len(hdr))
    for n in USER_COUNTS:
        f = forecast(n)
        print(f"{n:>8,} {f['n_free']:>8,.0f} {money(f['mrr']):>12} "
              f"{money(f['cogs_tok']):>12} {money(f['free_cogs']):>10} "
              f"{money(f['gross']):>13} {f['margin']:>6.0%}")

    print("\n[4] OUTPUT-BUDGETING LEVER - token COGS per pattern by prose trim\n")
    print("   (full=keep 100% of visible reply; terse=keep 40%; tool/code unaffected)")
    hdr = f"{'pattern':8} {'prose/tool':>11} {'full $/mo':>10} " \
          f"{'terse $/mo':>11} {'saves':>7}"
    print(hdr); print("-" * len(hdr))
    for pat in PATTERNS:
        p = PATTERNS[pat]
        full = token_cogs(pat, concise=1.0)
        terse = token_cogs(pat, concise=0.4)
        split = f"{p['out_prose']}/{p['out_tool']}"
        print(f"{pat:8} {split:>11} {full:>10.2f} {terse:>11.2f} "
              f"{1 - terse/full:>6.0%}")
    print("   -> terseness helps conversational (light/medium, mostly prose)")
    print("      far more than code-heavy (heavy, mostly tool output).")

    print("\n[5] SENSITIVITY at 1,000 paying users\n")
    rows = [
        ("base case", forecast(1_000)),
        ("caching OFF", forecast(1_000, cache_hit=0.0)),
        ("great caching (92%)", forecast(1_000, cache_hit=0.92)),
        ("terse mobile default (prose x0.4)", forecast(1_000, concise=0.4)),
        ("caching 92% + terse (prose x0.4)", forecast(1_000, cache_hit=0.92, concise=0.4)),
        ("mix skews heavy (40/35/25)", forecast(1_000,
            mix={"basic": 0.40, "pro": 0.35, "power": 0.25})),
        ("15% allowance leakage to heavy", forecast(1_000, leak=0.15)),
        ("15% leakage BUT terse (prose x0.4)", forecast(1_000, leak=0.15, concise=0.4)),
    ]
    hdr = f"{'scenario':38} {'MRR':>9} {'COGS':>9} {'gross/mo':>9} {'margin':>7}"
    print(hdr); print("-" * len(hdr))
    for name, f in rows:
        print(f"{name:38} {money(f['mrr']):>9} {money(f['cogs']):>9} "
              f"{money(f['gross']):>9} {f['margin']:>6.0%}")

    print("\n" + "=" * 78)
    print("Edit ASSUMPTIONS at top + re-run. Output split (prose/tool) and the")
    print("CONCISE_PROSE knob model adaptive output budgeting; ratios are robust.")
    print("=" * 78)


if __name__ == "__main__":
    main()
