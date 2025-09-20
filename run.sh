#!/bin/bash

# RaspRec Setup Script
# Sets up stable RTSP camera streaming with monitoring and long-term reliability

echo "=== RaspRec Setup ==="
echo "Setting up stable RTSP camera streaming for Raspberry Pi..."

# Check if this is an update or fresh install
if [ -f "/etc/systemd/system/rtsp-camera.service" ]; then
    echo "🔄 Existing installation detected - performing update/reinstall"
    echo "   Previous configurations will be backed up with timestamp"
else
    echo "🆕 Fresh installation detected"
fi
echo ""

# Check if we're running on Raspberry Pi
if ! command -v vcgencmd &> /dev/null; then
    echo "WARNING: This doesn't appear to be a Raspberry Pi system"
    echo "vcgencmd command not found"
fi

# Check if camera is available using modern libcamera tools
echo "Checking camera availability..."
if command -v rpicam-still &> /dev/null; then
    # Use modern rpicam-apps (Bookworm and newer)
    if rpicam-still --list-cameras 2>/dev/null | grep -q "Available cameras"; then
        echo "✓ Camera detected via rpicam-still"
        CAMERA_COUNT=$(rpicam-still --list-cameras 2>/dev/null | grep -c ":")
        echo "  Found $CAMERA_COUNT camera(s)"
    else
        echo "WARNING: No cameras detected via rpicam-still"
        echo "Camera troubleshooting:"
        echo "1. Check physical connection of camera module"
        echo "2. Verify /boot/config.txt has 'camera_auto_detect=1'"
        echo "3. Try: sudo reboot"
        echo "4. For older cameras, you may need to disable auto-detect and use specific overlay"
        read -p "Continue anyway? (y/N): " -n 1 -r
        echo
        if [[ ! $REPLY =~ ^[Yy]$ ]]; then
            exit 1
        fi
    fi
elif command -v libcamera-still &> /dev/null; then
    # Fallback to libcamera-still (older Bookworm)
    if libcamera-still --list-cameras 2>/dev/null | grep -q "Available cameras"; then
        echo "✓ Camera detected via libcamera-still"
        CAMERA_COUNT=$(libcamera-still --list-cameras 2>/dev/null | grep -c ":")
        echo "  Found $CAMERA_COUNT camera(s)"
    else
        echo "WARNING: No cameras detected via libcamera-still"
        echo "Camera troubleshooting:"
        echo "1. Check physical connection of camera module"
        echo "2. Verify /boot/config.txt has 'camera_auto_detect=1'"
        echo "3. Try: sudo reboot"
        read -p "Continue anyway? (y/N): " -n 1 -r
        echo
        if [[ ! $REPLY =~ ^[Yy]$ ]]; then
            exit 1
        fi
    fi
else
    echo "WARNING: Neither rpicam-still nor libcamera-still found"
    echo "This may be an older Raspberry Pi OS version or missing camera software"
    echo "Modern Raspberry Pi OS (Bookworm+) should have rpicam-apps installed by default"
    read -p "Continue anyway? (y/N): " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        exit 1
    fi
fi

# Check GPU memory
if command -v vcgencmd &> /dev/null; then
    GPU_MEM=$(vcgencmd get_mem gpu | cut -d'=' -f2 | cut -d'M' -f1)
    if [ "$GPU_MEM" -lt 128 ]; then
        echo "WARNING: GPU memory is ${GPU_MEM}M"
        echo "Recommend increasing to 128M+ for camera streaming stability:"
        echo ""
        echo "Quick fix: Run the GPU memory setup script:"
        echo "  ./setup-gpu-memory.sh"
        echo ""
        echo "Manual method:"
        echo "1. Edit: sudo nano /boot/firmware/config.txt (or /boot/config.txt)"
        echo "2. Add/modify: gpu_mem=128"
        echo "3. Reboot: sudo reboot"
        echo ""
        read -p "Run GPU memory setup script now? (y/N): " -n 1 -r
        echo
        if [[ $REPLY =~ ^[Yy]$ ]]; then
            if [ -f "setup-gpu-memory.sh" ]; then
                chmod +x setup-gpu-memory.sh
                ./setup-gpu-memory.sh
                # If script reboots, this won't continue
                echo "GPU memory setup completed. Continuing with installation..."
            else
                echo "setup-gpu-memory.sh not found, continuing with manual instructions above"
            fi
        fi
    else
        echo "✓ GPU memory: ${GPU_MEM}M"
    fi
fi

echo "Installing dependencies..."
sudo apt-get update
sudo apt-get install -y vlc-bin vlc-plugin-base bc netcat-openbsd

# Install ffprobe if available (for better stream monitoring)
if ! command -v ffprobe &> /dev/null; then
    echo "Installing ffmpeg for enhanced monitoring..."
    sudo apt-get install -y ffmpeg
fi

# Configuration - ask for username if not provided
if [ -z "$CAMERA_USER" ]; then
    echo ""
    echo "Camera streaming requires a user account."
    
    # Get the actual user (not root when using sudo)
    ACTUAL_USER="${SUDO_USER:-$USER}"
    
    echo "Options:"
    echo "1. Use current user ($ACTUAL_USER)"
    echo "2. Create dedicated camera user"
    echo ""
    read -p "Choose option (1/2) or enter custom username: " USER_CHOICE
    
    case "$USER_CHOICE" in
        1)
            CAMERA_USER="$ACTUAL_USER"
            echo "Using current user: $CAMERA_USER"
            ;;
        2)
            read -p "Enter username for dedicated camera user: " CAMERA_USER
            if [ -z "$CAMERA_USER" ]; then
                echo "ERROR: Username is required"
                exit 1
            fi
            ;;
        "")
            echo "ERROR: Please choose an option or enter a username"
            exit 1
            ;;
        *)
            CAMERA_USER="$USER_CHOICE"
            echo "Using custom username: $CAMERA_USER"
            ;;
    esac
fi

# Create user if it doesn't exist
if ! id "$CAMERA_USER" &>/dev/null; then
    echo "Creating user '$CAMERA_USER'..."
    sudo useradd -m -s /bin/bash "$CAMERA_USER"
    if getent group video > /dev/null 2>&1; then
        sudo usermod -a -G video "$CAMERA_USER"
        echo "User '$CAMERA_USER' created and added to existing video group"
    else
        echo "User '$CAMERA_USER' created (video group not found - may need manual camera permissions)"
    fi
else
    echo "Using existing user '$CAMERA_USER'"
    # Ensure user is in video group (only if group exists)
    if getent group video > /dev/null 2>&1; then
        if ! groups "$CAMERA_USER" | grep -q "\bvideo\b"; then
            sudo usermod -a -G video "$CAMERA_USER"
            echo "Added '$CAMERA_USER' to existing video group"
        else
            echo "User '$CAMERA_USER' already in video group"
        fi
    fi
fi

echo "Setting up streaming script..."
# Backup existing script if it exists
if [ -f "/home/$CAMERA_USER/rtsp-camera.sh" ]; then
    echo "Backing up existing streaming script..."
    sudo cp /home/$CAMERA_USER/rtsp-camera.sh /home/$CAMERA_USER/rtsp-camera.sh.backup.$(date +%Y%m%d_%H%M%S)
fi

# Copy and install the streaming script
sudo cp rtsp-camera.sh /home/$CAMERA_USER/rtsp-camera.sh
sudo chmod +x /home/$CAMERA_USER/rtsp-camera.sh
sudo chown $CAMERA_USER:$CAMERA_USER /home/$CAMERA_USER/rtsp-camera.sh

echo "Installing systemd service..."
# Stop existing service if running
if systemctl is-active --quiet rtsp-camera; then
    echo "Stopping existing rtsp-camera service..."
    sudo systemctl stop rtsp-camera
fi

# Backup existing service file if it exists
if [ -f "/etc/systemd/system/rtsp-camera.service" ]; then
    echo "Backing up existing service configuration..."
    sudo cp /etc/systemd/system/rtsp-camera.service /etc/systemd/system/rtsp-camera.service.backup.$(date +%Y%m%d_%H%M%S)
fi

# Install the service file with correct user and paths
sed -e "s/User=CAMERA_USER_PLACEHOLDER/User=$CAMERA_USER/" \
    -e "s|/home/CAMERA_USER_PLACEHOLDER/|/home/$CAMERA_USER/|g" \
    rtsp-camera.service | sudo tee /etc/systemd/system/rtsp-camera.service > /dev/null

echo "Setting up monitoring script..."
# Backup existing monitoring script if it exists
if [ -f "/home/$CAMERA_USER/camera-monitor.sh" ]; then
    echo "Backing up existing monitoring script..."
    sudo cp /home/$CAMERA_USER/camera-monitor.sh /home/$CAMERA_USER/camera-monitor.sh.backup.$(date +%Y%m%d_%H%M%S)
fi

# Install monitoring script
sudo cp camera-monitor.sh /home/$CAMERA_USER/
sudo chmod +x /home/$CAMERA_USER/camera-monitor.sh
sudo chown $CAMERA_USER:$CAMERA_USER /home/$CAMERA_USER/camera-monitor.sh

# Add monitoring to crontab (avoid duplicates)
echo "Setting up automatic monitoring (every 5 minutes)..."
CRON_JOB="*/5 * * * * /home/$CAMERA_USER/camera-monitor.sh"
(sudo crontab -l 2>/dev/null | grep -v "/home/$CAMERA_USER/camera-monitor.sh"; echo "$CRON_JOB") | sudo crontab -

# Force reload systemd daemon for any service changes
echo "Reloading systemd daemon..."
sudo systemctl daemon-reload

echo "Starting systemd service..."
sudo systemctl enable rtsp-camera
sudo systemctl start rtsp-camera

# Wait a moment for service to start
sleep 5

# Check service status
if systemctl is-active --quiet rtsp-camera; then
    echo "✓ Service started successfully"
    
    # Get IP address for RTSP URL
    IP_ADDR=$(hostname -I | awk '{print $1}')
    if [ ! -z "$IP_ADDR" ]; then
        echo ""
        echo "=== Setup Complete ==="
        echo "RTSP stream is available at: rtsp://$IP_ADDR:8554/stream1"
        echo ""
        echo "Features enabled:"
        echo "- ✓ Enhanced stability configuration"
        echo "- ✓ Automatic monitoring every 5 minutes"
        echo "- ✓ Memory leak prevention"
        echo "- ✓ Thermal monitoring"
        echo "- ✓ Auto-restart on failures"
        echo ""
        echo "To view the stream:"
        echo "- VLC: Media -> Open Network Stream -> rtsp://$IP_ADDR:8554/stream1"
        echo "- FFplay: ffplay rtsp://$IP_ADDR:8554/stream1"
        echo ""
        echo "Monitoring and logs:"
        echo "- Service status: sudo systemctl status rtsp-camera"
        echo "- Service logs: sudo journalctl -u rtsp-camera -f"
        echo "- Monitor logs: sudo tail -f /var/log/camera-monitor.log"
        echo "- Run diagnostics: ./diagnose.sh"
    fi
else
    echo "✗ Service failed to start"
    echo "Check logs with: sudo journalctl -u rtsp-camera -xe"
    exit 1
fi

echo ""
echo "Setup completed successfully!"
echo "The system will now automatically monitor and restart the stream if needed."

# Clean up old backup files (keep only last 5)
echo ""
echo "Cleaning up old backup files (keeping last 5)..."
sudo find /home/$CAMERA_USER/ -name "*.backup.*" -type f | sort | head -n -5 | xargs -r sudo rm -f
sudo find /etc/systemd/system/ -name "rtsp-camera.service.backup.*" -type f | sort | head -n -5 | xargs -r sudo rm -f
echo "Cleanup completed."
