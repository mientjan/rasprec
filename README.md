# RaspRec - Raspberry Pi RTSP Camera Streaming

A lightweight RTSP camera streaming solution for Raspberry Pi Zero 2 W that captures video from the camera module and streams it over the network.

## Features

- **Real-time RTSP streaming** via VLC on port 8554
- **Automatic startup** using systemd service
- **Low resource usage** optimized for Raspberry Pi Zero 2 W
- **Configurable resolution and framerate**
- **Memory-limited service** (256MB max) for stability

## Hardware Requirements

- Raspberry Pi Zero 2 W
- Raspberry Pi Camera Module (v1, v2, or HQ Camera)
- MicroSD card (8GB+ recommended)
- Stable power supply (2.5A recommended)

## Software Requirements

- Raspberry Pi OS (Bullseye or newer)
- VLC media player
- rpicam-vid (included in modern Raspberry Pi OS)

## Installation

### Quick Install (Recommended)

The easiest way to install on your Raspberry Pi:

```bash
# Download and run the installation script
wget https://raw.githubusercontent.com/mientjan/rasprec/main/install.sh
chmod +x install.sh
./install.sh
```

This will automatically:
- Clone the repository to `~/rasprec`
- Install git if not present
- Make all scripts executable
- Optionally run the setup immediately

### Manual Installation

1. **Clone the repository** to your Raspberry Pi:
   ```bash
   git clone https://github.com/mientjan/rasprec.git
   cd rasprec
   ```

2. **Run the setup script**:
   ```bash
   chmod +x run.sh
   ./run.sh
   ```

### What the Setup Does

The setup script will:
- Install all dependencies (VLC, ffmpeg, monitoring tools)
- Copy stable streaming configuration to `/home/hansolo/`
- Install enhanced systemd service with stability features
- Set up automatic monitoring every 5 minutes
- Create user 'hansolo' if needed
- Start the RTSP stream automatically

### Verify Installation

```bash
sudo systemctl status rtsp-camera
```

The setup automatically includes all stability features for long-term operation:
- ✅ Enhanced stability configuration
- ✅ Automatic monitoring every 5 minutes  
- ✅ Memory leak prevention
- ✅ Thermal monitoring and auto-restart
- ✅ Smart restart logic with backoff

## Usage

### Accessing the Stream

Once installed and running, you can access the RTSP stream at:
```
rtsp://[PI_IP_ADDRESS]:8554/stream1
```

### Viewing the Stream

You can view the stream using various RTSP-compatible players:

**VLC Media Player:**
1. Open VLC
2. Go to Media → Open Network Stream
3. Enter the RTSP URL above

**FFplay:**
```bash
ffplay rtsp://[PI_IP_ADDRESS]:8554/stream1
```

**OBS Studio:**
1. Add Source → Media Source
2. Uncheck "Local File"
3. Enter the RTSP URL

### Service Management

**Start the service:**
```bash
sudo systemctl start rtsp-camera
```

**Stop the service:**
```bash
sudo systemctl stop rtsp-camera
```

**Enable auto-start on boot:**
```bash
sudo systemctl enable rtsp-camera
```

**View service logs:**
```bash
sudo journalctl -u rtsp-camera -f
```

## Configuration

### Video Settings

The default configuration streams at:
- **Resolution**: 720x480 (4:3 aspect ratio)
- **Framerate**: 24 FPS
- **Format**: H.264

To modify these settings, edit the `rtsp-camera.sh` file:
```bash
sudo nano /home/hansolo/rtsp-camera.sh
```

### Service Configuration

The systemd service is configured with enhanced stability features:
- **Memory limit**: 256MB with accounting
- **CPU quota**: 80% to prevent system overload
- **Watchdog**: 300 second hang detection
- **Auto-restart**: On failure with intelligent backoff
- **Restart delay**: 15 seconds with burst limits
- **Security hardening**: Protected system and home directories
- **User**: hansolo (video group)

## File Structure

```
rasprec/
├── README.md                    # This documentation
├── install.sh                   # Quick installation script for Raspberry Pi
├── run.sh                       # Main setup script with stability features
├── rtsp-camera.sh               # Enhanced streaming script with stability features
├── rtsp-camera.service          # Enhanced systemd service configuration
├── camera-monitor.sh            # Stream monitoring and auto-restart script
├── diagnose.sh                  # System diagnostic tool
└── package.json                 # Project metadata
```

## Stability Features

The setup script automatically configures the following stability features:

- **Memory leak prevention** with resource limits and watchdog
- **Automatic restart** on failures with intelligent backoff
- **Enhanced error handling** and comprehensive logging  
- **Thermal monitoring** and throttling detection
- **Stream health checks** every 5 minutes via cron
- **Improved VLC parameters** optimized for long-term stability
- **Signal handling** for clean process shutdowns
- **GPU memory optimization** with automatic checks

## Network Configuration

Ensure your Raspberry Pi is connected to your network and note its IP address:
```bash
hostname -I
```

For remote access, you may need to:
1. Configure port forwarding on your router (port 8554)
2. Set up dynamic DNS if using over the internet
3. Consider VPN for secure remote access

## Performance Notes

- The Raspberry Pi Zero 2 W has limited processing power
- Higher resolutions/framerates may cause performance issues
- Monitor CPU and memory usage with `htop`
- Consider lowering quality settings if experiencing drops

## Troubleshooting

### RTSP Stream Issues

#### Stream Not Available / Connection Refused

**Check if the service is running:**
```bash
sudo systemctl status rtsp-camera
```

**If service is not running:**
```bash
sudo systemctl start rtsp-camera
sudo systemctl enable rtsp-camera
```

**Check service logs for errors:**
```bash
sudo journalctl -u rtsp-camera -f
```

#### Camera Stream Stops After Hours/Days (Runtime Stability Issues)

This is a common issue where the camera works initially but fails after extended operation.

**Common causes and solutions:**

**Memory leaks in VLC:**
```bash
# Add memory monitoring and restart to service
sudo nano /etc/systemd/system/rtsp-camera.service
# Add these lines under [Service]:
# WatchdogSec=300
# Restart=always
# RestartSec=10
```

**GPU memory fragmentation:**
```bash
# Increase GPU memory split
sudo raspi-config
# Advanced Options → Memory Split → Set to 256MB
sudo reboot
```

**Camera driver timeout issues:**
```bash
# Add camera timeout parameters to streaming script
sudo nano /home/hansolo/rtsp-camera.sh
# Modify rpicam-vid command:
rpicam-vid --timeout 0 --framerate 15 --width 720 --height 480 -n --flush --inline -o - | \
cvlc stream:///dev/stdin --sout '#rtp{sdp=rtsp://:8554/stream1}' :demux=h264 --intf dummy --no-audio --no-video-title-show --no-stats
```

**Thermal throttling:**
```bash
# Check for throttling events
vcgencmd get_throttled
# 0x0 = no throttling, other values indicate thermal/voltage issues
```

**Add automatic restart mechanism:**
```bash
# Create a monitoring script
sudo nano /home/hansolo/camera-monitor.sh
```

**Create camera monitoring script:**
```bash
#!/bin/bash
# Check if RTSP stream is responding
timeout 10 ffprobe -v quiet -select_streams v:0 -show_entries stream=width,height -of csv=p=0 rtsp://localhost:8554/stream1 > /dev/null 2>&1
if [ $? -ne 0 ]; then
    echo "$(date): RTSP stream not responding, restarting service"
    systemctl restart rtsp-camera
fi
```

**Add to crontab for automatic monitoring:**
```bash
sudo crontab -e
# Add this line to check every 5 minutes:
*/5 * * * * /home/hansolo/camera-monitor.sh >> /var/log/camera-monitor.log 2>&1
```

#### Camera Not Detected (Initial Setup)

**Modern Raspberry Pi OS (Bookworm+):**
Camera should be auto-detected by default. If not working:

```bash
# Check /boot/config.txt has:
grep camera_auto_detect /boot/config.txt
# Should show: camera_auto_detect=1

# If missing, add it:
echo "camera_auto_detect=1" | sudo tee -a /boot/config.txt
sudo reboot
```

**For older/problematic cameras, try manual overlay:**
```bash
# Edit /boot/config.txt:
sudo nano /boot/config.txt
# Change: camera_auto_detect=1
# To: camera_auto_detect=0
#     dtoverlay=imx219  # (or your camera model)
sudo reboot
```

**Legacy Raspberry Pi OS (Bullseye and older):**
```bash
sudo raspi-config
# Navigate to: Interface Options → Camera → Enable
sudo reboot
```

**Test camera manually:**
```bash
# Modern Raspberry Pi OS (Bookworm+):
rpicam-vid --timeout 5000 --output test.h264
# Or for older Bookworm:
libcamera-vid --timeout 5000 --output test.h264
```

**Check camera connection:**
```bash
# Modern detection (recommended):
rpicam-still --list-cameras
# Or for older Bookworm:
libcamera-still --list-cameras

# Legacy method (unreliable on modern systems):
vcgencmd get_camera
# Note: May show "supported=0 detected=0" even when camera works
```

#### VLC/Streaming Issues

**Install/reinstall VLC:**
```bash
sudo apt update
sudo apt install --reinstall vlc-bin vlc-plugin-base
```

**Test VLC manually:**
```bash
cvlc --version
```

**Check if port 8554 is in use:**
```bash
sudo netstat -tlnp | grep 8554
```

#### Memory/Performance Issues

**Check system resources:**
```bash
htop
free -h
```

**Reduce video quality in rtsp-camera.sh:**
```bash
# Lower resolution and framerate
rpicam-vid --framerate 10 --width 640 --height 480 -n -t 0 --inline -o -
```

**Increase GPU memory split:**
```bash
sudo raspi-config
# Advanced Options → Memory Split → Set to 128 or 256
```

### SSH Connection Issues

#### SSH Service Not Running

**Enable SSH:**
```bash
sudo systemctl enable ssh
sudo systemctl start ssh
```

**Or enable via raspi-config:**
```bash
sudo raspi-config
# Interface Options → SSH → Enable
```

**Create SSH file on boot partition (if no access):**
```bash
# On SD card boot partition, create empty file named 'ssh'
touch /boot/ssh
```

#### Network Connectivity Issues

**Check network status:**
```bash
ip addr show
ping google.com
```

**Find Pi's IP address:**
```bash
hostname -I
ip route get 1.1.1.1 | awk '{print $7}'
```

**WiFi configuration (if needed):**
```bash
sudo raspi-config
# System Options → Wireless LAN
```

**Or edit wpa_supplicant:**
```bash
sudo nano /etc/wpa_supplicant/wpa_supplicant.conf
```

#### Authentication Issues

**Reset default user password:**
```bash
sudo passwd pi
# Or for newer systems:
sudo passwd [username]
```

**Check SSH key permissions:**
```bash
chmod 700 ~/.ssh
chmod 600 ~/.ssh/authorized_keys
```

### Code-Specific Issues

#### User 'hansolo' Not Found

**Create the user:**
```bash
sudo useradd -m -s /bin/bash hansolo
sudo passwd hansolo
```

**Or modify service to use current user:**
```bash
sudo nano /etc/systemd/system/rtsp-camera.service
# Change User=hansolo to User=pi (or your username)
# Change ExecStart path accordingly
sudo systemctl daemon-reload
```

#### Resolution/Configuration Mismatch

**Fix resolution inconsistency:**
Edit `/home/hansolo/rtsp-camera.sh` to match desired resolution:
```bash
sudo nano /home/hansolo/rtsp-camera.sh
# Ensure resolution matches your needs (720x480 or 720x720)
```

#### Script Path Issues

**Verify script location and permissions:**
```bash
ls -la /home/hansolo/rtsp-camera.sh
chmod +x /home/hansolo/rtsp-camera.sh
```

### Network Diagnostics

#### Test RTSP Stream Locally

**From the Pi itself:**
```bash
ffplay rtsp://localhost:8554/stream1
# Or
vlc rtsp://localhost:8554/stream1
```

#### Test from Another Device

**Find Pi's IP:**
```bash
hostname -I
```

**Test connection:**
```bash
telnet [PI_IP] 8554
```

**Use VLC or ffplay:**
```bash
ffplay rtsp://[PI_IP]:8554/stream1
```

### Advanced Diagnostics

#### Check System Health

**Temperature:**
```bash
vcgencmd measure_temp
```

**Voltage:**
```bash
vcgencmd measure_volts
```

**Memory:**
```bash
vcgencmd get_mem arm && vcgencmd get_mem gpu
```

#### Log Analysis

**System logs:**
```bash
sudo dmesg | tail -20
sudo journalctl -xe
```

**Service-specific logs:**
```bash
sudo journalctl -u rtsp-camera --since "1 hour ago"
```

### Quick Fixes Checklist

1. ✅ Camera enabled in raspi-config
2. ✅ SSH enabled and running
3. ✅ Network connectivity working
4. ✅ VLC installed and working
5. ✅ Service running without errors
6. ✅ Port 8554 not blocked by firewall
7. ✅ Sufficient power supply (2.5A+)
8. ✅ SD card not corrupted
9. ✅ User permissions correct
10. ✅ Script paths and permissions correct

### Getting Help

If issues persist:
1. Check the service logs: `sudo journalctl -u rtsp-camera -f`
2. Test components individually (camera, VLC, network)
3. Verify hardware connections
4. Consider using a different RTSP streaming solution if VLC continues to have issues

## License

ISC License - See package.json for details.
