# Hosted Clayrune (Cloud) — document index

The hosted-compute product: run a user's full Clayrune stack in the cloud so they
need **no PC** — their AI agent is always on, reachable from a phone. This file is
the map; read in this order.

**Stage:** design + spec complete and self-consistent; **no compute-plane code
built yet** (deliberate — committee gate). Everything below is on
`local/opus-effort`.

---

## Read in this order

| # | Doc | What it covers | Status |
|---|---|---|---|
| 1 | [`HOSTED_CLOUD_PLATFORM_DESIGN.md`](HOSTED_CLOUD_PLATFORM_DESIGN.md) | The platform: topology, microVM lifecycle (sleep/wake), BYOK custody, persistence, networking, **revenue model (§8)**, codebase impact, risks, phases | **DRAFT v1.1**, committee-ratified |
| 2 | [`HOSTED_CLOUD_COMMITTEE_BRIEF.md`](HOSTED_CLOUD_COMMITTEE_BRIEF.md) + [`_committee/HOSTED_CLOUD_seat{1..4}_*.md`](_committee/) | 4-seat adversarial review (lifecycle / custody / cost / platform) | RATIFY-WITH-CONDITIONS, 0 blockers |
| 3 | [`HOSTED_CLOUD_POC_RUNBOOK.md`](HOSTED_CLOUD_POC_RUNBOOK.md) | Small-scale POC: one host, many containers, bucket-backed storage; how to stand it up | sketch |
| 4 | [`HOSTED_CLOUD_KEY_ONBOARDING.md`](HOSTED_CLOUD_KEY_ONBOARDING.md) | Assisted BYOK key-creation flow (the non-technical onboarding crux) | spec v1 |
| 5 | [`HOSTED_CLOUD_INVESTOR_BRIEF.md`](HOSTED_CLOUD_INVESTOR_BRIEF.md) | Business story, slide-mapped — for the investor deck | draft |
| — | [`HOSTED_CLOUD_INCOME_MODEL.md`](HOSTED_CLOUD_INCOME_MODEL.md) | **Explored & REJECTED** managed-token model — kept for the cost-lever findings | rejected |

**Runnable models** (`docs/poc/`): `bucket_pricing.py` (tier prices from GCP
rates), `revenue_forecast.py` (ARR by user count + mix), `income_model.py` +
`pricing_windows.py` (rejected managed-token analysis).

---

## Locked decisions (the spine)

- **BYOK, any provider** — user brings their own key (Claude / Gemini / OpenAI);
  they pay the provider; we never touch tokens. Our cost is bounded.
- **Sleep/wake per-user microVM** (cloud) / **container-per-user** (POC) — compute
  scales toward zero when idle.
- **Pricing = transparent bucketed workspace fee** on storage / network / compute
  — named tiers **$10 / $19 / $39 / $79** (~26–40% margin, GCP-rate-verified). No
  tokens, no metering, no bill-shock.
- **Onboarding:** Gemini-free as the 2-minute on-ramp; Claude for top quality. No
  OAuth shortcut exists; API-key BYOK only (Anthropic bans subscription OAuth in
  third-party services).
- **No open free tier** until dormant-storage archiving ships (invite/paid-only).

## Next build steps (when greenlit)

Phase 0 spike (Gemini-first, one host) → Phase 1 control plane (onboarding +
vault + injection + ingress) → Phase 2 sleep/wake → Phase 3 productize (billing,
caps, backups) → Phase 4 scale. Full detail: design doc §11.
