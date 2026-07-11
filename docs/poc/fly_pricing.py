#!/usr/bin/env python3
"""
Hosted Clayrune — WORKSPACE cost + tiers.  v2, post-committee (2026-07-11).

Stack: Fly Machines (compute) + **Tigris** (object storage).  Supersedes the
GCP-rate model in bucket_pricing.py AND the v1 Fly+R2 model this file replaced.
Design write-up: docs/HOSTED_CLOUD_INFRA_DECISION.md (v2).

WHAT THE COMMITTEE BROKE IN v1 (all four seats, 2026-07-10) and what changed here:

  [C:S1.7]  MC DOES NOT FIT IN 2GB.  Measured live: claude CLI ~605MB RSS,
            Flask MC ~168MB, MCP fleet 7x node ~70MB = 1.0-1.2GB BEFORE the
            agent runs npm/tsc/pytest.  So Fly's <=2GB suspend ceiling puts
            sub-second wake out of reach for EVERY tier, and a 2GB machine
            would OOM-kill ordinary work.  => 4GB floor, STOP-only wake.
            (Seat 2 [C:S2.13] concurs and calls stop-only SAFER: no API key
            frozen into a disk-resident RAM snapshot.)

  [C:S3.8]  THE BACKUP WRITE PATH IS FLY EGRESS.  v1 costed the hydrate
            (R2 -> Fly, free) and missed the direction the product actually
            runs daily: the tarball Fly -> R2 is Fly OUTBOUND at $0.02/GB.
            Weekly full backup takes Studio 36% -> 11%.  => TIGRIS, whose
            traffic to/from Fly is free in BOTH directions.

  [C:S4.11] THERE WAS NO BACKUP FOR A HOT USER.  v1 dropped Fly snapshots on
            cost and only archived DORMANT users -> an active paying customer's
            only copy sat on one unreplicated, host-pinned Fly volume ("then
            the data is lost" -- Fly's own docs).  Free Tigris writes make a
            hot-user cadence affordable; it is now modelled.

  [C:S3.9]  PLATFORM_COST=$2.00 wrapped a PER-SEAT cost: Cloudflare Access is
            $7/user/mo.  At $7 the $10 Lite tier is underwater and the "it
            amortizes down with scale" claim is INVERTED.  => hosted edge auth
            is token-in-app at our own ingress (design 10.9's lever), so the
            control plane is FIXED infra amortized over N users.  Stripe is
            now modelled too [C:S3.13] -- 2.9%+$0.30 is 5.9% of a $10 Lite.

  [C:S3.11] FLY VOLUMES CANNOT SHRINK and bill on PROVISIONED capacity.  So
            "thin-provision + auto-extend" is a real margin dial -- but it is a
            RATCHET: a user who peaks at their cap pays for the cap forever
            (until compaction, which is byte-for-byte the archive-and-destroy
            primitive).  We therefore headline the WORST case (user fills the
            bundle) and show typical as upside.  Do not sell on the typical row.

  [C:S3.10] SLEEP_HRS=60 was an unenforced HOPE.  Clayrune has schedulers,
            hiveminds and /loop, and the idle guard KEEPS THE VM AWAKE while an
            agent runs.  => awake-hours becomes a BUNDLED, ENFORCED tier line
            (the awake-budget primitive of design 10.4), so it is true by
            construction rather than by optimism.

  [C:S4.10] Fly's own blueprint says the autostop loop "can't keep up" with
            thousands of machines -- most idle machines stay running.  So sleep
            must be ORCHESTRATOR-DRIVEN (we call `machine stop`), not left to
            the proxy.  Doesn't change the arithmetic; it changes who fires it.

RATES VERIFIED 2026-07-10 (sources in docs/HOSTED_CLOUD_INFRA_DECISION.md):
  FLY (ams)   shared vCPU $1.94/mo (256MB RAM incl. per vCPU) | extra RAM
              $5.00/GB/mo | volume $0.15/GB/mo PROVISIONED (billed even when
              detached) | stopped/suspended = storage only, rootfs $0.15/GB/mo
              | egress NA/EU $0.02/GB, inbound free | Fly<->Tigris FREE
  TIGRIS      standard $0.02/GB/mo | egress $0 (regional, inter-region AND
              internet) | Class A $5.00/M, Class B $0.50/M | S3-compatible
  STRIPE      2.9% + $0.30 per charge

Run:  python docs/poc/fly_pricing.py
"""

# ── Fly rates ────────────────────────────────────────────────────────────────
VCPU_MO        = 1.94    # shared vCPU, incl. 256MB RAM
RAM_GB_MO      = 5.00    # RAM beyond the included 256MB/vCPU
VOLUME_GB_MO   = 0.15    # PROVISIONED, attached or not, cannot shrink
ROOTFS_GB_MO   = 0.15    # stopped machine rootfs
EGRESS_GB      = 0.02    # NA/EU internet egress (inbound free)
HRS_MO         = 730

# ── Tigris rates ─────────────────────────────────────────────────────────────
TIGRIS_GB_MO   = 0.02
TIGRIS_XFER    = 0.0     # to/from a Fly Machine: FREE, both directions

# ── Stripe ───────────────────────────────────────────────────────────────────
STRIPE_PCT     = 0.029
STRIPE_FIXED   = 0.30

# ── Fixed control plane (NOT per-seat — that was the [C:S3.9] error) ─────────
# orchestrator machine + ingress proxy + Postgres + monitoring + domains.
# Token-in-app auth at OUR ingress replaces Cloudflare Access ($7/seat).
PLATFORM_FIXED_MO = 70.0
PLATFORM_USERS    = 500     # amortization denominator; see sensitivity below

ROOTFS_GB      = 3.0     # MC image (python+node+claude CLI). [C:S3.14] MEASURE.
BACKUPS_KEPT   = 1.0     # full copies of the workspace held in Tigris

# Machines: (shared vCPUs, total RAM GB).  4GB FLOOR — [C:S1.7].
MACHINES = {
    "4x/4GB": (4, 4),
    "4x/8GB": (4, 8),
}

# v2 tiers.  (name, price$, storage_gb_cap, awake_hrs, egress_gb, machine)
# CHANGED vs v1: Plus 50->40GB, Studio 250->150GB.  v1's "the bundles survive
# intact" claim was an artifact of the broken model; at $0.17/GB all-in, a
# 250GB Studio bundle is $42.50 of cost on a $79 price — 54% of revenue.
TIERS = [
    ("Lite",   10,  10,  60,  20, "4x/4GB"),
    ("Plus",   19,  40, 100,  40, "4x/4GB"),
    ("Pro",    39, 100, 200,  75, "4x/4GB"),
    ("Studio", 79, 150, 400, 150, "4x/8GB"),
]

TYPICAL_FILL = 0.40      # what a real user actually stores vs. their cap


def machine_mo(key):
    vcpu, ram_gb = MACHINES[key]
    return vcpu * VCPU_MO + max(0.0, ram_gb - vcpu * 0.25) * RAM_GB_MO


def compute_cost(key, awake_hrs):
    """Stop-only: awake hours + the stopped-rootfs floor."""
    return machine_mo(key) / HRS_MO * awake_hrs + ROOTFS_GB * ROOTFS_GB_MO


def storage_cost(gb):
    """Live Fly volume (provisioned) + Tigris copies. Transfer is free."""
    return gb * VOLUME_GB_MO + gb * BACKUPS_KEPT * TIGRIS_GB_MO


def stripe_fee(price):
    return price * STRIPE_PCT + STRIPE_FIXED


def platform_cost():
    return PLATFORM_FIXED_MO / PLATFORM_USERS


def tier_cost(price, gb, awake, egress_gb, mach):
    return (storage_cost(gb) + compute_cost(mach, awake)
            + egress_gb * EGRESS_GB + platform_cost() + stripe_fee(price))


def main():
    print("=" * 92)
    print("HOSTED CLAYRUNE — TIERS v2 (Fly + Tigris), post-committee 2026-07-11")
    print("=" * 92)

    print("\n[1] Machines — 4GB FLOOR, STOP-only wake  [C:S1.7]\n")
    for k in MACHINES:
        m = machine_mo(k)
        print(f"  shared-cpu-{k:7} ${m:6.2f}/mo always-on | ${m/HRS_MO:.4f}/hr awake")
    print(f"  stopped rootfs ({ROOTFS_GB:.0f}GB) ${ROOTFS_GB*ROOTFS_GB_MO:.2f}/mo — the idle floor")
    print("  NO tier suspends. Fly's suspend needs <=2GB; MC needs 4GB. Wake = cold")
    print("  boot (~2s VM + 3-15s MC-ready). Sub-second wake is OFF THE TABLE.")

    print("\n[2] Storage — $0.17/GB all-in (Fly volume $0.15 + Tigris copy $0.02)\n")
    print("  Tigris transfer is FREE both ways, so the backup WRITE costs nothing")
    print("  [C:S3.8] and a HOT-user backup cadence is affordable [C:S4.11].")
    print("  On R2 the same write would be Fly egress at $0.02/GB.")

    print("\n[3] TIERS — margin at FULL bundle (headline) and at typical fill\n")
    hdr = (f"{'tier':7} {'price':>6} {'store':>6} {'awake':>6} {'egr':>5} {'machine':>8} "
           f"{'stor$':>6} {'cpu$':>6} {'egr$':>5} {'plat$':>6} {'strp$':>6} "
           f"{'COST':>7} {'margin':>7} | {'typical':>8}")
    print(hdr)
    print("-" * len(hdr))
    for name, price, gb, awake, egr, mach in TIERS:
        s, c = storage_cost(gb), compute_cost(mach, awake)
        e, p, st = egr * EGRESS_GB, platform_cost(), stripe_fee(price)
        cost = s + c + e + p + st
        margin = (price - cost) / price
        typ = tier_cost(price, gb * TYPICAL_FILL, awake, egr, mach)
        typ_m = (price - typ) / price
        flag = "  <-- UNDERWATER" if margin < 0 else ("  <-- thin" if margin < 0.20 else "")
        print(f"{name:7} {'$%d'%price:>6} {str(gb)+'GB':>6} {str(awake)+'h':>6} "
              f"{str(egr)+'GB':>5} {mach:>8} {'$%.2f'%s:>6} {'$%.2f'%c:>6} {'$%.2f'%e:>5} "
              f"{'$%.2f'%p:>6} {'$%.2f'%st:>6} {'$%.2f'%cost:>7} {margin:>6.0%} | "
              f"{typ_m:>7.0%}{flag}")
    print(f"\n  'margin'  = user FILLS the bundle. Fly volumes CANNOT SHRINK, so this")
    print(f"              is a ratchet, not a tail risk. SELL ON THIS ROW.  [C:S3.11]")
    print(f"  'typical' = user stores {TYPICAL_FILL:.0%} of cap. Upside, not a plan.")

    print("\n[4] What changed vs v1's bundles\n")
    print("  Plus   50GB -> 40GB     Studio 250GB -> 150GB")
    print("  v1 claimed 'the original bundles survive intact'. That was an artifact")
    print("  of the broken model. At $0.17/GB all-in, a 250GB Studio bundle is")
    print("  $42.50 of cost against a $79 price — 54% of revenue before compute.")
    print("  Awake-hours are now a BUNDLED, ENFORCED line (60/100/200/400h) [C:S3.10],")
    print("  which also gives the tiers a second axis to differentiate on.")

    print("\n[5] Platform cost — the [C:S3.9] fix\n")
    print(f"  Cloudflare Access is ${7.00:.2f}/USER/mo. At that rate Lite is UNDERWATER")
    print("  and 'amortizes down with scale' is INVERTED. Hosted edge auth must be")
    print("  token-in-app at OUR ingress (design 10.9's lever), making the control")
    print(f"  plane FIXED: ~${PLATFORM_FIXED_MO:.0f}/mo of infra. Sensitivity:")
    for n in (100, 500, 1000, 5000):
        print(f"    {n:>5} users -> ${PLATFORM_FIXED_MO/n:>5.2f}/user/mo")
    print(f"  Modelled at N={PLATFORM_USERS}. Stripe is NOT amortizable: "
          f"${stripe_fee(10):.2f} on a $10 Lite = {stripe_fee(10)/10:.0%}.")

    print("\n[6] Dormant exposure — archive-and-DESTROY (volume, not detach)\n")
    print(f"  {'GB':>5} {'volume kept':>13} {'archived':>10} {'x1,000 dead signups':>22}")
    for gb in (5, 10):
        keep = gb * VOLUME_GB_MO + ROOTFS_GB * ROOTFS_GB_MO
        arch = gb * TIGRIS_GB_MO
        print(f"  {gb:>5} {'$%.2f/mo'%keep:>13} {'$%.2f/mo'%arch:>10} "
              f"{'$%d -> $%d'%(keep*1000, arch*1000):>22}")
    print("\n  CAVEAT [C:S1.10]: a user with ONE daily schedule is NEVER dormant —")
    print("  the mirror wakes them. 'Dormant' must be defined against schedules,")
    print("  and the free-tier economics re-derived on that basis. NOT closed here.")

    print("\n[7] Still open (do not price on these)\n")
    print("  - ROOTFS_GB=3.0 is a guess for an image that does not exist [C:S3.14]")
    print("  - PLATFORM_FIXED_MO=$70 is estimated, not quoted [C:S3.9]")
    print("  - hydrate wall-time sets the dormancy threshold — unmeasured [C:S1.11]")
    print("  - awake-budget enforcement is UNBUILT; until it ships, SLEEP_HRS is")
    print("    still a hope [C:S3.10]")
    print("  - Fly reservations (~40% off) are a 1-yr commit on FIXED capacity and")
    print("    partially DEFEAT scale-to-zero. Not 'pure upside'. [C:S3.16]")

    print("\n" + "=" * 92)


if __name__ == "__main__":
    main()
