"""Render cameras.yml into a MediaMTX config.

Runs as a one-shot container before MediaMTX starts. Keys are those of
MediaMTX 1.19.x -- note `recordDeleteAfter`, which older docs call
`recordDeletePeriod`.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

import yaml

from . import config as cfg

RECORD_ROOT = "/recordings"


def build(conf: cfg.Config) -> dict[str, Any]:
    paths: dict[str, Any] = {}
    for cam in conf.cameras:
        paths[cam.name] = {
            "source": cam.url,
            "record": cam.record,
            "recordPath": f"{RECORD_ROOT}/%path/%Y-%m-%d_%H-%M-%S-%f",
            "recordFormat": "fmp4",
            "recordSegmentDuration": cfg.go_duration(conf.segment_duration),
            "recordDeleteAfter": cfg.go_duration(conf.continuous_retention),
        }
        if cam.sub_url:
            # Detection-only stream: pulled, never written to disk.
            paths[cam.detect_path] = {"source": cam.sub_url, "record": False}

    return {
        "logLevel": "info",
        "logDestinations": ["stdout"],
        # Served only on the internal compose network; the web container is the
        # single published entry point.
        "api": True,
        "apiAddress": ":9997",
        "playback": True,
        "playbackAddress": ":9996",
        "rtsp": True,
        "rtspAddress": ":8554",
        # TCP only: drops the UDP RTP/RTCP listeners we have no use for.
        "rtspTransports": ["tcp"],
        "hls": True,
        "hlsAddress": ":8888",
        # Everything else off -- fewer listeners, less to go wrong.
        "rtmp": False,
        "webrtc": False,
        "srt": False,
        "moq": False,
        "pathDefaults": {
            "sourceOnDemand": False,
            # The Pi enforces TCP-only RTSP; matching it here avoids a
            # UDP attempt and fallback on every reconnect.
            "rtspTransport": "tcp",
            "record": False,
        },
        "paths": paths,
    }


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    src = argv[0] if argv else os.environ.get("NVR_CONFIG", "/config/cameras.yml")
    dst = argv[1] if len(argv) > 1 else os.environ.get("MTX_CONFIG", "/mtxconfig/mediamtx.yml")

    try:
        conf = cfg.load(src)
    except (cfg.ConfigError, OSError) as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 1

    out = Path(dst)
    out.parent.mkdir(parents=True, exist_ok=True)
    # Contains expanded camera passwords.
    out.write_text(yaml.safe_dump(build(conf), sort_keys=False))
    out.chmod(0o600)

    for cam in conf.cameras:
        Path(RECORD_ROOT, cam.name).mkdir(parents=True, exist_ok=True)

    names = ", ".join(cam.name for cam in conf.cameras)
    print(f"wrote {out} for {len(conf.cameras)} camera(s): {names}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
