# RaspRec - Raspberry Pi Camera Streaming

A lightweight camera streaming solution for Raspberry Pi that captures video
from the camera module and streams it over the network — with **secure remote
access over Tailscale** and no open router ports.

Streaming is handled by [MediaMTX](https://github.com/bluenviron/mediamtx)
using its native `rpiCamera` (libcamera) source. This replaced the old
`cvlc`/`rpicam-vid` pipeline, which leaked memory and dropped the stream after
hours or days.

## Features

- **Real-time RTSP (`:8554`) + WebRTC (`:8889`) streaming** via MediaMTX
- **Secure remote access** over Tailscale (WireGuard VPN) — no port forwarding,
  invisible to the public internet
- **Stream authentication** (username/password) as defense in depth
- **Automatic startup + self-restart** using systemd (`Restart=always`)
- **Low resource usage** — MediaMTX is a single zero-dependency binary
- **Optional hardening**: hardware watchdog + log2ram for unattended 24/7 use

> **Security:** never forward ports 8554/8889 on your router. Tailscale makes it
> unnecessary and keeps the camera private. See
> [Secure Remote Access](#secure-remote-access-tailscale).

## Hardware Requirements

### Supported Raspberry Pi Models
- **Raspberry Pi Zero 2 W** (720p streaming)
- **Raspberry Pi 3 Model B/B+** (1080p streaming) 
- **Raspberry Pi 4 Model B** (1080p streaming, 30fps)
- **Raspberry Pi 5** (1080p streaming, 30fps)
- **Raspberry Pi Zero** (480p streaming, limited performance)

### Supported Camera Modules
- **Camera Module v1** (OV5647) - 5MP, up to 1080p
- **Camera Module v2** (IMX219) - 8MP, up to 1080p, better low light
- **HQ Camera** (IMX477) - 12MP, up to 4K (limited by Pi performance)

### Other Requirements
- MicroSD card (8GB+ recommended, 16GB+ for Pi 4/5)
- Stable power supply (2.5A for Pi 3, 3A for Pi 4/5)

## Software Requirements

- Raspberry Pi OS (Bookworm or newer recommended)
- libcamera / rpicam-apps (included in modern Raspberry Pi OS)
- MediaMTX (installed automatically by `run.sh`)
- Tailscale (installed by `setup-tailscale.sh`, for secure remote access)

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
- **Auto-detect** your Raspberry Pi model and camera type
- **Optimize** streaming parameters for your hardware capabilities
- Install MediaMTX and ffmpeg
- Prompt for a streaming user and a stream viewing password
- Write the MediaMTX config to `/usr/local/etc/mediamtx.yml`
- Install the `mediamtx` systemd service (auto-restart on failure)
- Offer to set up **Tailscale** for secure remote access (recommended)
- Offer to apply **reliability hardening** (watchdog + log2ram)
- Start the stream automatically

### Verify Installation

```bash
sudo systemctl status mediamtx
./diagnose.sh
```

## Usage

### Accessing the Stream

The stream path is `/cam` and requires the username/password you set during setup.

**On the local network:**
```
rtsp://[USER]:[PASS]@[PI_IP_ADDRESS]:8554/cam     # RTSP (VLC app, ffplay)
http://[PI_IP_ADDRESS]:8889/cam                   # WebRTC (any browser)
```

**Remotely from your phone (recommended, over Tailscale):** use the MagicDNS
name printed by `setup-tailscale.sh`, e.g.
```
rtsp://moms-camera:8554/cam      # in the VLC mobile app
http://moms-camera:8889/cam      # in a phone browser (WebRTC, no app)
```

### Viewing the Stream

**VLC (desktop or mobile app):** Media → Open Network Stream → paste the RTSP URL.
VLC will prompt for the username/password.

**Browser (WebRTC):** open the `http://…:8889/cam` URL — nothing to install.

**FFplay:**
```bash
ffplay "rtsp://[USER]:[PASS]@[PI_IP_ADDRESS]:8554/cam"
```

### Service Management

**Start the service:**
```bash
sudo systemctl start mediamtx
```

**Stop the service:**
```bash
sudo systemctl stop mediamtx
```

**Enable auto-start on boot:**
```bash
sudo systemctl enable mediamtx
```

**View service logs:**
```bash
sudo journalctl -u mediamtx -f
```

## Configuration

### Video Settings

The setup script automatically optimizes video settings based on your hardware:

#### Automatic Optimization Table
| Pi Model | Camera | Resolution | Bitrate | FPS | Notes |
|----------|--------|------------|---------|-----|-------|
| **Pi 4/5** | Any | 1080p | 3-4 Mbps | 30 | Maximum performance |
| **Pi 3** | v2/HQ | 1080p | 3 Mbps | 24 | High quality streaming |
| **Pi 3** | v1 | 720p | 2 Mbps | 24 | Balanced performance |
| **Pi Zero 2W** | Any | 720p | 2 Mbps | 24 | Optimized for hardware |
| **Pi Zero** | Any | 480p | 1 Mbps | 15 | Limited performance |

#### Manual Configuration
To modify these settings, edit the MediaMTX config and restart the service:
```bash
sudo nano /usr/local/etc/mediamtx.yml     # edit rpiCameraWidth/Height/FPS/Bitrate
sudo systemctl restart mediamtx
```

**Note**: The setup script detects your hardware and applies optimal settings automatically. Manual changes may affect performance and stability.

### GPU Memory Configuration

For optimal camera streaming performance, ensure adequate GPU memory allocation:

**Automatic Configuration (Recommended):**
```bash
./setup-gpu-memory.sh
```

**Manual Configuration:**
```bash
# Edit config file
sudo nano /boot/firmware/config.txt  # (or /boot/config.txt on older systems)
# Add or modify:
gpu_mem=128
# Reboot to apply
sudo reboot
```

**Check Current Setting:**
```bash
vcgencmd get_mem gpu
```

### Service Configuration

The `mediamtx` systemd service is configured with:
- **Memory limit**: 200MB with accounting
- **Auto-restart**: `Restart=always`, 5s delay, with start-burst limits
- **Security hardening**: `NoNewPrivileges`, `ProtectSystem=strict`, `ProtectHome`
- **User**: streaming user (in the `video` group)
- **Binary**: `/usr/local/bin/mediamtx`, config `/usr/local/etc/mediamtx.yml`

## File Structure

```
rasprec/
├── README.md                    # This documentation
├── install.sh                   # Quick installation / update bootstrap
├── run.sh                       # Main setup script (MediaMTX + Tailscale)
├── mediamtx.yml                 # MediaMTX config template (rpiCamera source)
├── mediamtx.service             # systemd unit for MediaMTX
├── setup-tailscale.sh           # Secure remote access (Tailscale VPN)
├── setup-hardening.sh           # Watchdog + log2ram for unattended use
├── setup-gpu-memory.sh          # GPU memory configuration helper
├── diagnose.sh                  # System diagnostic tool
├── package.json                 # Project metadata
├── rtsp-camera.sh               # DEPRECATED (old cvlc streamer)
├── rtsp-camera.service          # DEPRECATED (old cvlc service)
└── camera-monitor.sh            # DEPRECATED (old cron watchdog)
```

### Installed System Files

After running the setup:

```
/usr/local/bin/mediamtx           # MediaMTX binary
/usr/local/etc/mediamtx.yml       # MediaMTX config (mode 640, holds stream password)
/etc/systemd/system/mediamtx.service
```

## Stability Features

- **Purpose-built media server** — MediaMTX's native `rpiCamera` source replaces
  the leaky `cvlc` pipeline that dropped the stream after hours/days
- **Automatic restart** on failure via systemd (`Restart=always`)
- **RTSP over TCP** to avoid H.264 decode errors from UDP packet loss
- **Memory limit** (200MB) with accounting
- **Optional hardware watchdog** — auto-reboots on a hard hang (`setup-hardening.sh`)
- **Optional log2ram** — reduces SD-card wear for 24/7 operation
- **GPU memory optimization** with automatic checks

## Secure Remote Access (Tailscale)

To reach the camera over the internet **securely**, RaspRec uses
[Tailscale](https://tailscale.com) — a WireGuard-based mesh VPN.

> ⚠️ **Do NOT port-forward the RTSP/WebRTC ports on the router.** RTSP has no
> transport encryption, and an exposed camera port is found by internet scanners
> (e.g. Shodan) within hours. Tailscale removes the need entirely: it opens **no
> inbound router ports**, encrypts all traffic, and works behind CGNAT.

### How it works

```
your phone ──WireGuard (Tailscale)──▶ Pi @ remote house
  VLC app / browser                    MediaMTX  :8554 RTSP / :8889 WebRTC
router: NO forwarded ports — camera invisible to the public internet
```

### Setup

```bash
./setup-tailscale.sh
```

This installs Tailscale, runs `tailscale up --ssh`, and prints a login URL. Sign
in with the **same account** you'll use on your phone, then install the Tailscale
app on your phone and sign in there too. The script prints your viewing URL, e.g.
`rtsp://moms-camera:8554/cam`.

### Recommended lockdown

1. **ACLs** — restrict the tailnet so only *your* devices can reach the Pi:
   <https://login.tailscale.com/admin/acls>
2. **Disable key expiry** for the Pi so it never needs re-auth
   (Machines → the Pi → Disable key expiry).
3. **Tailscale SSH** (enabled by `--ssh`) lets you administer the Pi remotely
   from any tailnet device — again, no open router ports.
4. **MagicDNS** gives the Pi a stable name, so the viewing URL keeps working even
   when the remote ISP changes the public IP.

### Layers of protection

| Layer | What it stops |
|-------|---------------|
| No port forwarding | Public internet can't see the camera at all |
| Tailscale (WireGuard) | Encrypts traffic; only your tailnet can connect |
| Tailscale ACLs | Restrict which tailnet devices reach the Pi |
| MediaMTX auth | Password required even for a LAN/tailnet device |

## Unattended Reliability Hardening

For a Pi running 24/7 at a remote location (where you can't easily reboot it):

```bash
./setup-hardening.sh
```

- **Hardware watchdog** — auto-reboots the Pi if it hard-hangs.
- **log2ram** — keeps logs in RAM to reduce SD-card wear (SD corruption is the
  most common failure mode for always-on Raspberry Pis).

Reboot afterwards to activate the watchdog: `sudo reboot`.

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
sudo systemctl status mediamtx
```

**If service is not running:**
```bash
sudo systemctl start mediamtx
sudo systemctl enable mediamtx
```

**Check service logs for errors:**
```bash
sudo journalctl -u mediamtx -f
```

**Authentication failing?** Confirm the username/password in
`/usr/local/etc/mediamtx.yml` (`authInternalUsers`) match what your player sends.

#### Camera Stream Stops After Hours/Days

With MediaMTX this should no longer happen — it was a symptom of the old `cvlc`
memory leak. If a stream still drops:

```bash
# Confirm the service restarted itself and inspect why it exited
sudo journalctl -u mediamtx -n 100 --no-pager

# Check for thermal/voltage throttling (0x0 = OK)
vcgencmd get_throttled

# Ensure GPU memory is adequate for the ISP/encoder
vcgencmd get_mem gpu   # want 128M+
```

For an unattended Pi, run `./setup-hardening.sh` to add a hardware watchdog that
reboots the Pi on a hard hang.

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

#### MediaMTX/Streaming Issues

**Reinstall MediaMTX:** re-run `./run.sh` (it re-downloads the binary and rewrites config).

**Test MediaMTX manually (see live errors):**
```bash
sudo systemctl stop mediamtx
sudo -u <streaming-user> /usr/local/bin/mediamtx /usr/local/etc/mediamtx.yml
```

**Check if ports 8554/8889 are in use:**
```bash
sudo ss -tlnp | grep -E '8554|8889'
```

#### Memory/Performance Issues

**Check system resources:**
```bash
htop
free -h
```

**Reduce video quality in the MediaMTX config:**
```bash
sudo nano /usr/local/etc/mediamtx.yml
# Lower rpiCameraWidth/rpiCameraHeight/rpiCameraFPS/rpiCameraBitrate, then:
sudo systemctl restart mediamtx
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

#### Camera User Not Found

**The setup script will automatically create the camera user when you run it.**
If you need to create manually:
```bash
sudo useradd -m -s /bin/bash USERNAME
sudo usermod -a -G video USERNAME
sudo passwd USERNAME
```

**Or modify service to use existing user:**
```bash
sudo nano /etc/systemd/system/mediamtx.service
# Change User=USERNAME to your preferred user (must be in the video group)
sudo systemctl daemon-reload && sudo systemctl restart mediamtx
```

#### Resolution/Configuration Mismatch

**Fix resolution:**
```bash
sudo nano /usr/local/etc/mediamtx.yml
# Adjust rpiCameraWidth / rpiCameraHeight, then:
sudo systemctl restart mediamtx
```

#### Binary/Config Path Issues

**Verify MediaMTX binary and config:**
```bash
ls -la /usr/local/bin/mediamtx /usr/local/etc/mediamtx.yml
/usr/local/bin/mediamtx --version
```

### Network Diagnostics

#### Test RTSP Stream Locally

**From the Pi itself (uses the stream credentials):**
```bash
ffplay "rtsp://USER:PASS@localhost:8554/cam"
# Or
vlc "rtsp://USER:PASS@localhost:8554/cam"
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
ffplay "rtsp://USER:PASS@[PI_IP]:8554/cam"
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
sudo journalctl -u mediamtx --since "1 hour ago"
```

### Quick Fixes Checklist

1. ✅ Camera detected (`rpicam-still --list-cameras`)
2. ✅ MediaMTX service running (`systemctl status mediamtx`)
3. ✅ Network connectivity working
4. ✅ Stream credentials correct in `mediamtx.yml`
5. ✅ Tailscale up (`tailscale status`) for remote access
6. ✅ Ports 8554/8889 NOT forwarded on the router
7. ✅ Sufficient power supply (2.5A+)
8. ✅ SD card not corrupted
9. ✅ Streaming user in the `video` group
10. ✅ GPU memory 128M+

### Getting Help

If issues persist:
1. Run `./diagnose.sh` for a full status report
2. Check the service logs: `sudo journalctl -u mediamtx -f`
3. Test components individually (camera, MediaMTX, Tailscale, network)
4. Verify hardware connections and power supply

## License

ISC License - See package.json for details.
