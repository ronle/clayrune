#!/usr/bin/env python3
"""
Hosted Clayrune — WORKSPACE bucket pricing, costed from GCP list rates.

Model (settled 2026-06-02, design §8): provider-agnostic BYOK (no tokens, no
metering) + a flat WORKSPACE fee that is BUCKETED across the real GCP cost
drivers — storage, network/egress, compute — so the user sees exactly what they
pay for. This script sets the bucket thresholds + prices from GCP list rates:

  bucket price = (our GCP cost at the bucket ceiling) x markup, rounded.

The markup covers the value-add the raw GCP cost doesn't: backup/DR, always-on
hosting, the orchestration/control plane, assisted onboarding, support — and margin.

⚠️ GCP rates below are approximate list prices (~us-central1, early-2026
knowledge). VERIFY current rates + your region before trusting absolute dollars;
egress especially varies by destination and volume. Ratios/structure are robust.

Run:  python docs/poc/bucket_pricing.py
"""
import math

# ── GCP list rates (USD) ─────────────────────────────────────────────────────
PD_BALANCED_GB_MO = 0.10     # live volume — Persistent Disk (Balanced)
GCS_BACKUP_GB_MO  = 0.004    # backups/cold — GCS Coldline
EGRESS_GB         = 0.12     # internet egress (Premium tier, ~first TB)
EGRESS_FREE_GB    = 1        # ~1GB/mo free
HRS_MO            = 730
VM_HR = {                    # Compute Engine e2 (on-demand $/hr)
    "e2-small (2GB)":      0.01675,
    "e2-medium (4GB)":     0.03350,
    "e2-standard-2 (8GB)": 0.06701,
}
SLEEP_ACTIVE_HRS = 60        # scale-to-zero: ~2h/day actually awake

# ── Markups (value-add + margin over raw GCP cost) ───────────────────────────
MARKUP = {"storage": 3.0, "egress": 2.0, "compute": 2.0}
PLATFORM_BASE_PRICE = 5.0    # control plane + assisted onboarding + backup infra

# ── Buckets (ceilings) ───────────────────────────────────────────────────────
STORAGE = [("S", 10), ("M", 50), ("L", 200)]          # GB
EGRESS  = [("S", 20), ("M", 100), ("L", 500)]         # GB/mo
COMPUTE = [                                            # (label, vm, hrs/mo)
    ("Sleep",   "e2-medium (4GB)",     SLEEP_ACTIVE_HRS),
    ("Warm-S",  "e2-small (2GB)",      HRS_MO),
    ("Warm-L",  "e2-standard-2 (8GB)", HRS_MO),
]


def nice(x):
    if x < 10:
        return max(2, round(x))
    return int(math.ceil(x / 5.0) * 5)     # round up to nearest $5


def storage_cost(gb):   # live PD + a backup copy in GCS
    return gb * PD_BALANCED_GB_MO + gb * GCS_BACKUP_GB_MO


def egress_cost(gb):
    return max(0, gb - EGRESS_FREE_GB) * EGRESS_GB


def compute_cost(vm, hrs):
    return VM_HR[vm] * hrs


def price(cost, dim):
    return nice(cost * MARKUP[dim])


def section(title):
    print("\n" + title + "\n" + "-" * len(title))


def main():
    print("=" * 78)
    print("HOSTED CLAYRUNE — WORKSPACE BUCKET PRICING (from GCP list rates)")
    print(f"  markups: storage x{MARKUP['storage']}, egress x{MARKUP['egress']}, "
          f"compute x{MARKUP['compute']};  platform base ${PLATFORM_BASE_PRICE:.0f}")
    print("=" * 78)

    section("[1] STORAGE buckets  (live PD $%.2f/GB + backup $%.3f/GB)"
            % (PD_BALANCED_GB_MO, GCS_BACKUP_GB_MO))
    print(f"{'bucket':7} {'ceiling':>8} {'our cost':>9} {'price':>7} {'margin':>7}")
    for name, gb in STORAGE:
        c = storage_cost(gb); p = price(c, "storage")
        print(f"{name:7} {str(gb)+'GB':>8} {'$%.2f'%c:>9} {'$%d'%p:>7} {1-c/p:>6.0%}")

    section("[2] NETWORK / EGRESS buckets  (internet egress $%.2f/GB, ~1GB free)"
            % EGRESS_GB)
    print(f"{'bucket':7} {'ceiling':>9} {'our cost':>9} {'price':>7} {'margin':>7}")
    for name, gb in EGRESS:
        c = egress_cost(gb); p = price(c, "egress")
        print(f"{name:7} {str(gb)+'GB/mo':>9} {'$%.2f'%c:>9} {'$%d'%p:>7} {1-c/p:>6.0%}")

    section("[3] COMPUTE buckets  (Compute Engine e2, on-demand)")
    print(f"{'bucket':7} {'machine':>20} {'hrs/mo':>7} {'our cost':>9} {'price':>7} {'margin':>7}")
    for name, vm, hrs in COMPUTE:
        c = compute_cost(vm, hrs); p = price(c, "compute")
        print(f"{name:7} {vm:>20} {hrs:>7} {'$%.2f'%c:>9} {'$%d'%p:>7} {1-c/p:>6.0%}")

    # Example workspace bills
    def bill(s_gb, e_gb, comp):
        sc, ec = storage_cost(s_gb), egress_cost(e_gb)
        cvm = next(v for n, v, h in COMPUTE if n == comp)
        chrs = next(h for n, v, h in COMPUTE if n == comp)
        cc = compute_cost(cvm, chrs)
        sp, ep, cp = price(sc, "storage"), price(ec, "egress"), price(cc, "compute")
        total = PLATFORM_BASE_PRICE + sp + ep + cp
        our = sc + ec + cc + 2.0  # +~$2 amortized control plane
        return sp, ep, cp, total, our

    section("[4] EXAMPLE WORKSPACE BILLS  (what the user sees + our margin)")
    print(f"{'profile':24} {'storage':>8} {'egress':>7} {'compute':>8} "
          f"{'base':>5} {'TOTAL/mo':>9} {'our cost':>9} {'margin':>7}")
    examples = [
        ("Casual  (10GB/20/Sleep)",  10, 20,  "Sleep"),
        ("Regular (50GB/100/Sleep)", 50, 100, "Sleep"),
        ("Power   (200GB/100/Warm-S)", 200, 100, "Warm-S"),
        ("Studio  (200GB/500/Warm-L)", 200, 500, "Warm-L"),
    ]
    for label, s, e, comp in examples:
        sp, ep, cp, total, our = bill(s, e, comp)
        print(f"{label:24} {'$%d'%sp:>8} {'$%d'%ep:>7} {'$%d'%cp:>8} "
              f"{'$%d'%PLATFORM_BASE_PRICE:>5} {'$%d'%total:>9} "
              f"{'$%.0f'%our:>9} {1-our/total:>6.0%}")

    print("\n  The user always sees the line items, e.g. Regular =")
    print("  $5 base + $20 storage(M) + $25 egress(M) + $4 compute(Sleep) = $54.")
    print("  No tokens (BYOK), no metering — move a bucket only when you cross it,")
    print("  with a heads-up first.")

    print("\n" + "=" * 78)
    print("Knobs: GCP rates, MARKUP per dimension, bucket ceilings, PLATFORM_BASE.")
    print("Cold-tiering old data (GCS Coldline/Archive) is the lever to widen the")
    print("storage buckets cheaply for power users. Verify rates before launch.")
    print("=" * 78)


if __name__ == "__main__":
    main()
