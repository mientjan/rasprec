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

# Check if directory already exists
if [ -d "$INSTALL_DIR" ]; then
    echo "🔄 Existing RaspRec installation found at $INSTALL_DIR"
    echo "Updating repository..."
    
    cd "$INSTALL_DIR"
    
    # Check if it's a git repository
    if [ -d ".git" ]; then
        # Stash any local changes
        if ! git diff --quiet || ! git diff --cached --quiet; then
            echo "Stashing local changes..."
            git stash push -m "Auto-stash before update $(date)"
        fi
        
        # Pull latest changes
        echo "Pulling latest changes from repository..."
        git fetch origin
        git reset --hard origin/main
        
        echo "✓ Repository updated successfully"
    else
        echo "WARNING: Directory exists but is not a git repository"
        echo "Backing up existing directory and cloning fresh..."
        mv "$INSTALL_DIR" "${INSTALL_DIR}.backup.$(date +%Y%m%d_%H%M%S)"
        git clone "$REPO_URL" "$INSTALL_DIR"
        cd "$INSTALL_DIR"
    fi
else
    echo "🆕 Fresh installation - cloning repository..."
    git clone "$REPO_URL" "$INSTALL_DIR"
    cd "$INSTALL_DIR"
fi

# Make the setup script executable
chmod +x run.sh

echo ""
if [ -d "$INSTALL_DIR/.git" ] && git log --oneline -1 &>/dev/null; then
    echo "=== Repository updated successfully! ==="
    echo "Latest commit: $(git log --oneline -1)"
else
    echo "=== Repository setup completed! ==="
fi
echo "Location: $INSTALL_DIR"
echo ""

# Check if this is an update or fresh install for messaging
if systemctl is-active --quiet rtsp-camera 2>/dev/null; then
    echo "🔄 Existing RTSP service detected - this appears to be an update"
    echo "The setup script will:"
    echo "- Update all components with the latest versions"
    echo "- Backup existing configurations"
    echo "- Restart services with new code"
    echo "- Maintain existing settings and monitoring"
else
    echo "🆕 Fresh installation detected"
    echo "The setup script will:"
    echo "- Install all dependencies (VLC, ffmpeg, monitoring tools)"
    echo "- Configure RTSP streaming with stability features"
    echo "- Set up automatic monitoring every 5 minutes"
    echo "- Create necessary users and permissions"
    echo "- Start the camera stream automatically"
fi
echo ""

# Ask if user wants to run setup immediately
read -p "Would you like to run the setup/update now? (Y/n): " -n 1 -r
echo
if [[ $REPLY =~ ^[Nn]$ ]]; then
    echo ""
    echo "Setup skipped. Run the following commands when ready:"
    echo "  cd $INSTALL_DIR"
    echo "  ./run.sh"
else
    echo ""
    echo "Starting setup/update..."
    echo "========================================"
    ./run.sh
fi

echo ""
echo "Installation script completed!"
