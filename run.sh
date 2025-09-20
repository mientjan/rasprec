#!/bin/bash

# RaspRec Setup Script
# Sets up stable RTSP camera streaming with monitoring and long-term reliability

echo "=== RaspRec Setup ==="
echo "Setting up stable RTSP camera streaming for Raspberry Pi..."

# Check if we're running on Raspberry Pi
if ! command -v vcgencmd &> /dev/null; then
    echo "WARNING: This doesn't appear to be a Raspberry Pi system"
    echo "vcgencmd command not found"
fi

# Check if camera is enabled
if command -v vcgencmd &> /dev/null; then
    CAMERA_STATUS=$(vcgencmd get_camera 2>/dev/null || echo "supported=0 detected=0")
    if [[ $CAMERA_STATUS != *"detected=1"* ]]; then
        echo "WARNING: Camera not detected. Enable it with:"
        echo "sudo raspi-config -> Interface Options -> Camera -> Enable"
        echo "Then reboot and run this script again."
        read -p "Continue anyway? (y/N): " -n 1 -r
        echo
        if [[ ! $REPLY =~ ^[Yy]$ ]]; then
            exit 1
        fi
    else
        echo "✓ Camera detected"
    fi
fi

# Check GPU memory
if command -v vcgencmd &> /dev/null; then
    GPU_MEM=$(vcgencmd get_mem gpu | cut -d'=' -f2 | cut -d'M' -f1)
    if [ "$GPU_MEM" -lt 128 ]; then
        echo "WARNING: GPU memory is ${GPU_MEM}M"
        echo "Recommend increasing to 128M+ for stability:"
        echo "sudo raspi-config -> Advanced Options -> Memory Split -> 128"
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

# Create user if it doesn't exist
if ! id "hansolo" &>/dev/null; then
    echo "Creating user 'hansolo'..."
    sudo useradd -m -s /bin/bash hansolo
    sudo usermod -a -G video hansolo
    echo "User 'hansolo' created and added to video group"
fi

echo "Setting up streaming script..."
# Copy and install the streaming script
sudo cp rtsp-camera.sh /home/hansolo/rtsp-camera.sh
sudo chmod +x /home/hansolo/rtsp-camera.sh
sudo chown hansolo:hansolo /home/hansolo/rtsp-camera.sh

echo "Installing systemd service..."
# Install the service file
sudo cp rtsp-camera.service /etc/systemd/system/rtsp-camera.service

echo "Setting up monitoring script..."
# Install monitoring script
sudo cp camera-monitor.sh /home/hansolo/
sudo chmod +x /home/hansolo/camera-monitor.sh
sudo chown hansolo:hansolo /home/hansolo/camera-monitor.sh

# Add monitoring to crontab
echo "Setting up automatic monitoring (every 5 minutes)..."
(sudo crontab -l 2>/dev/null; echo "*/5 * * * * /home/hansolo/camera-monitor.sh") | sudo crontab -

# Stop any existing service
sudo systemctl stop rtsp-camera 2>/dev/null || true

echo "Starting systemd service..."
sudo systemctl daemon-reload
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
