#!/bin/bash

# GPU Memory Setup Script for Raspberry Pi Camera Streaming
# Automatically configures optimal GPU memory split for RTSP camera streaming

set -e

echo "=== GPU Memory Setup for Camera Streaming ==="
echo "This script will configure optimal GPU memory allocation for camera streaming"
echo ""

# Check if we're on a Raspberry Pi
if ! command -v vcgencmd &> /dev/null; then
    echo "ERROR: This script is designed for Raspberry Pi systems"
    echo "vcgencmd command not found"
    exit 1
fi

# Get current GPU memory
CURRENT_GPU_MEM=$(vcgencmd get_mem gpu | cut -d'=' -f2 | cut -d'M' -f1)
echo "Current GPU memory: ${CURRENT_GPU_MEM}M"

# Get total system memory
TOTAL_MEM=$(vcgencmd get_mem arm | cut -d'=' -f2 | cut -d'M' -f1)
TOTAL_SYSTEM_MEM=$((CURRENT_GPU_MEM + TOTAL_MEM))
echo "Total system memory: ${TOTAL_SYSTEM_MEM}M"

# Determine recommended GPU memory based on total memory
if [ "$TOTAL_SYSTEM_MEM" -le 256 ]; then
    RECOMMENDED_GPU_MEM=128
    echo "Detected 256MB system - recommending 128MB for GPU"
elif [ "$TOTAL_SYSTEM_MEM" -le 512 ]; then
    RECOMMENDED_GPU_MEM=128
    echo "Detected 512MB system - recommending 128MB for GPU"
else
    RECOMMENDED_GPU_MEM=128
    echo "Detected ${TOTAL_SYSTEM_MEM}MB system - recommending 128MB for GPU"
fi

# Check if current setting is already optimal
if [ "$CURRENT_GPU_MEM" -ge "$RECOMMENDED_GPU_MEM" ]; then
    echo "✓ GPU memory is already optimally configured (${CURRENT_GPU_MEM}M >= ${RECOMMENDED_GPU_MEM}M)"
    echo "No changes needed."
    exit 0
fi

echo ""
echo "⚠️  Current GPU memory (${CURRENT_GPU_MEM}M) is below recommended (${RECOMMENDED_GPU_MEM}M)"
echo "This may cause camera streaming performance issues or failures."
echo ""
echo "This script will:"
echo "1. Backup your current /boot/firmware/config.txt"
echo "2. Set gpu_mem=${RECOMMENDED_GPU_MEM}"
echo "3. Require a reboot to apply changes"
echo ""

read -p "Continue with GPU memory configuration? (y/N): " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "Configuration cancelled."
    exit 0
fi

# Determine config file location (newer vs older Raspberry Pi OS)
CONFIG_FILE=""
if [ -f "/boot/firmware/config.txt" ]; then
    CONFIG_FILE="/boot/firmware/config.txt"
    echo "Using modern config location: /boot/firmware/config.txt"
elif [ -f "/boot/config.txt" ]; then
    CONFIG_FILE="/boot/config.txt"
    echo "Using legacy config location: /boot/config.txt"
else
    echo "ERROR: Cannot find config.txt file"
    echo "Checked: /boot/firmware/config.txt and /boot/config.txt"
    exit 1
fi

# Backup current config
BACKUP_FILE="${CONFIG_FILE}.backup.$(date +%Y%m%d_%H%M%S)"
echo "Creating backup: $BACKUP_FILE"
sudo cp "$CONFIG_FILE" "$BACKUP_FILE"

# Check if gpu_mem is already set
if grep -q "^gpu_mem=" "$CONFIG_FILE"; then
    # Update existing gpu_mem setting
    echo "Updating existing gpu_mem setting..."
    sudo sed -i "s/^gpu_mem=.*/gpu_mem=${RECOMMENDED_GPU_MEM}/" "$CONFIG_FILE"
elif grep -q "^#gpu_mem=" "$CONFIG_FILE"; then
    # Uncomment and update commented gpu_mem setting
    echo "Enabling and updating commented gpu_mem setting..."
    sudo sed -i "s/^#gpu_mem=.*/gpu_mem=${RECOMMENDED_GPU_MEM}/" "$CONFIG_FILE"
else
    # Add new gpu_mem setting
    echo "Adding new gpu_mem setting..."
    echo "" | sudo tee -a "$CONFIG_FILE" > /dev/null
    echo "# GPU memory allocation for camera streaming" | sudo tee -a "$CONFIG_FILE" > /dev/null
    echo "gpu_mem=${RECOMMENDED_GPU_MEM}" | sudo tee -a "$CONFIG_FILE" > /dev/null
fi

echo ""
echo "✓ GPU memory configuration updated successfully!"
echo "✓ Backup created: $BACKUP_FILE"
echo ""
echo "=== REBOOT REQUIRED ==="
echo "The new GPU memory setting will take effect after reboot."
echo ""
echo "After reboot, verify with: vcgencmd get_mem gpu"
echo "Expected result: gpu=${RECOMMENDED_GPU_MEM}M"
echo ""

read -p "Reboot now to apply changes? (y/N): " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    echo "Rebooting in 3 seconds..."
    sleep 3
    sudo reboot
else
    echo "Reboot skipped. Remember to reboot manually to apply GPU memory changes:"
    echo "sudo reboot"
fi
