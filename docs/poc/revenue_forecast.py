#!/usr/bin/env python3
"""
Hosted Clayrune — REVENUE FORECAST for the BYOK + bucketed-tier model.

Revenue = the named-tier prices ($10/$19/$39/$79). NO token revenue (BYOK — the
user pays their own provider). COGS = our GCP cost per tier (storage + egress +
compute + control plane) — bounded, no token tail. We forecast across user counts
and a mix of tiers, mirroring the earlier (managed-token) forecast but on the
chosen honest model.

Tier prices + costs are imported from `bucket_pricing.py` (single source of
truth, GCP rates verified 2026-06-02). Edit the MIX / counts and re-run.

Run:  python docs/poc/revenue_forecast.py
"""
import bucket_pricing as bp

# Per-tier (price, our GCP cost) derived from the verified bucket model.
TIERS = {}
for name, price, s_gb, e_gb, comp in bp.TIERS:
    cost = (bp.storage_cost(s_gb) + bp.egress_cost(e_gb)
            + bp.compute_cost(comp) + bp.PLATFORM_COST)
    TIERS[name] = dict(price=price, cost=cost)

# User mix across tiers (consumer skew: most on entry). Tune freely.
MIX = {"Lite": 0.50, "Plus": 0.30, "Pro": 0.15, "Studio": 0.05}

USER_COUNTS = [1_000, 10_000, 50_000, 100_000]

# Control plane beyond the per-workspace PLATFORM_COST already in tier cost.
FIXED_BASE = 500.0
FIXED_PER_USER = 0.10

# A free/trial user: storage floor + a little sleep compute + platform. Drag only.
FREE_COST = bp.storage_cost(5) + bp.compute_cost("Sleep") * 0.3 + 1.0


def blended(mix):
    arpu = sum(TIERS[t]["price"] * s for t, s in mix.items())
    cogs = sum(TIERS[t]["cost"] * s for t, s in mix.items())
    return arpu, cogs


def forecast(n, mix=MIX, free_per_paying=0.0):
    arpu, wcogs = blended(mix)
    mrr = n * arpu
    var_cogs = n * wcogs
    free_cogs = n * free_per_paying * FREE_COST
    fixed = FIXED_BASE + FIXED_PER_USER * n * (1 + free_per_paying)
    cogs = var_cogs + free_cogs + fixed
    gross = mrr - cogs
    return dict(mrr=mrr, cogs=cogs, gross=gross,
                margin=(gross / mrr if mrr else 0))


def money(x):
    return f"${x:,.0f}"


def main():
    print("=" * 80)
    print("HOSTED CLAYRUNE — REVENUE FORECAST (BYOK + bucketed tiers)")
    print("=" * 80)

    print("\n[1] Per-tier economics (from verified bucket model)\n")
    hdr = f"{'tier':8} {'price':>6} {'our cost':>9} {'gross$':>7} {'margin':>7}"
    print(hdr); print("-" * len(hdr))
    for t, v in TIERS.items():
        g = v["price"] - v["cost"]
        print(f"{t:8} {money(v['price']):>6} {'$%.2f'%v['cost']:>9} "
              f"{'$%.1f'%g:>7} {g/v['price']:>6.0%}")

    arpu, wcogs = blended(MIX)
    print("\n[2] Blended per paying user (mix "
          + ", ".join(f"{k} {v:.0%}" for k, v in MIX.items()) + ")")
    print(f"    ARPU {money(arpu)}  |  COGS {money(wcogs)}  |  "
          f"gross {money(arpu - wcogs)} ({(arpu-wcogs)/arpu:.0%})")

    print("\n[3] Forecast by paying-user count (no free tier)\n")
    hdr = f"{'paying':>9} {'MRR/mo':>12} {'COGS/mo':>12} {'gross/mo':>12} {'gross/yr':>13} {'margin':>7}"
    print(hdr); print("-" * len(hdr))
    for n in USER_COUNTS:
        f = forecast(n)
        print(f"{n:>9,} {money(f['mrr']):>12} {money(f['cogs']):>12} "
              f"{money(f['gross']):>12} {money(f['gross']*12):>13} {f['margin']:>6.0%}")

    print("\n[4] Mix sensitivity at 10,000 paying users\n")
    mixes = [
        ("base (50/30/15/5)", MIX),
        ("skew-light (70/20/8/2)", {"Lite":0.70,"Plus":0.20,"Pro":0.08,"Studio":0.02}),
        ("skew-premium (30/30/25/15)", {"Lite":0.30,"Plus":0.30,"Pro":0.25,"Studio":0.15}),
    ]
    hdr = f"{'mix':28} {'ARPU':>6} {'gross/mo':>11} {'gross/yr':>13} {'margin':>7}"
    print(hdr); print("-" * len(hdr))
    for label, m in mixes:
        a, c = blended(m); f = forecast(10_000, m)
        print(f"{label:28} {money(a):>6} {money(f['gross']):>11} "
              f"{money(f['gross']*12):>13} {f['margin']:>6.0%}")

    print("\n[5] Free-tier drag at 10,000 paying (free users per paying user)\n")
    hdr = f"{'free:paying':12} {'gross/mo':>11} {'gross/yr':>13} {'margin':>7}"
    print(hdr); print("-" * len(hdr))
    for ratio, label in [(0.0, "0 (paid-only)"), (1.0, "1:1"), (2.0, "2:1"), (3.0, "3:1")]:
        f = forecast(10_000, MIX, free_per_paying=ratio)
        print(f"{label:12} {money(f['gross']):>11} {money(f['gross']*12):>13} {f['margin']:>6.0%}")
    print(f"  (each free user ~${FREE_COST:.2f}/mo cost: storage floor + a little"
          f" sleep compute. No revenue. This is the dormant-storage exposure.)")

    print("\n" + "=" * 80)
    print("BYOK => revenue is pure subscription, cost is bounded (no token tail).")
    print("~30% blended margin at the reduced prices; mix + free-tier drag are the")
    print("two dials. Dormant/free users are the main risk -> archive (GCS Coldline).")
    print("=" * 80)


if __name__ == "__main__":
    main()
