#!/bin/bash
# scripts/stop_server.sh
# This script attempts to stop the HTTPD service if it's running.
# It's designed to be idempotent.

echo "--- CodeDeploy: Running ApplicationStop hook (stop_server.sh) ---"
echo "Attempting to stop httpd service..."

# Check if httpd is active before trying to stop it
if systemctl is-active --quiet httpd; then
  sudo systemctl stop httpd
  if [ $? -eq 0 ]; then
    echo "httpd service stopped successfully."
  else
    echo "Warning: Failed to stop httpd service. Continuing anyway." >&2
    # Exit with 0 to allow deployment to proceed even if stop fails, unless it's critical.
    exit 0 # Exit 0 to indicate success for the hook
  fi
else
  echo "httpd service is not active. Nothing to stop."
fi

echo "--- CodeDeploy: Finished ApplicationStop hook ---"
exit 0 # Ensure the script exits successfully