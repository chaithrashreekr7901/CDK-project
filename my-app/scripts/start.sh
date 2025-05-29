#!/bin/bash

# Exit immediately if a command exits with a non-zero status
set -e

echo "Running start.sh..."

APP_DIR="/home/ec2-user/myapp"

# Stop any existing server running on port 8080
fuser -k 8080/tcp || true

# Start the app
cd $APP_DIR
nohup python3 -m http.server 8080 > app.log 2>&1 &

echo "Application started on port 8080."
