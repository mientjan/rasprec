#!/bin/bash

echo "Creating rtsp-camera.sh"
cat <<EOF > rtsp-camera.sh
#!/bin/bash
rpicam-vid --framerate 15 --width 720 --height 720 -n -t 0 --inline -o - | \
cvlc stream:///dev/stdin --sout '#rtp{sdp=rtsp://:8554/stream1}' :demux=h264,cache=500  --no-audio --no-video-title-show --no-stats
EOF
chmod +x rtsp-camera.sh

echo "Installing dependencies"
sudo apt-get update
sudo apt-get install -y vlc

echo "Creating systemd service"
cat <<EOF > rtsp-camera.service
[Unit]
Description=Raspberry Pi RTSP Stream
After=network.target

[Service]
ExecStart=/home/hansolo/rtsp-camera.sh
Restart=always
RestartSec=5
User=hansolo
SyslogIdentifier=rtsp-stream
MemoryMax=256M
MemoryAccounting=true
[Install]
WantedBy=multi-user.target
EOF
mv rtsp-camera.service /etc/systemd/system/rtsp-camera.service

echo "Starting systemd service"
sudo systemctl daemon-reload
sudo systemctl enable rtsp-camera
sudo systemctl start rtsp-camera
sudo systemctl status rtsp-camera

echo "Done"
