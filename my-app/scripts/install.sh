#!/bin/bash

# Exit immediately if a command exits with a non-zero status
set -e

echo "Running install.sh..."

# Update and install dependencies (optional)
sudo yum update -y

# Create the app directory
APP_DIR="/home/ec2-user/myapp"
mkdir -p $APP_DIR
chown ec2-user:ec2-user $APP_DIR

echo "Installation step completed."
