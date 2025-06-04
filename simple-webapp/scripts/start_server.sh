#!/bin/bash
# scripts/start_server.sh
# This script starts and enables the HTTPD service.

echo "--- CodeDeploy: Running ApplicationStart hook (start_server.sh) ---"
echo "Starting and enabling httpd service..."

sudo systemctl start httpd
if [ $? -ne 0 ]; then
  echo "Error: Failed to start httpd service." >&2
  exit 1
fi

sudo systemctl enable httpd
if [ $? -ne 0 ]; then
  echo "Error: Failed to enable httpd service." >&2
  # This might not be critical enough to fail the deployment, but log it.
fi

echo "--- CodeDeploy: Finished ApplicationStart hook ---"
exit 0