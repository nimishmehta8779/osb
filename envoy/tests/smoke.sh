#!/bin/bash
# Run on the Envoy instance (via SSM Session Manager) or from inside the VPC.
# Checks that Envoy is ready and that the listener answers.
set -euo pipefail

ready=$(curl -fsS http://127.0.0.1:9901/ready)
[ "$ready" = "LIVE" ] || { echo "FAIL: admin /ready returned '$ready'"; exit 1; }

body=$(curl -fsS http://127.0.0.1:10000/)
[ "$body" = "osb edge ok" ] || { echo "FAIL: listener body '$body'"; exit 1; }

echo "PASS: Envoy is LIVE and listener returns expected body"
