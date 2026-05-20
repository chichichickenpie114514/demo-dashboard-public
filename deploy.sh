#!/bin/bash
gcloud run deploy sales-dashboard \
  --region=asia-northeast1 \
  --source=. \
  --allow-unauthenticated
