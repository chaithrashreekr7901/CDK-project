#!/bin/bash
# scripts/install_dependencies.sh
# This script installs necessary dependencies (e.g., httpd)

echo "--- CodeDeploy: Running BeforeInstall hook (install_dependencies.sh) ---"
echo "Updating yum packages..."
sudo yum update -y
if [ $? -ne 0 ]; then
  echo "Error: yum update failed." >&2
  exit 1
fi

echo "Installing httpd..."
sudo yum install -y httpd
if [ $? -ne 0 ]; then
  echo "Error: httpd installation failed." >&2
  exit 1
fi

echo "--- CodeDeploy: Finished BeforeInstall hook ---"
exit 0