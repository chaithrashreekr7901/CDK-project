#!/bin/bash
exec > >(tee /var/log/user-data.log|logger -t user-data -s 2>/dev/console) 2>&1
set -x
echo "Starting manual EC2 setup script execution... $(date)"

# --- Install httpd and basic health check file ---
echo "Updating dnf packages and installing httpd..."
sudo dnf update -y
sudo dnf install -y httpd
sudo systemctl start httpd
sudo systemctl enable httpd
echo "OK" | sudo tee /var/www/html/healthz # Create health check file
echo "<html><body><h1>Initial Manual Setup Complete!</h1></body></html>" | sudo tee /var/www/html/index.html
sudo sed -i 's/Listen 80/Listen 8080/' /etc/httpd/conf/httpd.conf # Configure httpd to listen on 8080
sudo systemctl restart httpd

# --- CodeDeploy Agent Installation ---
echo "Installing CodeDeploy agent dependencies..."
sudo dnf install -y ruby wget # Ensure ruby and wget are installed for the agent installer
if [ $? -ne 0 ]; then
    echo "Error: Failed to install ruby or wget." >&2
    exit 1
fi

echo "Downloading CodeDeploy agent installer..."
# IMPORTANT: Replace us-east-1 with your actual EC2 instance's AWS region
CODEDEPLOY_AGENT_INSTALLER_URL="https://aws-codedeploy-us-east-1.s3.us-east-1.amazonaws.com/latest/install"
wget $CODEDEPLOY_AGENT_INSTALLER_URL -O /tmp/install
if [ $? -ne 0 ]; then
    echo "Error: Failed to download CodeDeploy agent installer from $CODEDEPLOY_AGENT_INSTALLER_URL." >&2
    exit 1
fi

echo "Making installer executable and running..."
sudo chmod +x /tmp/install
sudo /tmp/install auto # 'auto' for Amazon Linux, RHEL, CentOS
if [ $? -ne 0 ]; then
    echo "Error: CodeDeploy agent installation failed." >&2
    exit 1
fi

echo "Checking CodeDeploy agent status..."
sudo systemctl status codedeploy-agent
if [ $? -ne 0 ]; then
    echo "Error: CodeDeploy agent is not running after installation." >&2
    exit 1
fi
echo "CodeDeploy agent installed and running successfully."

echo "Finished manual EC2 setup script execution. $(date)"
