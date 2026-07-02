# Weekly GCP cost review

A weekly Clayrune schedule that surveys the GCP deployment across **all
projects** on the billing account and emails a dollar-ranked list of
cost-reduction suggestions. Read-only and advisory — it never mutates a cloud
resource. Sibling of `tools/night-review/` and reuses its Gmail mailer.

## How it works

1. The Clayrune scheduler fires **weekly, Monday 06:00 local** (schedule id
   `701a537f`, cron `0 6 * * 1`, project `mission_control`).
2. The dispatched agent runs `PROMPT.md`:
   - **Tier 0** — light usage gate: skip only if 5h utilization > 70% (weekly +
     read-only, so unlike the nightly reviewer's 10% gate it almost always runs).
   - **Tier 1** — enumerate all projects (`gcloud projects list`).
   - **Tier 2** — resource inventory per project via **Cloud Asset Inventory**
     (`gcloud asset search-all-resources`, one call — works regardless of which
     per-service APIs are enabled).
   - **Tier 3** — **Recommender API** cost recommendations (idle VM/disk/IP,
     rightsizing, oversized/idle Cloud SQL, committed-use discounts) —
     dollar-quantified, swept across the regions each project actually uses.
   - **Tier 4** — heuristic waste flags from the inventory (unattached disks,
     reserved-unused IPs, stopped-but-billed VMs, always-warm Cloud Run, stale
     snapshots).
   - **Tier 5** — actual spend from the **BigQuery billing export** (if
     populated — see setup below).
3. Writes `docs/gcp-cost-review-<date>.md` (gitignored working record) and emails
   a self-contained, one-line-per-item summary via
   `tools/night-review/send_mail.py`.

## One-time setup

### Done (2026-07-01)
- **Recommender + Cloud Asset APIs enabled** on all 8 projects via
  `enable-apis.sh`. Re-run that script when a new project joins the billing
  account (the reviewer flags projects still missing these APIs).
- **BigQuery dataset `fl3-v2-prod.billing_export` created** (holds the
  billing export for the whole billing account).

### Remaining — manual Console step (for Tier 5 spend data)
The billing export → BigQuery toggle has **no gcloud/CLI equivalent**; it must
be enabled once in the Console. Until then, Tiers 1-4 run fully and the email
notes that spend data is pending.

1. Open **Billing → Billing export → BigQuery export**:
   <https://console.cloud.google.com/billing/01FD63-AE489D-5B0724/export/bigquery>
2. Under **Standard usage cost** (and optionally **Detailed usage cost**) click
   **Edit settings** → Project `fl3-v2-prod`, Dataset `billing_export` → **Save**.
3. Data begins populating within **~24h**. The weekly reviewer auto-detects the
   export table and adds the spend section once it appears.

### Gmail credentials
The email step reuses `tools/night-review/send_mail.py` — same
`~/.clayrune/night-mail.json` (or `NIGHT_MAIL_*` env vars). See
`tools/night-review/README.md`. Until set, runs still work; they just note
"email not sent".

## Files

- `PROMPT.md` — canonical copy of the agent prompt the schedule runs. Edit here
  **and** update the live schedule (`PUT /api/schedules/701a537f` or the
  Scheduler UI) to keep them in sync.
- `enable-apis.sh` — idempotent one-time (re-runnable) API enabler.

## Managing the schedule

- List: `curl -s http://localhost:5199/api/schedules`
- Change time / disable / re-enable: the Scheduler UI, or
  `PUT /api/schedules/701a537f`.
- Reports (`docs/gcp-cost-review-*.md`) and the skips log
  (`docs/gcp-cost-review-skips.log`) are gitignored runtime output.
