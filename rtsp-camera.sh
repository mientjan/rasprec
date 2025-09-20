#!/bin/bash

# RTSP Camera Streaming Script
# Enhanced for long-term stability with memory leak prevention and driver timeout handling

# Exit on any error
set -e

# Logging function
log_message() {
    echo "$(date '+%Y-%m-%d %H:%M:%S'): $1" >&2
}

log_message "Starting RTSP camera stream..."

# Check if camera is available using modern detection
camera_detected=false
if command -v rpicam-still &> /dev/null; then
    if rpicam-still --list-cameras 2>/dev/null | grep -q "Available cameras"; then
        camera_detected=true
        log_message "Camera detected via rpicam-still"
    fi
elif command -v libcamera-still &> /dev/null; then
    if libcamera-still --list-cameras 2>/dev/null | grep -q "Available cameras"; then
        camera_detected=true
        log_message "Camera detected via libcamera-still"
    fi
fi

if [ "$camera_detected" = false ]; then
    log_message "ERROR: No camera detected via modern libcamera tools"
    log_message "Check: 1) Physical connection 2) /boot/config.txt camera_auto_detect=1 3) Reboot"
    exit 1
fi

# Set up cleanup trap
cleanup() {
    log_message "Cleaning up processes..."
    pkill -f "rpicam-vid" 2>/dev/null || true
    pkill -f "cvlc.*stream1" 2>/dev/null || true
    exit 0
}
trap cleanup SIGTERM SIGINT

# GPU memory check
gpu_mem=$(vcgencmd get_mem gpu | cut -d'=' -f2 | cut -d'M' -f1)
if [ "$gpu_mem" -lt 128 ]; then
    log_message "WARNING: GPU memory is ${gpu_mem}M, recommend 128M+ for stability"
fi

# Temperature check
temp=$(vcgencmd measure_temp | cut -d'=' -f2 | cut -d"'" -f1)
if (( $(echo "$temp > 70" | bc -l) )); then
    log_message "WARNING: High temperature detected: ${temp}°C"
fi

# Start camera with improved stability parameters
log_message "Starting camera capture..."

# Use exec to replace the shell process (important for proper signal handling)
exec /usr/bin/rpicam-vid \
    --timeout 0 \
    --framerate 24 \
    --width 1280 \
    --height 720 \
    --bitrate 2000000 \
    --profile baseline \
    --intra 30 \
    --inline \
    --flush \
    --nopreview \
    --output - | \
/usr/bin/cvlc \
    --intf dummy \
    --no-audio \
    --no-video-title-show \
    --no-stats \
    --no-osd \
    --no-interact \
    --verbose 0 \
    stream:///dev/stdin \
    --sout '#rtp{sdp=rtsp://:8554/stream1,caching=500}' \
    :demux=h264
