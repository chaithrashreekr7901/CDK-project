#!/bin/bash
set -e

echo "Running before_install.sh..."

# Clean previous app dir if needed
rm -rf /home/ec2-user/myapp/tmp || true
mkdir -p /home/ec2-user/myapp/tmp

echo "BeforeInstall step complete."
