#!/bin/bash
# Tail Cloud Run logs for jigglylogin, highlight errors

watch -n 5 gcloud logging read \
  'resource.type="cloud_run_revision" AND resource.labels.service_name="jigglylogin"' \
  --project pogo-passport \
  --limit 20 \
  --format "value(timestamp, textPayload)" \
  --order=desc | GREP_COLOR='01;31' grep --color=always -E "ERROR|$"