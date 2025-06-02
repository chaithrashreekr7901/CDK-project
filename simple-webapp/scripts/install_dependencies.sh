#!/bin/bash
echo "Installing application dependencies and setting up environment..."
# Ensure Apache is installed (if not already by user data)
yum update -y
yum install -y httpd

# Ensure /var/www/html exists and has correct permissions
mkdir -p /var/www/html
chown -R apache:apache /var/www/html/
chmod -R 755 /var/www/html/

# Configure httpd to listen on 8080 (if not already by user data)
# This check prevents re-adding if already present
grep -q "Listen 8080" /etc/httpd/conf/httpd.conf || sed -i 's/Listen 80/Listen 8080/' /etc/httpd/conf/httpd.conf
systemctl enable httpd # Ensure it starts on boot
echo "Dependencies installed and permissions set."