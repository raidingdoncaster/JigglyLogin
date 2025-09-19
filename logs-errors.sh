#!/bin/bash
# Tail only ERROR logs for jigglylogin

watch -n 5 gcloud logging read \
  'resource.type="cloud_run_revision" AND resource.labels.service_name="jigglylogin" AND severity="ERROR"' \
  --project pogo-passport \
  --limit 20 \
  --format "value(timestamp, textPayload)" \
  --order=desc