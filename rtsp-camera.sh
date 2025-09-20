#!/bin/bash
rpicam-vid --framerate 15 --width 720 --height 480 -n -t 0 --inline -o - | \
cvlc stream:///dev/stdin --sout '#rtp{sdp=rtsp://:8554/stream1}' :demux=h264  --no-audio --no-video-title-show --no-stats