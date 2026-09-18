"""Opt-in Docker checks: RUN_DOCKER_TESTS=1 PYTHONPATH=docker pytest tests/ -q.

Uses fixture secrets, ephemeral loopback ports, and removes every test container.
No actual camera hardware or production Docker configuration is accessed.
"""

import base64
import importlib.util
import json
import os
import socket
import subprocess
import time
import uuid
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_DOCKER_TESTS") != "1", reason="opt-in Docker integration"
)
ROOT = Path(__file__).resolve().parents[1]


def camera_config():
    spec = importlib.util.spec_from_file_location(
        "setup", ROOT / "scripts/camera_setup.py"
    )
    setup = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(setup)
    return yaml.safe_load(
        setup.render(
            (ROOT / "mediamtx.yml").read_text(),
            "view/one",
            "a #b /c",
            1280,
            720,
            24,
            2000000,
        )
    )


def docker(*args):
    return subprocess.check_output(
        ["docker", *args], text=True, stderr=subprocess.STDOUT
    )


@pytest.mark.parametrize("version", ["1.21.0", "1.19.3"])
def test_pinned_schema(tmp_path, version):
    from nvr import config, render_config

    if version == "1.21.0":
        conf = camera_config()
    else:
        conf = render_config.build(
            config.Config(
                [config.Camera("fake", "rtsp://127.0.0.1:1/cam")], 3600, 1, 60
            )
        )
    path = tmp_path / "config.yml"
    path.write_text(yaml.safe_dump(conf))
    name = "rasprec-test-" + uuid.uuid4().hex
    try:
        docker(
            "run",
            "-d",
            "--name",
            name,
            "-v",
            f"{path}:/test.yml:ro",
            f"bluenviron/mediamtx:{version}",
            "/test.yml",
        )
        time.sleep(2)
        assert docker("inspect", "-f", "{{.State.Running}}", name).strip() == "true", (
            docker("logs", name)
        )
        assert "configuration loaded" in docker("logs", name)
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True, check=False)


def test_special_character_login(tmp_path):
    conf = camera_config()
    # No physical camera: a publisher path lets authentication be tested independently.
    conf["paths"]["cam"] = {"source": "publisher"}
    path = tmp_path / "config.yml"
    path.write_text(yaml.safe_dump(conf))
    name = "rasprec-auth-test-" + uuid.uuid4().hex
    try:
        docker(
            "run",
            "-d",
            "--name",
            name,
            "-p",
            "127.0.0.1::8554",
            "-v",
            f"{path}:/test.yml:ro",
            "bluenviron/mediamtx:1.21.0",
            "/test.yml",
        )
        time.sleep(2)
        port = int(docker("port", name, "8554/tcp").strip().rsplit(":", 1)[1])

        def describe(password):
            auth = base64.b64encode(f"view/one:{password}".encode()).decode()
            with socket.create_connection(("127.0.0.1", port), timeout=10) as sock:
                sock.sendall(
                    (
                        f"DESCRIBE rtsp://127.0.0.1:{port}/cam RTSP/1.0\r\nCSeq: 1\r\nAuthorization: Basic {auth}\r\n\r\n"
                    ).encode()
                )
                return sock.recv(4096).splitlines()[0]

        assert b"404" in describe("a #b /c")  # authenticated, but no publisher
        assert b"401" in describe("wrong")
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True, check=False)


def test_compose_limits(tmp_path):
    env = tmp_path / "fixture.env"
    env.write_text("NVR_PASS=fixture\n")
    compose = tmp_path / "compose.yml"
    compose.write_text(
        (ROOT / "docker/compose.yml")
        .read_text()
        .replace("env_file: [.env]", f"env_file: [{env}]")
    )
    data = json.loads(
        docker(
            "compose",
            "--env-file",
            str(env),
            "-f",
            str(compose),
            "config",
            "--format",
            "json",
        )
    )
    for name in ("nvr-config", "mediamtx", "nvr"):
        service = data["services"][name]
        assert int(service["mem_limit"]) > 0 and float(service["cpus"]) > 0
        assert service["logging"]["options"] == {"max-size": "10m", "max-file": "3"}


def test_real_frame_probe_with_special_password(tmp_path):
    import shutil
    from urllib.parse import quote

    if not shutil.which("ffmpeg"):
        pytest.skip("FFmpeg required for synthetic-frame integration")
    spec = importlib.util.spec_from_file_location(
        "setup", ROOT / "scripts/camera_setup.py"
    )
    setup = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(setup)
    conf = camera_config()
    conf["paths"]["cam"] = {"source": "publisher"}
    conf["authInternalUsers"][0]["permissions"].append({"action": "publish"})
    path = tmp_path / "config.yml"
    path.write_text(yaml.safe_dump(conf))
    name = "rasprec-frame-test-" + uuid.uuid4().hex
    publisher = None
    try:
        docker(
            "run",
            "-d",
            "--name",
            name,
            "-p",
            "127.0.0.1::8554",
            "-v",
            f"{path}:/test.yml:ro",
            "bluenviron/mediamtx:1.21.0",
            "/test.yml",
        )
        time.sleep(2)
        port = int(docker("port", name, "8554/tcp").strip().rsplit(":", 1)[1])
        url = f"rtsp://{quote('view/one', safe='')}:{quote('a #b /c', safe='')}@127.0.0.1:{port}/cam"
        publisher = subprocess.Popen(
            [
                "ffmpeg",
                "-nostdin",
                "-loglevel",
                "error",
                "-re",
                "-f",
                "lavfi",
                "-i",
                "testsrc2=size=160x120:rate=5",
                "-an",
                "-c:v",
                "libx264",
                "-threads",
                "1",
                "-g",
                "5",
                "-t",
                "20",
                "-rtsp_transport",
                "tcp",
                "-f",
                "rtsp",
                url,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        setup.probe(
            path,
            deadline=15,
            user="view/one",
            password="a #b /c",
            address=f"127.0.0.1:{port}",
        )
    finally:
        if publisher is not None:
            publisher.terminate()
            try:
                publisher.wait(timeout=5)
            except subprocess.TimeoutExpired:
                publisher.kill()
                publisher.wait()
        subprocess.run(["docker", "rm", "-f", name], capture_output=True, check=False)
