#!/usr/bin/env bash
# One-time setup for the weekly GCP cost-review agent.
# Enables the Recommender API (dollar-quantified cost recs) + Cloud Asset API
# (single-call resource inventory) on every accessible GCP project.
#
# Idempotent — safe to re-run when a NEW project is added to the billing account
# (the weekly reviewer flags projects that still lack these APIs).
#
# Ran first on 2026-07-01 against billing account 01FD63-AE489D-5B0724.
set -u
# NOTE: `tr -d '\r'` is MANDATORY on Windows Git Bash — gcloud output is CRLF,
# and `--project=<id>\r` fails with "not a valid project ID".
gcloud projects list --format="value(projectId)" 2>/dev/null | tr -d '\r' | while read -r p; do
  [ -z "$p" ] && continue
  echo "=== $p ==="
  gcloud services enable recommender.googleapis.com cloudasset.googleapis.com \
    --project="$p" 2>&1 | tr -d '\r'
done
echo "ALL DONE"
