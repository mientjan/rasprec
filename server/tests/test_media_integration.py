"""Opt in with RUN_DOCKER_TESTS=1. Uses two synthetic RTSP streams, never real cameras."""

import os
import shutil
import subprocess
import time
import uuid

import pytest
import yaml
from fastapi.testclient import TestClient

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_DOCKER_TESTS") != "1" or not shutil.which("ffmpeg"),
    reason="opt-in Docker + ffmpeg integration",
)


def test_two_camera_capture_pipeline(tmp_path, monkeypatch):
    from nvr import __main__ as main

    def docker(*args):
        return subprocess.check_output(
            ["docker", *args], text=True, stderr=subprocess.DEVNULL
        ).strip()

    recordings = tmp_path / "recordings"
    recordings.mkdir()
    mtx = tmp_path / "mediamtx.yml"
    mtx.write_text(
        yaml.safe_dump(
            {
                "rtspTransports": ["tcp"],
                "playback": True,
                "hls": True,
                "webrtc": False,
                "rtmp": False,
                "paths": {
                    name: {
                        "source": "publisher",
                        "record": True,
                        "recordPath": "/recordings/%path/%Y-%m-%d_%H-%M-%S-%f",
                        "recordFormat": "fmp4",
                        "recordPartDuration": "1s",
                        "recordSegmentDuration": "2s",
                        "recordDeleteAfter": "1h",
                    }
                    for name in ["cam1", "cam2"]
                },
            }
        )
    )
    container = "rasprec-media-fixture-" + uuid.uuid4().hex[:10]
    publishers = []
    try:
        docker(
            "run",
            "-d",
            "--name",
            container,
            "-p",
            "127.0.0.1::8554",
            "-p",
            "127.0.0.1::9996",
            "-v",
            f"{mtx}:/mediamtx.yml:ro",
            "-v",
            f"{recordings}:/recordings",
            "bluenviron/mediamtx:1.19.3",
        )
        port = docker("port", container, "8554/tcp").rsplit(":", 1)[1]
        playback_port = docker("port", container, "9996/tcp").rsplit(":", 1)[1]
        for name in ["cam1", "cam2"]:
            publishers.append(
                subprocess.Popen(
                    [
                        "ffmpeg",
                        "-hide_banner",
                        "-loglevel",
                        "error",
                        "-re",
                        "-f",
                        "lavfi",
                        "-i",
                        "testsrc2=size=640x360:rate=10",
                        "-c:v",
                        "libx264",
                        "-threads",
                        "1",
                        "-preset",
                        "ultrafast",
                        "-g",
                        "10",
                        "-pix_fmt",
                        "yuv420p",
                        "-f",
                        "rtsp",
                        "-rtsp_transport",
                        "tcp",
                        f"rtsp://127.0.0.1:{port}/{name}",
                    ],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            )
        config = tmp_path / "cameras.yml"
        config.write_text(
            yaml.safe_dump(
                {
                    "motion": {
                        "pre_roll": "0s",
                        "post_roll": "0s",
                        "max_duration": "5s",
                        "consecutive": 1,
                        "min_area": 0.01,
                    },
                    "cameras": [
                        {"name": name, "url": f"rtsp://127.0.0.1:{port}/{name}"}
                        for name in ["cam1", "cam2"]
                    ],
                }
            )
        )
        monkeypatch.setattr(main, "CONFIG_PATH", str(config))
        monkeypatch.setattr(main, "DATA_DIR", tmp_path / "data")
        monkeypatch.setattr(main, "RTSP_BASE", f"rtsp://127.0.0.1:{port}")
        monkeypatch.setattr(main, "PLAYBACK_BASE", f"http://127.0.0.1:{playback_port}")
        monkeypatch.setenv("NVR_AUTH_MODE", "basic")
        monkeypatch.setenv("NVR_USER", "fixture")
        monkeypatch.setenv("NVR_PASS", "synthetic-fixture")
        with TestClient(main.app) as client:
            auth = ("fixture", "synthetic-fixture")
            deadline = time.monotonic() + 25
            while time.monotonic() < deadline:
                if all(
                    c["detecting"] for c in client.get("/api/cameras", auth=auth).json()
                ):
                    break
                time.sleep(0.5)
            else:
                pytest.fail("Synthetic streams did not connect")
            headers = {"Origin": "http://testserver", "X-Capture-Request": "1"}
            ids = []
            for name, kind in [("cam1", "photo"), ("cam2", "clip")]:
                response = client.post(
                    f"/api/cameras/{name}/captures",
                    json={"kind": kind},
                    auth=auth,
                    headers=headers,
                )
                assert response.status_code == 202, response.text
                ids.append(response.json()["id"])
            deadline = time.monotonic() + 45
            while time.monotonic() < deadline:
                rows = [main.state.db.get(i) for i in ids]
                if all(row["status"] != "pending" for row in rows):
                    break
                time.sleep(0.5)
            assert all(row["status"] == "ready" for row in rows), rows
            for row in rows:
                response = client.get(f"/media/image/{row['id']}", auth=auth)
                assert response.status_code == 200 and response.content.startswith(
                    b"\xff\xd8"
                )
                image = main.DATA_DIR / row["image"]
                dimensions = subprocess.check_output(
                    [
                        "ffprobe",
                        "-v",
                        "error",
                        "-select_streams",
                        "v:0",
                        "-show_entries",
                        "stream=width,height",
                        "-of",
                        "csv=p=0",
                        str(image),
                    ],
                    text=True,
                ).strip()
                assert dimensions == "640,360"
            assert client.get(f"/media/clip/{ids[1]}", auth=auth).status_code == 200
            assert any(
                row["source"] == "motion" and row["status"] == "ready"
                for row in client.get("/api/events", auth=auth).json()
            )
            publishers[0].terminate()
            publishers[0].wait(timeout=5)
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                status = client.get("/api/cameras", auth=auth).json()
                if not status[0]["detecting"]:
                    break
                time.sleep(0.5)
            assert not status[0]["detecting"] and status[1]["detecting"]
    finally:
        for process in publishers:
            if process.poll() is None:
                process.kill()
            process.wait()
        subprocess.run(
            ["docker", "rm", "-f", container], capture_output=True, check=False
        )
