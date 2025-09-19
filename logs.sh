#!/bin/bash
# Stream only error logs from Cloud Run service jigglylogin

gcloud beta logging tail \
  'resource.type="cloud_run_revision" AND resource.labels.service_name="jigglylogin" AND severity>=ERROR' \
  --project pogo-passport \
  --format="value(timestamp, severity, textPayload)"