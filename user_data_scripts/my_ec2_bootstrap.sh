# In deployment_config.py -> ec2_deployments -> instances -> MyStandaloneWebServer1 -> config -> user_data
"user_data_code": #!/bin/bash
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
    dnf update -y && dnf install -y httpd ruby wget && break
    echo "dnf command failed. Retrying in 20 seconds..."
    sleep 20
done

# --- Web Server Setup ---
echo "Starting and enabling httpd..."
systemctl start httpd
systemctl enable httpd

# --- Create Health Check and Index Files ---
echo "Creating web content..."
echo "OK" > /var/www/html/healthz
echo "<html><body><h1>Initial Manual Setup Complete!</h1></body></html>" > /var/www/html/index.html

# --- Configure Port and Restart ---
echo "Configuring httpd to listen on port 8080..."
sed -i 's/Listen 80/Listen 8080/' /etc/httpd/conf/httpd.conf 
systemctl restart httpd

# --- CodeDeploy Agent Installation ---
echo "Downloading CodeDeploy agent installer..."
CODEDEPLOY_AGENT_INSTALLER_URL="https://aws-codedeploy-us-east-1.s3.us-east-1.amazonaws.com/latest/install"
wget "$CODEDEPLOY_AGENT_INSTALLER_URL" -O /tmp/install

echo "Making installer executable and running..."
chmod +x /tmp/install
/tmp/install auto

# --- Verify CodeDeploy Agent ---
echo "Checking CodeDeploy agent status..."
# Give the agent a moment to start before checking its status
sleep 10
systemctl status codedeploy-agent || echo "CodeDeploy agent status check failed, but continuing..."

echo "--- Finished UserData script execution. $(date) ---"
