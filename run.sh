#!/bin/bash

# RaspRec Setup Script
# Sets up a stable, SECURE RTSP/WebRTC camera stream on a Raspberry Pi using
# MediaMTX (native rpiCamera source) + Tailscale (WireGuard VPN, no open ports).
#
# This replaces the old cvlc pipeline, which leaked memory and dropped the
# stream after hours/days.

set -e

echo "=== RaspRec Setup ==="
echo "Secure RTSP/WebRTC camera streaming for Raspberry Pi (MediaMTX + Tailscale)"
echo ""

# Check if this is an update or fresh install
if [ -f "/etc/systemd/system/mediamtx.service" ]; then
    echo "🔄 Existing installation detected - performing update/reinstall"
    echo "   Previous configurations will be backed up with timestamp"
else
    echo "🆕 Fresh installation detected"
fi
echo ""

# ---------------------------------------------------------------------------
# 1. Detect Raspberry Pi model + performance tier
# ---------------------------------------------------------------------------
if ! command -v vcgencmd &> /dev/null; then
    echo "WARNING: This doesn't appear to be a Raspberry Pi system (no vcgencmd)"
    PI_MODEL="unknown"
    PI_PERFORMANCE="medium"
else
    PI_MODEL=$(cat /proc/device-tree/model 2>/dev/null | tr -d '\0' | grep -o "Raspberry Pi [0-9A-Za-z ]*" | head -1)
    [ -z "$PI_MODEL" ] && PI_MODEL="Raspberry Pi (unknown model)"
    echo "✓ Detected: $PI_MODEL"

    if echo "$PI_MODEL" | grep -qi "Pi 4\|Pi 5"; then
        PI_PERFORMANCE="maximum"; echo "  Optimizing for Pi 4/5 (maximum performance)"
    elif echo "$PI_MODEL" | grep -qi "Pi 3"; then
        PI_PERFORMANCE="high"; echo "  Optimizing for Pi 3 (high performance)"
    elif echo "$PI_MODEL" | grep -qi "Pi Zero 2"; then
        PI_PERFORMANCE="medium"; echo "  Optimizing for Pi Zero 2 W (balanced)"
    elif echo "$PI_MODEL" | grep -qi "Pi Zero"; then
        PI_PERFORMANCE="low"; echo "  Optimizing for Pi Zero (limited)"
    else
        PI_PERFORMANCE="medium"; echo "  Using default (medium) performance settings"
    fi
fi

# ---------------------------------------------------------------------------
# 2. Check camera availability + detect model
# ---------------------------------------------------------------------------
echo "Checking camera availability..."
CAMERA_TYPE="unknown"
CAM_TOOL=""
command -v rpicam-still &> /dev/null && CAM_TOOL="rpicam-still"
[ -z "$CAM_TOOL" ] && command -v libcamera-still &> /dev/null && CAM_TOOL="libcamera-still"

if [ -n "$CAM_TOOL" ]; then
    if $CAM_TOOL --list-cameras 2>/dev/null | grep -q "Available cameras"; then
        echo "✓ Camera detected via $CAM_TOOL"
        CAMERA_INFO=$($CAM_TOOL --list-cameras 2>/dev/null)
        if echo "$CAMERA_INFO" | grep -qi "imx219"; then CAMERA_TYPE="v2"; echo "  Camera v2 (IMX219)"
        elif echo "$CAMERA_INFO" | grep -qi "ov5647"; then CAMERA_TYPE="v1"; echo "  Camera v1 (OV5647)"
        elif echo "$CAMERA_INFO" | grep -qi "imx477"; then CAMERA_TYPE="hq"; echo "  HQ Camera (IMX477)"
        else echo "  Camera detected (model unknown)"; fi
    else
        echo "WARNING: No cameras detected via $CAM_TOOL"
        echo "  Check: 1) camera cable  2) camera_auto_detect=1 in config.txt  3) reboot"
        read -p "Continue anyway? (y/N): " -n 1 -r; echo
        [[ ! $REPLY =~ ^[Yy]$ ]] && exit 1
    fi
else
    echo "WARNING: Neither rpicam-still nor libcamera-still found."
    echo "Modern Raspberry Pi OS (Bookworm+) ships rpicam-apps by default."
    read -p "Continue anyway? (y/N): " -n 1 -r; echo
    [[ ! $REPLY =~ ^[Yy]$ ]] && exit 1
fi

# ---------------------------------------------------------------------------
# 3. Check GPU memory (native rpiCamera still needs GPU for the ISP/encoder)
# ---------------------------------------------------------------------------
if command -v vcgencmd &> /dev/null; then
    GPU_MEM=$(vcgencmd get_mem gpu | cut -d'=' -f2 | cut -d'M' -f1)
    if [ "$GPU_MEM" -lt 128 ]; then
        echo "WARNING: GPU memory is ${GPU_MEM}M (recommend 128M+)."
        read -p "Run GPU memory setup script now? (y/N): " -n 1 -r; echo
        if [[ $REPLY =~ ^[Yy]$ ]] && [ -f "setup-gpu-memory.sh" ]; then
            chmod +x setup-gpu-memory.sh && ./setup-gpu-memory.sh
        fi
    else
        echo "✓ GPU memory: ${GPU_MEM}M"
    fi
fi

# ---------------------------------------------------------------------------
# 4. Choose streaming user
# ---------------------------------------------------------------------------
if [ -z "$CAMERA_USER" ]; then
    ACTUAL_USER="${SUDO_USER:-$USER}"
    echo ""
    echo "Camera streaming requires a user account (must be in the 'video' group)."
    echo "1. Use current user ($ACTUAL_USER)"
    echo "2. Create dedicated camera user"
    read -p "Choose (1/2) or enter custom username: " USER_CHOICE
    case "$USER_CHOICE" in
        1) CAMERA_USER="$ACTUAL_USER" ;;
        2) read -p "Enter username for dedicated camera user: " CAMERA_USER
           [ -z "$CAMERA_USER" ] && { echo "ERROR: Username required"; exit 1; } ;;
        "") echo "ERROR: Choose an option or enter a username"; exit 1 ;;
        *) CAMERA_USER="$USER_CHOICE" ;;
    esac
fi
echo "Streaming user: $CAMERA_USER"

if ! id "$CAMERA_USER" &>/dev/null; then
    echo "Creating user '$CAMERA_USER'..."
    sudo useradd -m -s /bin/bash "$CAMERA_USER"
fi
if getent group video > /dev/null 2>&1; then
    sudo usermod -a -G video "$CAMERA_USER"
    echo "✓ '$CAMERA_USER' is in the video group"
fi

# ---------------------------------------------------------------------------
# 5. Stream credentials (defense-in-depth on top of Tailscale)
# ---------------------------------------------------------------------------
echo ""
echo "Set a username/password required to VIEW the stream."
echo "(This is a second layer behind the Tailscale VPN.)"
read -p "  Stream username [view]: " STREAM_USER
STREAM_USER=${STREAM_USER:-view}
while true; do
    read -s -p "  Stream password: " STREAM_PASS; echo
    [ -n "$STREAM_PASS" ] && break
    echo "  Password cannot be empty."
done

# ---------------------------------------------------------------------------
# 6. Pick resolution / bitrate / fps for the detected hardware
# ---------------------------------------------------------------------------
case "$PI_PERFORMANCE" in
    maximum) WIDTH=1920; HEIGHT=1080; BITRATE=3000000; FRAMERATE=30 ;;
    high)    if [ "$CAMERA_TYPE" = "v2" ] || [ "$CAMERA_TYPE" = "hq" ]; then
                 WIDTH=1920; HEIGHT=1080; BITRATE=3000000; FRAMERATE=24
             else WIDTH=1280; HEIGHT=720; BITRATE=2000000; FRAMERATE=24; fi ;;
    medium)  WIDTH=1280; HEIGHT=720;  BITRATE=2000000; FRAMERATE=24 ;;
    low)     WIDTH=720;  HEIGHT=480;  BITRATE=1000000; FRAMERATE=15 ;;
    *)       WIDTH=1280; HEIGHT=720;  BITRATE=2000000; FRAMERATE=24 ;;
esac
echo ""
echo "Stream settings: ${WIDTH}x${HEIGHT} @ ${FRAMERATE}fps, ${BITRATE} bps"

# ---------------------------------------------------------------------------
# 7. Install MediaMTX (ARM binary) if missing
# ---------------------------------------------------------------------------
echo ""
echo "Installing dependencies..."
sudo apt-get update
sudo apt-get install -y curl tar ffmpeg   # ffmpeg gives ffprobe for verification

if command -v mediamtx &> /dev/null || [ -x /usr/local/bin/mediamtx ]; then
    echo "✓ MediaMTX already installed ($(/usr/local/bin/mediamtx --version 2>/dev/null | head -1))"
else
    echo "Downloading MediaMTX for this architecture..."
    # Map uname -m to MediaMTX release arch suffix
    case "$(uname -m)" in
        aarch64|arm64) MTX_ARCH="linux_arm64v8" ;;
        armv7l)        MTX_ARCH="linux_armv7" ;;
        armv6l)        MTX_ARCH="linux_armv6" ;;
        x86_64|amd64)  MTX_ARCH="linux_amd64" ;;
        *) echo "ERROR: unsupported architecture $(uname -m)"; exit 1 ;;
    esac
    # Resolve the latest release tag from GitHub
    MTX_TAG=$(curl -fsSL https://api.github.com/repos/bluenviron/mediamtx/releases/latest \
        | grep -o '"tag_name": *"[^"]*"' | head -1 | cut -d'"' -f4)
    [ -z "$MTX_TAG" ] && { echo "ERROR: could not resolve MediaMTX release tag"; exit 1; }
    MTX_URL="https://github.com/bluenviron/mediamtx/releases/download/${MTX_TAG}/mediamtx_${MTX_TAG}_${MTX_ARCH}.tar.gz"
    echo "  ${MTX_TAG} (${MTX_ARCH})"
    TMP_DIR=$(mktemp -d)
    curl -fsSL "$MTX_URL" -o "$TMP_DIR/mediamtx.tar.gz"
    tar -xzf "$TMP_DIR/mediamtx.tar.gz" -C "$TMP_DIR"
    sudo install -m 0755 "$TMP_DIR/mediamtx" /usr/local/bin/mediamtx
    rm -rf "$TMP_DIR"
    echo "✓ MediaMTX installed to /usr/local/bin/mediamtx"
fi

# ---------------------------------------------------------------------------
# 8. Write MediaMTX config from template (inject settings + credentials)
# ---------------------------------------------------------------------------
echo "Writing MediaMTX config..."
sudo mkdir -p /usr/local/etc
if [ -f /usr/local/etc/mediamtx.yml ]; then
    sudo cp /usr/local/etc/mediamtx.yml /usr/local/etc/mediamtx.yml.backup.$(date +%Y%m%d_%H%M%S)
fi

# Escape sed-sensitive characters in the password
ESC_PASS=$(printf '%s' "$STREAM_PASS" | sed -e 's/[\/&]/\\&/g')

sed -e "s/CAMERA_STREAM_USER_PLACEHOLDER/${STREAM_USER}/" \
    -e "s/CAMERA_STREAM_PASS_PLACEHOLDER/${ESC_PASS}/" \
    -e "s/RPI_WIDTH_PLACEHOLDER/${WIDTH}/" \
    -e "s/RPI_HEIGHT_PLACEHOLDER/${HEIGHT}/" \
    -e "s/RPI_FPS_PLACEHOLDER/${FRAMERATE}/" \
    -e "s/RPI_BITRATE_PLACEHOLDER/${BITRATE}/" \
    mediamtx.yml | sudo tee /usr/local/etc/mediamtx.yml > /dev/null
# Config holds a password — restrict readability to root + the streaming user.
sudo chown root:video /usr/local/etc/mediamtx.yml
sudo chmod 640 /usr/local/etc/mediamtx.yml
echo "✓ Config written to /usr/local/etc/mediamtx.yml (mode 640)"

# ---------------------------------------------------------------------------
# 9. Install the systemd service
# ---------------------------------------------------------------------------
echo "Installing systemd service..."
systemctl is-active --quiet mediamtx && sudo systemctl stop mediamtx
if [ -f /etc/systemd/system/mediamtx.service ]; then
    sudo cp /etc/systemd/system/mediamtx.service /etc/systemd/system/mediamtx.service.backup.$(date +%Y%m%d_%H%M%S)
fi
sed "s/CAMERA_USER_PLACEHOLDER/${CAMERA_USER}/" mediamtx.service \
    | sudo tee /etc/systemd/system/mediamtx.service > /dev/null

# Retire the old cvlc-based service if it exists
if [ -f /etc/systemd/system/rtsp-camera.service ]; then
    echo "Disabling legacy rtsp-camera (cvlc) service..."
    sudo systemctl disable --now rtsp-camera 2>/dev/null || true
    sudo mv /etc/systemd/system/rtsp-camera.service \
        /etc/systemd/system/rtsp-camera.service.retired.$(date +%Y%m%d_%H%M%S)
    # Remove the old cron monitor (MediaMTX + Restart=always replaces it)
    (sudo crontab -l 2>/dev/null | grep -v "camera-monitor.sh") | sudo crontab - 2>/dev/null || true
fi

sudo systemctl daemon-reload
sudo systemctl enable mediamtx
sudo systemctl start mediamtx
sleep 5

if ! systemctl is-active --quiet mediamtx; then
    echo "✗ MediaMTX failed to start. Logs:"
    sudo journalctl -u mediamtx -n 30 --no-pager
    exit 1
fi
echo "✓ MediaMTX service running"

# ---------------------------------------------------------------------------
# 10. Secure remote access via Tailscale
# ---------------------------------------------------------------------------
echo ""
echo "=== Secure remote access (Tailscale) ==="
echo "Recommended: reach the camera from your phone with NO open router ports."
read -p "Set up Tailscale now? (Y/n): " -n 1 -r; echo
if [[ ! $REPLY =~ ^[Nn]$ ]]; then
    chmod +x setup-tailscale.sh
    ./setup-tailscale.sh
else
    echo "Skipped. Run ./setup-tailscale.sh later to enable secure remote access."
fi

# ---------------------------------------------------------------------------
# 11. Optional hardening (watchdog + SD-card wear reduction)
# ---------------------------------------------------------------------------
echo ""
read -p "Apply reliability hardening (hardware watchdog + log2ram)? (Y/n): " -n 1 -r; echo
if [[ ! $REPLY =~ ^[Nn]$ ]]; then
    chmod +x setup-hardening.sh
    ./setup-hardening.sh
else
    echo "Skipped. Run ./setup-hardening.sh later (recommended for remote Pis)."
fi

# ---------------------------------------------------------------------------
# Done
# ---------------------------------------------------------------------------
LAN_IP=$(hostname -I | awk '{print $1}')
echo ""
echo "=== Setup Complete ==="
echo "Stream path: /cam   (user: ${STREAM_USER})"
echo ""
echo "On the LAN you can test now:"
echo "  RTSP:   rtsp://${STREAM_USER}:<password>@${LAN_IP}:8554/cam"
echo "  WebRTC: http://${LAN_IP}:8889/cam"
echo ""
echo "From your phone anywhere (over Tailscale) — see the URL printed by"
echo "setup-tailscale.sh, e.g. rtsp://<tailscale-name>:8554/cam"
echo ""
echo "Manage:  sudo systemctl status mediamtx"
echo "Logs:    sudo journalctl -u mediamtx -f"
echo "Diag:    ./diagnose.sh"
echo ""
echo "SECURITY: never forward ports 8554/8889 on the router. Tailscale makes it"
echo "unnecessary and keeps the camera invisible to the public internet."
