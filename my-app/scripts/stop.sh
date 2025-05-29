#!/bin/bash
set -e

echo "Running stop.sh..."

# Kill any process running on port 8080
fuser -k 8080/tcp || true

echo "Application stopped if it was running."
