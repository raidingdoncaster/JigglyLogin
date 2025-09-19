#!/bin/bash
# logs-debug.sh: Stream all logs (stdout + stderr) for Cloud Run service

gcloud beta logging tail \
  'resource.type="cloud_run_revision" AND resource.labels.service_name="jigglylogin"' \
  --project pogo-passport \
  --format "json"
