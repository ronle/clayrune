#!/usr/bin/env python3
"""
Hosted Clayrune — WORKSPACE pricing, costed from GCP list rates.

Model (settled 2026-06-02, design §8): provider-agnostic BYOK (no tokens, no
metering) + a flat WORKSPACE fee. Pricing is PRICE-ANCHORED named tiers (Ron,
2026-06-02: base ≤ $10, next $19, …) at deliberately REDUCED margin — each tier
bundles a storage / egress / compute allocation, and we check the margin our GCP
cost leaves at that price.

⚠️ GCP rates are approximate list prices (~us-central1, early-2026 knowledge).
VERIFY current rates + region before trusting absolute dollars; egress varies by
destination/volume. Structure + the "egress is the margin-sensitive line" finding
are robust.

Run:  python docs/poc/bucket_pricing.py
"""

# ── GCP list rates (USD) ─────────────────────────────────────────────────────
PD_BALANCED_GB_MO = 0.10     # live volume — Persistent Disk (Balanced)
GCS_BACKUP_GB_MO  = 0.004    # backup copy — GCS Coldline
EGRESS_GB         = 0.12     # internet egress (Premium tier, ~first TB)
EGRESS_FREE_GB    = 1
HRS_MO            = 730
VM_HR = {
    "e2-small (2GB)":      0.01675,
    "e2-medium (4GB)":     0.03350,
    "e2-standard-2 (8GB)": 0.06701,
}
SLEEP_ACTIVE_HRS = 60        # scale-to-zero: ~2h/day awake
PLATFORM_COST    = 2.0       # our amortized control-plane cost / workspace

# ── Price-anchored named tiers (Ron's targets) ──────────────────────────────
# (name, price$, storage_gb, egress_gb/mo, compute_label)
TIERS = [
    ("Lite",   10,  10,  20, "Sleep"),
    ("Plus",   19,  50,  40, "Sleep"),
    ("Pro",    39, 100,  75, "Sleep"),
    ("Studio", 79, 250, 150, "Warm-S"),
]
COMPUTE = {"Sleep": ("e2-medium (4GB)", SLEEP_ACTIVE_HRS),
           "Warm-S": ("e2-small (2GB)", HRS_MO),
           "Warm-L": ("e2-standard-2 (8GB)", HRS_MO)}


def storage_cost(gb):  # live PD + backup
    return gb * (PD_BALANCED_GB_MO + GCS_BACKUP_GB_MO)


def egress_cost(gb):
    return max(0, gb - EGRESS_FREE_GB) * EGRESS_GB


def compute_cost(label):
    vm, hrs = COMPUTE[label]
    return VM_HR[vm] * hrs


def main():
    print("=" * 80)
    print("HOSTED CLAYRUNE — WORKSPACE TIERS (price-anchored, reduced margin)")
    print("=" * 80)

    print("\n[1] Our GCP cost per resource bucket (reference)\n")
    print(f"  storage  10GB ${storage_cost(10):.2f} | 50GB ${storage_cost(50):.2f} "
          f"| 100GB ${storage_cost(100):.2f} | 250GB ${storage_cost(250):.2f}")
    print(f"  egress   20GB ${egress_cost(20):.2f} | 40GB ${egress_cost(40):.2f} "
          f"| 75GB ${egress_cost(75):.2f} | 150GB ${egress_cost(150):.2f}")
    print(f"  compute  Sleep ${compute_cost('Sleep'):.2f} | "
          f"Warm-S ${compute_cost('Warm-S'):.2f} | Warm-L ${compute_cost('Warm-L'):.2f}")
    print(f"  + platform/control-plane ${PLATFORM_COST:.2f} per workspace")

    print("\n[2] NAMED TIERS — our cost + margin at your price points\n")
    hdr = (f"{'tier':7} {'price':>6} {'storage':>8} {'egress':>8} {'compute':>8} "
           f"{'our cost':>9} {'margin':>7} {'gross$':>7}")
    print(hdr); print("-" * len(hdr))
    for name, price, s_gb, e_gb, comp in TIERS:
        cost = storage_cost(s_gb) + egress_cost(e_gb) + compute_cost(comp) + PLATFORM_COST
        margin = (price - cost) / price
        flag = "  <-- thin/negative" if margin < 0.20 else ""
        print(f"{name:7} {'$%d'%price:>6} {str(s_gb)+'GB':>8} {str(e_gb)+'GB':>8} "
              f"{comp:>8} {'$%.2f'%cost:>9} {margin:>6.0%} {price-cost:>6.1f}{flag}")

    print("\n  Steps:", " -> ".join(f"${p}" for _, p, *_ in TIERS),
          f"  (jumps {' / '.join('x%.1f'%(TIERS[i+1][1]/TIERS[i][1]) for i in range(len(TIERS)-1))})")

    print("\n[3] Notes")
    print("  - EGRESS is the margin-sensitive line: 100GB ~ $12 of cost, so low-")
    print("    priced tiers MUST keep egress modest. Heavy-traffic users move up")
    print("    (or a small egress add-on). At reduced margin this matters MORE.")
    print("  - Storage & compute are cheap for us; that's where the headroom is.")
    print("  - Warm compute ($12-49 cost) only fits the top tier; keep lower tiers")
    print("    on Sleep (scale-to-zero) or they go underwater.")
    print("  - Reduced margin (~25-40%) = price-competitive but thinner buffer, so")
    print("    egress caps + dormant-storage archiving (GCS Coldline) matter more.")

    print("\n" + "=" * 80)
    print("Edit TIERS (price + included buckets) and re-run. BYOK => no token cost;")
    print("the only real tail is egress, handled by the per-tier egress allocation.")
    print("=" * 80)


if __name__ == "__main__":
    main()
