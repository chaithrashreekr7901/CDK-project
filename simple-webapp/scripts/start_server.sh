#!/bin/bash
echo "Starting web server (httpd)..."
# Assuming httpd is already installed and configured to listen on 8080 by user data
systemctl start httpd
echo "Web server started."