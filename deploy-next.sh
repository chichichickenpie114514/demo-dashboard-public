#!/bin/bash
# Demo dashboard — deploy to personal GCP Cloud Run.
# Pre-generated simulated data baked into the container image.
# No MySQL, no GCS bucket needed.
#
# Required env vars:
#   GCP_PROJECT          — GCP project ID (e.g. "my-gcp-project")
#   GCP_PROJECT_NUMBER   — GCP project number (e.g. "123456789012")
#
# Prerequisites (one-time):
#   gcloud secrets create oauth-client-id --data-file=<(printf "xxx.apps.googleusercontent.com")
#   gcloud secrets create oauth-client-secret --data-file=<(printf "GOCSPX-xxx")
#   gcloud secrets create flask-secret-key --data-file=<(python3 -c "import secrets; print(secrets.token_hex(32))")
#
set -euo pipefail

if [ -z "${GCP_PROJECT:-}" ] || [ -z "${GCP_PROJECT_NUMBER:-}" ]; then
  echo "Usage: GCP_PROJECT=<project-id> GCP_PROJECT_NUMBER=<project-number> bash deploy-next.sh" >&2
  exit 1
fi

gcloud run deploy demo-dashboard \
  --project="$GCP_PROJECT" \
  --region=asia-northeast1 \
  --source=. \
  --allow-unauthenticated \
  --quiet \
  --max-instances=3 \
  --concurrency=20 \
  --set-env-vars=GCS_BUCKET= \
  --update-secrets=GOOGLE_MAPS_API_KEY=projects/$GCP_PROJECT_NUMBER/secrets/google-map-api:latest \
  --update-secrets=DEEPSEEK_API_KEY=projects/$GCP_PROJECT_NUMBER/secrets/deepseek-api:latest \
  --update-secrets=GOOGLE_CLIENT_ID=projects/$GCP_PROJECT_NUMBER/secrets/oauth-client-id:latest \
  --update-secrets=GOOGLE_CLIENT_SECRET=projects/$GCP_PROJECT_NUMBER/secrets/oauth-client-secret:latest \
  --update-secrets=SECRET_KEY=projects/$GCP_PROJECT_NUMBER/secrets/flask-secret-key:latest
