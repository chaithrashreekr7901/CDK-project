#!/bin/bash
# Exit immediately if a command exits with a non-zero status.
set -e
# Print commands and their arguments as they are executed.
set -x

# Redirect all output to a log file and to the console for easier debugging.
exec > >(tee /var/log/user-data.log|logger -t user-data -s 2>/dev/console) 2>&1

echo "--- Starting UserData script execution... $(date) ---"

# --- Resilient Package Installation ---
# This loop will try up to 3 times to install the necessary packages.
# This handles temporary network issues during boot.
for i in {1..3}; do
    echo "Attempt $i to install packages..."
    sudo dnf update -y && sudo dnf install -y httpd ruby wget && break
    echo "dnf command failed. Retrying in 20 seconds..."
    sleep 20
done

# --- Web Server Setup ---
echo "Starting and enabling httpd..."
sudo systemctl start httpd
sudo systemctl enable httpd

# --- Create Health Check and Index Files ---
echo "Creating web content..."
echo "OK" | sudo tee /var/www/html/healthz  # Use sudo tee for permissions
echo "<html><body><h1>Hello from ASG Instance: $(hostname -f)</h1></body></html>" | sudo tee /var/www/html/index.html # Use sudo tee

# --- Configure Port and Restart ---
echo "Configuring httpd to listen on port 8080..."
sudo sed -i 's/Listen 80/Listen 8080/' /etc/httpd/conf/httpd.conf

# Add a VirtualHost block to explicitly serve content on port 8080
echo "Configuring Apache VirtualHost for port 8080..."
cat <<EOF | sudo tee /etc/httpd/conf.d/application_port.conf
<VirtualHost *:8080>
    DocumentRoot /var/www/html
    <Directory /var/www/html>
        AllowOverride None
        Require all granted
    </Directory>
    ErrorLog /var/log/httpd/application_error_log
    CustomLog /var/log/httpd/application_access_log combined
</VirtualHost>
EOF

sudo systemctl restart httpd

# --- CodeDeploy Agent Installation ---
echo "Downloading CodeDeploy agent installer..."
# Ensure the region in the S3 URL matches your deployment region (us-east-1 in your logs)
CODEDEPLOY_AGENT_INSTALLER_URL="https://aws-codedeploy-us-east-1.s3.us-east-1.amazonaws.com/latest/install"
sudo wget "$CODEDEPLOY_AGENT_INSTALLER_URL" -O /tmp/install

echo "Making installer executable and running..."
sudo chmod +x /tmp/install
sudo /tmp/install auto

# --- Verify CodeDeploy Agent ---
echo "Checking CodeDeploy agent status..."
# Give the agent a moment to start before checking its status
sleep 10
sudo systemctl status codedeploy-agent || echo "CodeDeploy agent status check failed, but continuing..."

echo "--- Finished UserData script execution. $(date) ---"