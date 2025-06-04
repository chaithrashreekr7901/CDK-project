#!/bin/bash
echo "Starting Apache..."
systemctl start httpd
systemctl enable httpd
