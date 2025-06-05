#!/bin/bash
# scripts/start_server.sh
# This script starts and enables the HTTPD service.

echo "--- CodeDeploy: Running ApplicationStart hook (start_server.sh) ---"
echo "Starting and enabling httpd service..."

# Configure httpd to listen on 8080 (if not already done by UserData)
# This is idempotent, so it's safe to run multiple times.
if ! grep -q "Listen 8080" /etc/httpd/conf/httpd.conf; then
  sudo sed -i 's/Listen 80/Listen 8080/' /etc/httpd/conf/httpd.conf
  echo "Configured httpd to listen on port 8080."
fi

# Ensure Apache serves from /var/www/html on port 8080
# This is a common VirtualHost setup for Apache on a non-standard port
if [ ! -f /etc/httpd/conf.d/port8080.conf ]; then
  echo "<VirtualHost *:8080>
    DocumentRoot /var/www/html
    <Directory /var/www/html>
        AllowOverride None
        Require all granted
    </Directory>
  </VirtualHost>" | sudo tee /etc/httpd/conf.d/port8080.conf
  echo "Created Apache config for port 8080."
fi


sudo systemctl start httpd
if [ $? -ne 0 ]; then
  echo "Error: Failed to start httpd service." >&2
  exit 1
fi

sudo systemctl enable httpd
if [ $? -ne 0 ]; then
  echo "Error: Failed to enable httpd service." >&2
  # This might not be critical enough to fail the deployment, but log it.
fi

echo "--- CodeDeploy: Finished ApplicationStart hook ---"
exit 0