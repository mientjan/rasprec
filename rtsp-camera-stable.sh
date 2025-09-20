#!/bin/bash

# Stable RTSP Camera Streaming Script
# Addresses long-term stability issues with memory leaks and driver timeouts

# Exit on any error
set -e

# Logging function
log_message() {
    echo "$(date '+%Y-%m-%d %H:%M:%S'): $1" >&2
}

log_message "Starting RTSP camera stream..."

# Check if camera is available
if ! vcgencmd get_camera | grep -q "detected=1"; then
    log_message "ERROR: Camera not detected"
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
exec rpicam-vid \
    --timeout 0 \
    --framerate 15 \
    --width 720 \
    --height 480 \
    --bitrate 1000000 \
    --profile baseline \
    --level 3.1 \
    --intra 30 \
    --inline \
    --flush \
    --nopreview \
    --output - | \
cvlc \
    --intf dummy \
    --no-audio \
    --no-video-title-show \
    --no-stats \
    --no-osd \
    --no-interact \
    --no-qt-privacy-ask \
    --no-qt-updates-notifier \
    --extraintf logger \
    --verbose 0 \
    stream:///dev/stdin \
    --sout '#rtp{sdp=rtsp://:8554/stream1,caching=500}' \
    :demux=h264
