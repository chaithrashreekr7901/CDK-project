#!/bin/bash
echo "Validating web service..."
# Check if the web server is running and responds on port 8080
# Use curl with a short timeout to check local endpoint
if curl -sS -m 5 "http://localhost:8080/index.html" > /dev/null; then
    echo "Web service is healthy."
    exit 0
else
    echo "Web service is NOT healthy."
    exit 1
fi