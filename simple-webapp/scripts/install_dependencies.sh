#!/bin/bash
# scripts/install_dependencies.sh
# This script installs necessary dependencies (e.g., httpd, curl)

echo "--- CodeDeploy: Running BeforeInstall hook (install_dependencies.sh) ---"
echo "Updating yum/dnf packages..."
# Use 'dnf' for Amazon Linux 2023, it's the default package manager.
# Add --allowerasing to handle package conflicts, specifically for curl.
sudo dnf update -y
if [ $? -ne 0 ]; then
  echo "Error: dnf update failed." >&2
  exit 1
fi

echo "Installing httpd and curl..."
# Use dnf and --allowerasing for curl conflict
sudo dnf install -y httpd curl --allowerasing
if [ $? -ne 0 ]; then
  echo "Error: httpd or curl installation failed." >&2
  exit 1
fi

echo "--- CodeDeploy: Finished BeforeInstall hook ---"
exit 0