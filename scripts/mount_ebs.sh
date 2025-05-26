#!/bin/bash
# Shell script to format and mount an attached EBS volume.
# This script should be run as root or with sudo privileges.

# --- Configuration ---
# The device name assigned by EC2 when the volume is attached.
# This is what you specify in your CDK/CloudFormation attachment config.
# Common names: /dev/sdf, /dev/xvdf, /dev/sdg, /dev/xvdg, etc.
# For Nitro-based instances, it might be /dev/nvme1n1, /dev/nvme2n1, etc.
# **IMPORTANT**: You MUST set this variable correctly based on your attachment.
DEVICE_PATH_CONFIGURED="/dev/xvdf" # Example: Change this to match your attachment_config.device_name

# The directory where you want to mount the volume.
MOUNT_POINT="/data" # Example: Change this to your desired mount point.

# The filesystem type to use for formatting.
FILESYSTEM_TYPE="ext4"

# --- Script Logic ---
set -e # Exit immediately if a command exits with a non-zero status.
# set -x # Uncomment for debugging to see each command as it's executed.

# Function to find the actual device path (handles Xen vs Nitro naming)
find_actual_device() {
    local configured_device=$1
    local actual_device=""

    # Check for Xen-style device names (e.g., /dev/xvdf)
    if [ -b "$configured_device" ]; then
        actual_device="$configured_device"
    else
        # Check for Nitro-style device names (e.g., /dev/nvme1n1)
        # This is a common mapping, but might need adjustment based on your specific setup
        # /dev/sdf  -> /dev/nvme1n1
        # /dev/sdg  -> /dev/nvme2n1
        # ... and so on.
        # This simple mapping might not cover all cases perfectly.
        # A more robust method would be to list available block devices and match by size or other attributes if needed.
        nitro_device_name=$(echo "$configured_device" | sed 's/\/dev\/sd/\/dev\/nvme/g' | sed 's/\([a-z]\)$/\1n1/')
        if [ -b "$nitro_device_name" ]; then
            actual_device="$nitro_device_name"
        else
            # Try another common Nitro mapping if the first didn't work
            # e.g. /dev/xvdf -> /dev/nvme1n1 (less direct mapping, depends on enumeration)
            # For simplicity, we'll stick to the configured path or the direct sdX -> nvmeXn1 mapping.
            # If neither exists, we'll report an error.
            if [ -b "/dev/nvme1n1" ] && [ ! -L "/dev/nvme1n1" ]; then # Check if nvme1n1 exists and is not a symlink (like root)
                 # This part is heuristic. If you have multiple NVMe devices, it's not guaranteed to be the correct one
                 # without more specific identification (e.g., based on volume size if unique).
                 # For a single attached data volume, this might work.
                 logger -s "Configured device $configured_device not found directly. Checking common Nitro paths."
                 # A better approach if you know the volume ID: lsblk -o NAME,SERIAL | grep <volume-id-serial>
            fi
        fi
    fi

    if [ -z "$actual_device" ]; then
        logger -s "Error: Device $configured_device (or its Nitro equivalent) not found."
        exit 1
    fi
    echo "$actual_device"
}

ACTUAL_DEVICE_PATH=$(find_actual_device "$DEVICE_PATH_CONFIGURED")
logger -s "Actual device path identified as: $ACTUAL_DEVICE_PATH"

# 1. Check if the device has a filesystem
logger -s "Checking filesystem on $ACTUAL_DEVICE_PATH..."
# `file -sL` outputs "data" for unformatted devices on Amazon Linux.
# On other systems, it might be empty or different. `lsblk -f` is another option.
# Using `blkid` is generally more reliable for checking existing filesystems.
if ! sudo blkid -p "$ACTUAL_DEVICE_PATH" -s TYPE -o value > /dev/null 2>&1; then
    logger -s "Device $ACTUAL_DEVICE_PATH does not appear to have a filesystem or is unformatted."
    logger -s "Formatting $ACTUAL_DEVICE_PATH as $FILESYSTEM_TYPE..."
    sudo mkfs -t "$FILESYSTEM_TYPE" "$ACTUAL_DEVICE_PATH"
    logger -s "Formatting complete."
else
    EXISTING_FS_TYPE=$(sudo blkid -p "$ACTUAL_DEVICE_PATH" -s TYPE -o value)
    logger -s "Device $ACTUAL_DEVICE_PATH already has a filesystem: $EXISTING_FS_TYPE."
fi

# 2. Create mount point directory if it doesn't exist
if [ ! -d "$MOUNT_POINT" ]; then
    logger -s "Creating mount point directory $MOUNT_POINT..."
    sudo mkdir -p "$MOUNT_POINT"
    logger -s "Mount point $MOUNT_POINT created."
else
    logger -s "Mount point $MOUNT_POINT already exists."
fi

# 3. Mount the volume if not already mounted
# `grep -qs "$MOUNT_POINT" /proc/mounts` checks if the mount point is in use
if ! grep -qs " $MOUNT_POINT " /proc/mounts; then # Added spaces around MOUNT_POINT for more exact match
    logger -s "Mounting $ACTUAL_DEVICE_PATH to $MOUNT_POINT..."
    sudo mount "$ACTUAL_DEVICE_PATH" "$MOUNT_POINT"
    logger -s "$ACTUAL_DEVICE_PATH mounted to $MOUNT_POINT."
else
    logger -s "$ACTUAL_DEVICE_PATH (or another device) is already mounted at $MOUNT_POINT."
fi

# 4. Add to /etc/fstab for persistence across reboots
# Get UUID of the formatted device for fstab to avoid issues with device name changes
UUID=$(sudo blkid -s UUID -o value "$ACTUAL_DEVICE_PATH")

if [ -n "$UUID" ]; then
    FSTAB_ENTRY="UUID=$UUID $MOUNT_POINT $FILESYSTEM_TYPE defaults,nofail 0 2"
    # Check if entry already exists in fstab (to avoid duplicates)
    if ! grep -qs "^UUID=$UUID" /etc/fstab; then
        logger -s "Adding to /etc/fstab: $FSTAB_ENTRY"
        echo "$FSTAB_ENTRY" | sudo tee -a /etc/fstab
    else
        logger -s "Entry for UUID=$UUID already exists in /etc/fstab."
    fi
else
    logger -s "Error: Could not get UUID for $ACTUAL_DEVICE_PATH. Cannot add to /etc/fstab automatically."
    logger -s "Please add the entry manually to /etc/fstab if needed: $ACTUAL_DEVICE_PATH $MOUNT_POINT $FILESYSTEM_TYPE defaults,nofail 0 2"
fi

# Optional: Set permissions on the mount point after mounting
# sudo chown myuser:mygroup /data
# sudo chmod 775 /data

logger -s "EBS volume mounting script completed."
