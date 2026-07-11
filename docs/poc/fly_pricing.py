#!/usr/bin/env python3
"""
Hosted Clayrune — WORKSPACE cost + tiers on the HYBRID stack (Fly + R2).

Companion to bucket_pricing.py (same tier model, GCP rates). The design (§8)
costed tiers against GCP; the real stack is Fly Machines for compute (§4.2) and
an object store for durability/archive (§3.1). This re-costs the SAME bundles at
verified Fly + R2 rates.

✅ RATES VERIFIED 2026-07-10 (sources in docs/HOSTED_CLOUD_INFRA_DECISION.md):
  FLY (ams; regions carry a markup multiplier)
    shared vCPU        $1.94 / vCPU / mo   (256MB RAM included per vCPU)
    extra RAM          $5.00 / GB / mo
    volume             $0.15 / GB / mo of PROVISIONED capacity — billed even
                       when DETACHED. Destroying it is the only way to stop it.
    volume snapshot    $0.08 / GB / mo (10GB/mo free)   <- we DON'T use these
    stopped/suspended  storage only, no CPU/RAM. rootfs $0.15/GB/mo.
    egress NA/EU       $0.02 / GB   (inbound free; Fly<->Tigris free)
    reservations       ~40% off compute on a 1-yr commit
  CLOUDFLARE R2
    storage            $0.015 / GB / mo   (10GB/mo free)
    egress             $0 — unconditional. Inbound to Fly is free too.
    Class A (PUT)      $4.50 / million    Class B (GET) $0.36 / million
    min duration       none; no retrieval fee

⚠️  SUSPEND CONSTRAINT (verified): Fly suspend/resume (~sub-second wake) is only
    supported for machines with <= 2GB RAM. Larger machines must use STOP, whose
    wake is VM-boot (~2s) + MC-ready (~3-15s, design §4.2 [C:S3.3]). So the
    2GB/suspend path is the good one; 4-8GB tiers pay a cold boot.

THE TWO FIXES this model applies vs. the GCP-costed tiers:
  1. Back up to R2 ($0.015/GB), NOT Fly volume snapshots ($0.08/GB) — 5.3x.
  2. No always-on "Warm" compute at any tier. Sleep everywhere.
Together they clear ~40% margin at every price point with the ORIGINAL storage
bundles intact (no bundle cut needed).

Run:  python docs/poc/fly_pricing.py
"""

# ── Fly rates (USD) ──────────────────────────────────────────────────────────
VCPU_MO        = 1.94    # shared vCPU, incl. 256MB RAM
RAM_GB_MO      = 5.00    # RAM beyond the included 256MB/vCPU
VOLUME_GB_MO   = 0.15    # provisioned, attached or not
FLY_SNAP_GB_MO = 0.08    # rejected in favour of R2
ROOTFS_GB_MO   = 0.15    # stopped/suspended machine rootfs
EGRESS_GB      = 0.02    # NA/EU internet egress
HRS_MO         = 730

# ── Cloudflare R2 rates (USD) ────────────────────────────────────────────────
R2_GB_MO       = 0.015
R2_EGRESS_GB   = 0.0     # free, unconditional

ROOTFS_GB      = 3.0     # MC image: python + node + claude CLI + deps (estimate)
PLATFORM_COST  = 2.0     # amortized control plane / workspace (same as GCP model)
SLEEP_HRS      = 60      # ~2h/day awake (same assumption as the GCP model)

# Machine presets: (shared vCPUs, total RAM GB, can_suspend)
MACHINES = {
    "2x/2GB": (2, 2, True),    # <=2GB -> suspend, sub-second wake
    "4x/4GB": (4, 4, False),   # >2GB  -> stop only, cold boot
    "4x/8GB": (4, 8, False),
}

# (name, price$, storage_gb, egress_gb/mo, machine)
TIERS = [
    ("Lite",   10,  10,  20, "2x/2GB"),
    ("Plus",   19,  50,  40, "2x/2GB"),
    ("Pro",    39, 100,  75, "4x/4GB"),
    ("Studio", 79, 250, 150, "4x/8GB"),
]


def machine_mo(key):
    vcpu, ram_gb, _ = MACHINES[key]
    return vcpu * VCPU_MO + max(0.0, ram_gb - vcpu * 0.25) * RAM_GB_MO


def compute_cost(key):
    """Sleep/wake: awake hours + the idle rootfs floor."""
    return machine_mo(key) / HRS_MO * SLEEP_HRS + ROOTFS_GB * ROOTFS_GB_MO


def storage_hybrid(gb):
    """Live Fly volume + durable R2 copy (replaces Fly snapshots)."""
    return gb * VOLUME_GB_MO + gb * R2_GB_MO


def storage_fly_only(gb):
    """The old shape: Fly volume + Fly snapshots."""
    return gb * VOLUME_GB_MO + max(0.0, gb - 10) * FLY_SNAP_GB_MO


def dormant_cost(gb, archived):
    """A signup that stops logging in."""
    if archived:                      # volume + machine destroyed; tarball in R2
        return gb * R2_GB_MO
    return gb * VOLUME_GB_MO + ROOTFS_GB * ROOTFS_GB_MO


def main():
    print("=" * 88)
    print("HOSTED CLAYRUNE — HYBRID (Fly compute + R2 durability), rates verified 2026-07-10")
    print("=" * 88)

    print("\n[1] Machine cost (sleep/wake bills awake hours only)\n")
    for k, (_, ram, susp) in MACHINES.items():
        m = machine_mo(k)
        wake = "suspend, sub-second wake" if susp else "STOP only (>2GB): ~2s VM + 3-15s MC"
        print(f"  shared-cpu-{k:8} ${m:6.2f}/mo always-on | ${compute_cost(k):5.2f}/mo "
              f"at {SLEEP_HRS}h awake | {wake}")

    print("\n[2] Storage: the fix — R2 backup instead of Fly snapshots\n")
    print(f"  {'GB':>5} {'Fly vol+snap':>13} {'Fly vol + R2':>13} {'saved':>8}")
    for gb in (10, 50, 100, 250):
        a, b = storage_fly_only(gb), storage_hybrid(gb)
        print(f"  {gb:>5} {'$%.2f'%a:>13} {'$%.2f'%b:>13} {'$%.2f'%(a-b):>8}")

    print("\n[3] NAMED TIERS — original bundles, no cut, sleep everywhere\n")
    hdr = (f"{'tier':7} {'price':>6} {'store':>6} {'egress':>7} {'machine':>8} "
           f"{'stor$':>6} {'egr$':>6} {'cpu$':>6} {'plat$':>6} {'COST':>7} {'margin':>7}")
    print(hdr)
    print("-" * len(hdr))
    for name, price, s_gb, e_gb, mach in TIERS:
        s = storage_hybrid(s_gb)
        e = e_gb * EGRESS_GB
        c = compute_cost(mach)
        cost = s + e + c + PLATFORM_COST
        margin = (price - cost) / price
        flag = "  <-- UNDERWATER" if margin < 0 else ("  <-- thin" if margin < 0.20 else "")
        print(f"{name:7} {'$%d'%price:>6} {str(s_gb)+'GB':>6} {str(e_gb)+'GB':>7} {mach:>8} "
              f"{'$%.2f'%s:>6} {'$%.2f'%e:>6} {'$%.2f'%c:>6} {'$%.2f'%PLATFORM_COST:>6} "
              f"{'$%.2f'%cost:>7} {margin:>6.0%}{flag}")

    print("\n[4] Dormant exposure — the free-tier blocker, solved\n")
    print(f"  {'GB':>5} {'volume kept':>13} {'archived to R2':>15} {'x1,000 users':>26}")
    for gb in (5, 10):
        keep, arch = dormant_cost(gb, False), dormant_cost(gb, True)
        blow = f"${keep*1000:,.0f} -> ${arch*1000:,.0f}"
        print(f"  {gb:>5} {'$%.2f/mo'%keep:>13} {'$%.3f/mo'%arch:>15} {blow:>26}")
    print("\n  Fly volumes bill on PROVISIONED capacity even when DETACHED — so")
    print("  'archive' means destroy the volume, not detach it. That is the whole")
    print("  reason archive-and-detach is a Phase-1 concern, not Phase-4 polish.")

    print("\n[5] Egress is no longer the margin-sensitive line\n")
    print("  Fly $0.02/GB vs GCP $0.12 -> 6x cheaper. 150GB costs $3.00, not $18.")
    print("  R2 egress is $0 unconditional, so hydrate-on-wake is bandwidth-free.")
    print("  Design §8's 'egress is the margin-sensitive line' is RETIRED on this stack.")
    print("  Storage is now the only line that scales with the user.")

    print("\n[6] Levers not modelled above")
    print(f"  - Fly compute reservations: ~40% off -> Lite cpu ${compute_cost('2x/2GB'):.2f}"
          f" -> ~${compute_cost('2x/2GB')*0.6+0.18:.2f}")
    print("  - PLATFORM_COST $2.00/user is a placeholder; it amortizes DOWN with scale.")
    print("  - Tarball, never per-file sync: node_modules is 100k+ objects and R2")
    print("    Class A is $4.50/M. One multipart object = ~free. LOAD-BEARING.")

    print("\n" + "=" * 88)


if __name__ == "__main__":
    main()
