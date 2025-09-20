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

1. **Clone or download this repository** to your Raspberry Pi:
   ```bash
   git clone <repository-url>
   cd rasprec
   ```

2. **Run the setup script** (requires sudo privileges):
   ```bash
   chmod +x run.sh
   sudo ./run.sh
   ```

   This script will:
   - Install VLC dependencies
   - Create the streaming script
   - Set up systemd service
   - Start the RTSP stream automatically

3. **Verify the service is running**:
   ```bash
   sudo systemctl status rtsp-camera
   ```

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
- **Resolution**: 720x480
- **Framerate**: 15 FPS
- **Format**: H.264

To modify these settings, edit the `rtsp-camera.sh` file:
```bash
sudo nano /home/hansolo/rtsp-camera.sh
```

### Service Configuration

The systemd service is configured with:
- **Memory limit**: 256MB
- **Auto-restart**: On failure
- **Restart delay**: 5 seconds
- **User**: hansolo

## File Structure

```
rasprec/
├── README.md                    # This documentation
├── run.sh                       # Setup and installation script
├── rtsp-camera.sh               # Main streaming script
├── rtsp-camera.service          # Systemd service configuration
├── rtsp-camera-stable.sh        # Improved streaming script for long-term stability
├── rtsp-camera-stable.service   # Enhanced service config with stability features
├── camera-monitor.sh            # Stream monitoring and auto-restart script
├── diagnose.sh                  # System diagnostic tool
├── test.sh                      # Test script (utility)
└── package.json                 # Project metadata
```

## Long-Term Stability Setup

For production use where the camera needs to run continuously for days/weeks, use the enhanced stability configuration:

### 1. Install Stable Configuration

```bash
# Copy stable scripts
sudo cp rtsp-camera-stable.sh /home/hansolo/
sudo cp rtsp-camera-stable.service /etc/systemd/system/
sudo chmod +x /home/hansolo/rtsp-camera-stable.sh

# Install monitoring script
sudo cp camera-monitor.sh /home/hansolo/
sudo chmod +x /home/hansolo/camera-monitor.sh

# Enable stable service
sudo systemctl daemon-reload
sudo systemctl disable rtsp-camera  # Disable basic version
sudo systemctl enable rtsp-camera-stable
sudo systemctl start rtsp-camera-stable
```

### 2. Setup Automatic Monitoring

```bash
# Add monitoring to crontab
sudo crontab -e
# Add this line:
*/5 * * * * /home/hansolo/camera-monitor.sh
```

### 3. Stability Features

The stable configuration includes:
- **Memory leak prevention** with resource limits
- **Automatic restart** on failures with backoff
- **Enhanced error handling** and logging
- **Thermal monitoring** and throttling detection
- **Stream health checks** every 5 minutes
- **Improved VLC parameters** for stability
- **Signal handling** for clean shutdowns

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

**Enable camera interface:**
```bash
sudo raspi-config
# Navigate to: Interface Options → Camera → Enable
sudo reboot
```

**Test camera manually:**
```bash
rpicam-vid --timeout 5000 --output test.h264
# Or for older systems:
raspivid -t 5000 -o test.h264
```

**Check camera connection:**
```bash
vcgencmd get_camera
# Should return: supported=1 detected=1
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
