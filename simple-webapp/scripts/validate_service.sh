#!/bin/bash
# scripts/validate_service.sh
# This script validates the application service is running.

echo "--- CodeDeploy: Running ValidateService hook (validate_service.sh) ---"
echo "Waiting for application to become healthy on port 8080..."

HEALTH_CHECK_URL="http://localhost:8080/healthz"
MAX_RETRIES=20 # Increased retries
RETRY_INTERVAL=10 # Increased interval (total 200 seconds wait time)

for i in $(seq 1 $MAX_RETRIES); do
  response=$(curl -s -o /dev/null -w "%{http_code}" $HEALTH_CHECK_URL)
  if [ "$response" -eq 200 ]; then
    echo "Application is healthy (HTTP 200 OK)."
    echo "--- CodeDeploy: Finished ValidateService hook ---"
    exit 0
  else
    echo "Attempt $i: Health check failed with HTTP code $response. Retrying in $RETRY_INTERVAL seconds..."
    sleep $RETRY_INTERVAL
  fi
done

echo "Error: Application did not become healthy after $MAX_RETRIES attempts." >&2
echo "--- CodeDeploy: Failed ValidateService hook ---"
exit 1 # Fail the hook if service is not healthy