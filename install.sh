#!/bin/bash

# RaspRec Quick Install Script
# Run this script on your Raspberry Pi to automatically clone and setup RTSP camera streaming

set -e  # Exit on any error

echo "=== RaspRec Quick Install ==="
echo "This script will clone and setup RTSP camera streaming on your Raspberry Pi"
echo ""

# Check if we're running on Raspberry Pi
if ! command -v vcgencmd &> /dev/null; then
    echo "WARNING: This doesn't appear to be a Raspberry Pi system"
    echo "vcgencmd command not found"
    read -p "Continue anyway? (y/N): " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        exit 1
    fi
fi

# Check if git is installed
if ! command -v git &> /dev/null; then
    echo "Installing git..."
    sudo apt-get update
    sudo apt-get install -y git
fi

# Set repository URL (update this with your actual repository URL)
REPO_URL="https://github.com/mientjan/rasprec.git"
INSTALL_DIR="$HOME/rasprec"

# Remove existing directory if it exists
if [ -d "$INSTALL_DIR" ]; then
    echo "Removing existing rasprec directory..."
    rm -rf "$INSTALL_DIR"
fi

# Clone the repository
echo "Cloning RaspRec repository..."
git clone "$REPO_URL" "$INSTALL_DIR"

# Change to the project directory
cd "$INSTALL_DIR"

# Make the setup script executable
chmod +x run.sh

echo ""
echo "=== Repository cloned successfully! ==="
echo "Location: $INSTALL_DIR"
echo ""
echo "Next steps:"
echo "1. cd $INSTALL_DIR"
echo "2. ./run.sh"
echo ""
echo "The setup script will:"
echo "- Install all dependencies (VLC, ffmpeg, monitoring tools)"
echo "- Configure RTSP streaming with stability features"
echo "- Set up automatic monitoring every 5 minutes"
echo "- Create necessary users and permissions"
echo "- Start the camera stream automatically"
echo ""

# Ask if user wants to run setup immediately
read -p "Would you like to run the setup now? (y/N): " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    echo ""
    echo "Starting setup..."
    ./run.sh
else
    echo ""
    echo "Setup skipped. Run './run.sh' when you're ready to configure the camera."
    echo "Make sure to enable the camera first with: sudo raspi-config"
fi

echo ""
echo "Installation script completed!"
