#!/bin/bash
# scripts/install_dependencies.sh
# This script installs necessary dependencies (e.g., httpd, curl)

echo "--- CodeDeploy: Running BeforeInstall hook (install_dependencies.sh) ---"
echo "Updating yum packages..."
sudo yum update -y
if [ $? -ne 0 ]; then
  echo "Error: yum update failed." >&2
  exit 1
fi

echo "Installing httpd and curl..."
sudo yum install -y httpd curl # Added curl here
if [ $? -ne 0 ]; then
  echo "Error: httpd or curl installation failed." >&2
  exit 1
fi

echo "--- CodeDeploy: Finished BeforeInstall hook ---"
exit 0