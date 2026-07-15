#!/bin/bash

# RaspRec Diagnostic Script
# This script helps diagnose common issues with the RTSP camera streaming setup

echo "=== RaspRec Diagnostic Tool ==="
echo "Checking system status and common issues..."
echo ""

# Color codes for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Function to print status
print_status() {
    if [ $1 -eq 0 ]; then
        echo -e "${GREEN}✓${NC} $2"
    else
        echo -e "${RED}✗${NC} $2"
    fi
}

print_warning() {
    echo -e "${YELLOW}⚠${NC} $1"
}

echo "1. Checking Camera Status..."
# Check camera using modern libcamera tools
camera_detected=false
camera_method=""
camera_count=0

if command -v rpicam-still &> /dev/null; then
    if rpicam-still --list-cameras 2>/dev/null | grep -q "Available cameras"; then
        camera_detected=true
        camera_method="rpicam-still"
        camera_count=$(rpicam-still --list-cameras 2>/dev/null | grep -c ":" || echo "0")
    fi
elif command -v libcamera-still &> /dev/null; then
    if libcamera-still --list-cameras 2>/dev/null | grep -q "Available cameras"; then
        camera_detected=true
        camera_method="libcamera-still"
        camera_count=$(libcamera-still --list-cameras 2>/dev/null | grep -c ":" || echo "0")
    fi
fi

if [ "$camera_detected" = true ]; then
    print_status 0 "Camera detected via $camera_method ($camera_count camera(s))"
else
    print_status 1 "No cameras detected via modern libcamera tools"
    if command -v rpicam-still &> /dev/null || command -v libcamera-still &> /dev/null; then
        echo "   Modern camera tools available but no cameras found"
        echo "   Check: 1) Physical connection 2) /boot/config.txt 3) Reboot"
    else
        echo "   No modern camera tools found - very old Raspberry Pi OS?"
    fi
fi

# Legacy vcgencmd check (for reference, often unreliable on modern systems)
if command -v vcgencmd &> /dev/null; then
    CAMERA_STATUS=$(vcgencmd get_camera 2>/dev/null || echo "unavailable")
    echo "   Legacy vcgencmd status: $CAMERA_STATUS (may be unreliable on modern systems)"
fi

echo ""
echo "2. Checking SSH Service..."
systemctl is-active ssh &>/dev/null
print_status $? "SSH service is running"

systemctl is-enabled ssh &>/dev/null
print_status $? "SSH service is enabled for auto-start"

echo ""
echo "3. Checking Network Connectivity..."
ping -c 1 8.8.8.8 &>/dev/null
print_status $? "Internet connectivity"

IP_ADDR=$(hostname -I | awk '{print $1}')
if [ ! -z "$IP_ADDR" ]; then
    print_status 0 "Network interface configured (IP: $IP_ADDR)"
else
    print_status 1 "No IP address assigned"
fi

echo ""
echo "4. Checking MediaMTX Installation..."
which mediamtx &>/dev/null || [ -x /usr/local/bin/mediamtx ]
print_status $? "MediaMTX is installed"

if [ -x /usr/local/bin/mediamtx ]; then
    MTX_VERSION=$(/usr/local/bin/mediamtx --version 2>/dev/null | head -n1)
    echo "   MediaMTX Version: $MTX_VERSION"
fi

echo ""
echo "5. Checking MediaMTX Service..."
systemctl is-active mediamtx &>/dev/null
print_status $? "MediaMTX service is running"

systemctl is-enabled mediamtx &>/dev/null
print_status $? "MediaMTX service is enabled for auto-start"

if systemctl is-active mediamtx &>/dev/null; then
    echo "   Service has been running for: $(systemctl show mediamtx --property=ActiveEnterTimestamp --value | cut -d' ' -f2-)"
fi

echo ""
echo "6. Checking Service Configuration..."
if [ -f "/etc/systemd/system/mediamtx.service" ]; then
    print_status 0 "Service file exists"

    # Check if user exists
    SERVICE_USER=$(grep "User=" /etc/systemd/system/mediamtx.service | cut -d'=' -f2)
    if id "$SERVICE_USER" &>/dev/null; then
        print_status 0 "Service user '$SERVICE_USER' exists"
    else
        print_status 1 "Service user '$SERVICE_USER' does not exist"
    fi

    # Check if MediaMTX config exists
    if [ -f "/usr/local/etc/mediamtx.yml" ]; then
        print_status 0 "MediaMTX config exists at /usr/local/etc/mediamtx.yml"
    else
        print_status 1 "MediaMTX config not found at /usr/local/etc/mediamtx.yml"
    fi
else
    print_status 1 "Service file not found (has run.sh been run?)"
fi

echo ""
echo "7. Checking Port Availability..."
if netstat -tlnp 2>/dev/null | grep ":8554" &>/dev/null; then
    print_status 0 "Port 8554 is in use (RTSP service likely running)"
    PROCESS=$(netstat -tlnp 2>/dev/null | grep ":8554" | awk '{print $7}')
    echo "   Process using port: $PROCESS"
else
    print_status 1 "Port 8554 is not in use (RTSP service not running)"
fi

echo ""
echo "8. Checking System Resources..."
# Check memory usage
MEM_USAGE=$(free | grep Mem | awk '{printf "%.1f", $3/$2 * 100.0}')
echo "   Memory usage: ${MEM_USAGE}%"
if (( $(echo "$MEM_USAGE > 80" | bc -l) )); then
    print_warning "High memory usage detected"
fi

# Check temperature
if command -v vcgencmd &>/dev/null; then
    TEMP=$(vcgencmd measure_temp | cut -d'=' -f2 | cut -d"'" -f1)
    echo "   CPU Temperature: ${TEMP}°C"
    if (( $(echo "$TEMP > 70" | bc -l) )); then
        print_warning "High CPU temperature detected"
    fi
fi

# Check voltage
if command -v vcgencmd &>/dev/null; then
    VOLTAGE=$(vcgencmd measure_volts | cut -d'=' -f2)
    echo "   Voltage: $VOLTAGE"
fi

echo ""
echo "9. Testing Camera Functionality..."
if command -v rpicam-vid &>/dev/null; then
    print_status 0 "rpicam-vid command available"
    echo "   Testing camera capture (5 seconds)..."
    timeout 5 rpicam-vid -t 5000 -o /tmp/test_camera.h264 &>/dev/null
    if [ $? -eq 0 ] && [ -f "/tmp/test_camera.h264" ]; then
        FILE_SIZE=$(stat -f%z /tmp/test_camera.h264 2>/dev/null || stat -c%s /tmp/test_camera.h264 2>/dev/null)
        if [ "$FILE_SIZE" -gt 1000 ]; then
            print_status 0 "Camera capture test successful (${FILE_SIZE} bytes)"
        else
            print_status 1 "Camera capture test failed - file too small"
        fi
        rm -f /tmp/test_camera.h264
    else
        print_status 1 "Camera capture test failed"
    fi
elif command -v raspivid &>/dev/null; then
    print_status 0 "raspivid command available (legacy)"
    print_warning "Consider upgrading to rpicam-vid for better performance"
else
    print_status 1 "No camera capture command available"
fi

echo ""
echo "10. Checking Tailscale (secure remote access)..."
if command -v tailscale &>/dev/null; then
    if tailscale status &>/dev/null; then
        print_status 0 "Tailscale is up"
        TS_NAME=$(tailscale status --json 2>/dev/null | grep -o '"DNSName":"[^"]*"' | head -1 | cut -d'"' -f4 | sed 's/\.$//')
        TS_IP=$(tailscale ip -4 2>/dev/null | head -1)
        [ -n "$TS_NAME" ] && echo "   MagicDNS name: $TS_NAME"
        [ -n "$TS_IP" ] && echo "   Tailscale IP:  $TS_IP"
    else
        print_status 1 "Tailscale installed but not connected (run: sudo tailscale up --ssh)"
    fi
else
    print_warning "Tailscale not installed — run ./setup-tailscale.sh for secure remote access"
fi

echo ""
echo "11. Recent Service Logs..."
echo "   Last 5 MediaMTX log entries:"
journalctl -u mediamtx -n 5 --no-pager -q

echo ""
echo "=== Diagnostic Summary ==="
echo "System IP Address: $IP_ADDR"
echo "LAN Stream URL:  rtsp://$IP_ADDR:8554/cam   (also http://$IP_ADDR:8889/cam for WebRTC)"
[ -n "$TS_NAME" ] && echo "Remote (Tailscale): rtsp://$TS_NAME:8554/cam"
echo ""
echo "SECURITY: reach the stream over Tailscale only. Never forward ports 8554/8889."
echo ""
echo "If issues persist, check the full service logs with:"
echo "sudo journalctl -u mediamtx -f"
