#!/usr/bin/env python3
"""
Hosted Clayrune — WINDOW + MULTIPLIER pricing model (Anthropic-style).

The "different approach" (2026-06-02): instead of a visible monthly turn
ALLOWANCE (which trains users to ration — wrong for a "use more, create more"
platform), package tiers the way Anthropic does its consumer plans:

  - Relative MULTIPLIERS the user sees ("Plus / Pro / Max", "3x / 8x more"),
    never a turn count.
  - A rolling TIME WINDOW (e.g. 5h) as the throttle: can't binge a month in a
    day; abuse bounded by time, not a monthly cliff.
  - Tiers buy MORE turns of the SAME weight (this is what makes the multiplier
    honest — the first cut failed because higher tiers did *heavier* turns, so
    the multipliers collapsed to ~1x).
  - PRICE is DERIVED from the headroom so worst-case keeps a floor margin.

Two ladders are printed: a 3-rung Anthropic-style one (big honest jumps) and a
5-rung gentle one (~2x steps) — because honest multipliers FORCE big jumps, and
the only real fix for "the jumps feel high" is more rungs, not smaller ratios.

Run:  python docs/poc/pricing_windows.py
"""

PRICE = {"sonnet": (3.00, 15.00, 0.30), "opus": (15.00, 75.00, 1.50)}  # $/Mtok
CACHE_HIT = 0.85       # optimized (committed lever)
CONCISE = 0.5          # terse-on-mobile keep-fraction of prose (committed lever)
FLOOR_MARGIN = 0.35    # worst-case (maxed to cap) must still keep this margin

# ONE product-wide turn profile (same weight across tiers — see docstring).
TURN = dict(inp=25_000, prose=1_400, tool=600)
OPUS_DEFAULT = 0.08    # Sonnet-default, occasional Opus (the locked policy)

WINDOWS_PER_MONTH = (24 / 5) * 30   # 5h window => 144/mo
BASE_CAP = 300                       # Plus (1x) sustained turns/mo at floor margin


def turn_cost(inp, out, model):
    p_in, p_out, p_cached = PRICE[model]
    eff_in = (1 - CACHE_HIT) * p_in + CACHE_HIT * p_cached
    return (inp * eff_in + out * p_out) / 1_000_000


def per_turn(opus=OPUS_DEFAULT):
    out = TURN["prose"] * CONCISE + TURN["tool"]
    s = turn_cost(TURN["inp"], out, "sonnet")
    o = turn_cost(TURN["inp"], out, "opus")
    return (1 - opus) * s + opus * o


def nice_price(x):
    """Round up to a friendly $...9 price point."""
    import math
    if x <= 10:
        return max(9, math.ceil(x))
    return int(math.ceil(x / 10.0) * 10 - 1)   # -> 19, 29, 79, 149, 299 ...


def derive_price(mult, infra):
    cap = BASE_CAP * mult
    raw = (cap * per_turn() + infra) / (1 - FLOOR_MARGIN)
    return nice_price(raw), cap


def show_ladder(title, ladder):
    pt = per_turn()
    print(f"\n{title}\n")
    hdr = (f"{'tier':9} {'mult':>5} {'price':>7} {'headroom/mo':>11} "
           f"{'/5h window':>10} {'exp.use':>8} {'exp.COGS':>9} {'exp.margin':>10}")
    print(hdr); print("-" * len(hdr))
    prev = None
    for name, mult, util in ladder:
        infra = 1.0 + 0.3 * mult
        price, cap = derive_price(mult, infra)
        exp = cap * util
        exp_cogs = exp * pt + infra
        exp_margin = (price - exp_cogs) / price
        jump = f"  (x{price/prev:.1f})" if prev else ""
        print(f"{name:9} {mult:>4}x ${price:>5}/mo {cap:>11.0f} "
              f"{cap/WINDOWS_PER_MONTH:>10.1f} {exp:>8.0f} ${exp_cogs:>7.2f} "
              f"{exp_margin:>9.0%}{jump}")
        prev = price


def main():
    print("=" * 82)
    print("HOSTED CLAYRUNE - WINDOW + MULTIPLIER PRICING (Anthropic-style)")
    print(f"  optimized: cache {CACHE_HIT:.0%}, terse prose x{CONCISE}, "
          f"Sonnet-default ({OPUS_DEFAULT:.0%} Opus), 5h window, "
          f"floor margin {FLOOR_MARGIN:.0%}")
    print(f"  product-wide cost/turn = ${per_turn():.3f}  (base headroom {BASE_CAP}/mo = 1x)")
    print("=" * 82)

    # 3-rung, Anthropic-style (big honest jumps)
    show_ladder("[A] 3-RUNG LADDER (Anthropic-style honest multipliers)", [
        ("Plus", 1, 0.35),
        ("Pro", 3, 0.35),
        ("Max", 8, 0.30),
    ])
    print("\n   Honest 1x/3x/8x headroom -> big jumps, by nature (8x headroom ~ 8x")
    print("   price). This is why Anthropic is $20 / $100 / $200.")

    # 5-rung, gentle (~2x steps)
    show_ladder("[B] 5-RUNG LADDER (gentle ~2x steps — the fix for 'jumps feel high')", [
        ("Starter", 1, 0.35),
        ("Plus", 2, 0.35),
        ("Pro", 4, 0.32),
        ("Max", 8, 0.30),
        ("Studio", 16, 0.25),
    ])
    print("\n   Same economics; more rungs => each step ~2x and the user lands on a")
    print("   nearby tier instead of facing one scary jump. Top still reaches 'power'.")

    # The top-tier lever
    print("\n[C] TOP-TIER LEVER — Sonnet discipline buys headroom (same price)\n")
    base = per_turn(OPUS_DEFAULT)
    for label, opus in [("Sonnet-default (8% Opus)", 0.08),
                        ("balanced (20% Opus)", 0.20),
                        ("Opus-heavy (40%)", 0.40)]:
        pt = per_turn(opus)
        # headroom you can give for a fixed $99 at floor margin
        cap99 = (99 * (1 - FLOOR_MARGIN) - 4) / pt
        print(f"   {label:26} cost/turn ${pt:.3f}  ->  $99 buys {cap99:>5.0f} turns/mo")
    print("   Opus is ~5x Sonnet: the more you hold Opus for when it's truly")
    print("   needed, the more generous every tier feels at the same price.")

    print("\n[D] WHAT THE USER SEES (5-rung) — headroom, never counts\n")
    for name, mult, _ in [("Starter", 1, 0), ("Plus", 2, 0), ("Pro", 4, 0),
                          ("Max", 8, 0), ("Studio", 16, 0)]:
        price, _ = derive_price(mult, 1.0 + 0.3 * mult)
        label = "baseline" if mult == 1 else f"~{mult}x Starter"
        print(f"   {name:8} ${price:>3}/mo   {label:14} "
              f"{'— get going' if mult==1 else 'more headroom to create'}")
    print("   (throttle only on sustained bursts; never a bill, never a count)")

    print("\n" + "=" * 82)
    print("Same economics as the allowance model; better PACKAGING (multipliers +")
    print("time-window throttle). Two honest truths the math forces:")
    print(" 1. Real multipliers => real jumps; more RUNGS is the fix, not smaller ratios.")
    print(" 2. The top tier is only generous if it stays Sonnet-mostly.")
    print("=" * 82)


if __name__ == "__main__":
    main()
