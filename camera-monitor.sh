#!/bin/bash

# Camera Stream Monitor Script
# Monitors RTSP stream health and restarts service if needed
# Usage: Run via cron every 5 minutes

LOG_FILE="/var/log/camera-monitor.log"
RTSP_URL="rtsp://localhost:8554/stream1"
SERVICE_NAME="rtsp-camera"
MAX_RESTART_ATTEMPTS=3
RESTART_COUNT_FILE="/tmp/camera_restart_count"

# Function to log messages
log_message() {
    echo "$(date '+%Y-%m-%d %H:%M:%S'): $1" | tee -a "$LOG_FILE"
}

# Function to get restart count
get_restart_count() {
    if [ -f "$RESTART_COUNT_FILE" ]; then
        cat "$RESTART_COUNT_FILE"
    else
        echo "0"
    fi
}

# Function to increment restart count
increment_restart_count() {
    local count=$(get_restart_count)
    echo $((count + 1)) > "$RESTART_COUNT_FILE"
}

# Function to reset restart count
reset_restart_count() {
    echo "0" > "$RESTART_COUNT_FILE"
}

# Check if service is running
if ! systemctl is-active --quiet "$SERVICE_NAME"; then
    log_message "ERROR: Service $SERVICE_NAME is not running"
    
    # Check restart count
    restart_count=$(get_restart_count)
    if [ "$restart_count" -lt "$MAX_RESTART_ATTEMPTS" ]; then
        log_message "Attempting to start service (attempt $((restart_count + 1))/$MAX_RESTART_ATTEMPTS)"
        systemctl start "$SERVICE_NAME"
        increment_restart_count
        
        # Wait a bit and check if it started successfully
        sleep 10
        if systemctl is-active --quiet "$SERVICE_NAME"; then
            log_message "Service started successfully"
            reset_restart_count
        else
            log_message "Failed to start service"
        fi
    else
        log_message "Maximum restart attempts reached. Manual intervention required."
    fi
    exit 1
fi

# Test RTSP stream connectivity
log_message "Testing RTSP stream connectivity..."

# Method 1: Try to probe the stream with ffprobe (if available)
if command -v ffprobe >/dev/null 2>&1; then
    timeout 15 ffprobe -v quiet -select_streams v:0 -show_entries stream=width,height -of csv=p=0 "$RTSP_URL" >/dev/null 2>&1
    ffprobe_result=$?
else
    ffprobe_result=1
fi

# Method 2: Try to connect to the port
timeout 5 nc -z localhost 8554 >/dev/null 2>&1
nc_result=$?

# Method 3: Check if VLC process is consuming CPU (indicates active streaming)
vlc_cpu=$(ps aux | grep "[c]vlc" | awk '{sum += $3} END {print sum+0}')
vlc_active=0
if (( $(echo "$vlc_cpu > 0.1" | bc -l 2>/dev/null || echo "0") )); then
    vlc_active=1
fi

# Evaluate stream health
stream_healthy=0
if [ $ffprobe_result -eq 0 ] || ([ $nc_result -eq 0 ] && [ $vlc_active -eq 1 ]); then
    stream_healthy=1
    log_message "Stream is healthy (ffprobe: $ffprobe_result, port: $nc_result, vlc_cpu: $vlc_cpu%)"
    reset_restart_count
else
    log_message "Stream appears unhealthy (ffprobe: $ffprobe_result, port: $nc_result, vlc_cpu: $vlc_cpu%)"
fi

# Check system resources
memory_usage=$(free | grep Mem | awk '{printf "%.1f", $3/$2 * 100.0}')
if command -v vcgencmd >/dev/null 2>&1; then
    temp=$(vcgencmd measure_temp | cut -d'=' -f2 | cut -d"'" -f1)
    throttled=$(vcgencmd get_throttled)
else
    temp="N/A"
    throttled="N/A"
fi

log_message "System status - Memory: ${memory_usage}%, Temp: ${temp}°C, Throttled: $throttled"

# Check for concerning system conditions
restart_needed=0

# High memory usage
if (( $(echo "$memory_usage > 90" | bc -l 2>/dev/null || echo "0") )); then
    log_message "WARNING: High memory usage detected (${memory_usage}%)"
    restart_needed=1
fi

# Thermal throttling
if [ "$throttled" != "0x0" ] && [ "$throttled" != "N/A" ]; then
    log_message "WARNING: Thermal throttling detected ($throttled)"
    restart_needed=1
fi

# Stream unhealthy
if [ $stream_healthy -eq 0 ]; then
    restart_needed=1
fi

# Restart service if needed
if [ $restart_needed -eq 1 ]; then
    restart_count=$(get_restart_count)
    if [ "$restart_count" -lt "$MAX_RESTART_ATTEMPTS" ]; then
        log_message "Restarting $SERVICE_NAME service (attempt $((restart_count + 1))/$MAX_RESTART_ATTEMPTS)"
        systemctl restart "$SERVICE_NAME"
        increment_restart_count
        
        # Wait and verify restart
        sleep 15
        if systemctl is-active --quiet "$SERVICE_NAME"; then
            log_message "Service restarted successfully"
            
            # Test stream again after restart
            sleep 10
            if command -v ffprobe >/dev/null 2>&1; then
                timeout 10 ffprobe -v quiet "$RTSP_URL" >/dev/null 2>&1
                if [ $? -eq 0 ]; then
                    log_message "Stream verified working after restart"
                    reset_restart_count
                else
                    log_message "Stream still not working after restart"
                fi
            fi
        else
            log_message "Failed to restart service"
        fi
    else
        log_message "CRITICAL: Maximum restart attempts reached. Service requires manual intervention."
        log_message "Check system logs: journalctl -u $SERVICE_NAME -n 50"
    fi
else
    log_message "Stream monitoring complete - no action needed"
fi

# Cleanup old log entries (keep last 1000 lines)
if [ -f "$LOG_FILE" ]; then
    tail -n 1000 "$LOG_FILE" > "${LOG_FILE}.tmp" && mv "${LOG_FILE}.tmp" "$LOG_FILE"
fi
