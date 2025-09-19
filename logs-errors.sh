#!/bin/bash
# Stream logs from Cloud Run service jigglylogin in project pogo-passport

gcloud beta logging tail \
  'resource.type="cloud_run_revision" AND resource.labels.service_name="jigglylogin"' \
  --project pogo-passport \
  --format="value(timestamp, severity, textPayload)"