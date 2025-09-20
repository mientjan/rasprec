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
# Check if camera is enabled and detected
vcgencmd get_camera &>/dev/null
if [ $? -eq 0 ]; then
    CAMERA_STATUS=$(vcgencmd get_camera)
    echo "   Camera status: $CAMERA_STATUS"
    if [[ $CAMERA_STATUS == *"detected=1"* ]]; then
        print_status 0 "Camera detected"
    else
        print_status 1 "Camera not detected - check physical connection"
    fi
else
    print_status 1 "Cannot check camera status - vcgencmd not available"
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
echo "4. Checking VLC Installation..."
which cvlc &>/dev/null
print_status $? "VLC (cvlc) is installed"

if which cvlc &>/dev/null; then
    VLC_VERSION=$(cvlc --version 2>/dev/null | head -n1)
    echo "   VLC Version: $VLC_VERSION"
fi

echo ""
echo "5. Checking RTSP Camera Service..."
systemctl is-active rtsp-camera &>/dev/null
print_status $? "RTSP camera service is running"

systemctl is-enabled rtsp-camera &>/dev/null
print_status $? "RTSP camera service is enabled for auto-start"

if systemctl is-active rtsp-camera &>/dev/null; then
    echo "   Service has been running for: $(systemctl show rtsp-camera --property=ActiveEnterTimestamp --value | cut -d' ' -f2-)"
fi

echo ""
echo "6. Checking Service Configuration..."
if [ -f "/etc/systemd/system/rtsp-camera.service" ]; then
    print_status 0 "Service file exists"
    
    # Check if user exists
    SERVICE_USER=$(grep "User=" /etc/systemd/system/rtsp-camera.service | cut -d'=' -f2)
    if id "$SERVICE_USER" &>/dev/null; then
        print_status 0 "Service user '$SERVICE_USER' exists"
    else
        print_status 1 "Service user '$SERVICE_USER' does not exist"
    fi
    
    # Check if script exists
    SCRIPT_PATH=$(grep "ExecStart=" /etc/systemd/system/rtsp-camera.service | cut -d'=' -f2)
    if [ -f "$SCRIPT_PATH" ]; then
        print_status 0 "Streaming script exists at $SCRIPT_PATH"
        if [ -x "$SCRIPT_PATH" ]; then
            print_status 0 "Streaming script is executable"
        else
            print_status 1 "Streaming script is not executable"
        fi
    else
        print_status 1 "Streaming script not found at $SCRIPT_PATH"
    fi
else
    print_status 1 "Service file not found"
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
echo "10. Recent Service Logs..."
if systemctl is-active rtsp-camera &>/dev/null; then
    echo "   Last 5 log entries:"
    journalctl -u rtsp-camera -n 5 --no-pager -q
else
    echo "   Service not running - showing last failed logs:"
    journalctl -u rtsp-camera -n 5 --no-pager -q
fi

echo ""
echo "=== Diagnostic Summary ==="
echo "System IP Address: $IP_ADDR"
echo "RTSP Stream URL: rtsp://$IP_ADDR:8554/stream1"
echo ""
echo "If issues persist, check the full service logs with:"
echo "sudo journalctl -u rtsp-camera -f"
echo ""
echo "For manual testing, try:"
echo "sudo systemctl stop rtsp-camera"
echo "sudo -u $SERVICE_USER $SCRIPT_PATH"
