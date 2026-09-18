"""Real RTMPS authorization and Pi publisher smoke test. Ephemeral credentials/certs only."""

import importlib.util
import ipaddress
import json
import os
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest
import yaml
from nvr import config, render_config

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_DOCKER_TESTS") != "1" or not shutil.which("ffmpeg"),
    reason="opt-in Docker, ffmpeg and PyAV integration",
)
ROOT = Path(__file__).resolve().parents[2]


def test_verified_publisher_and_path_permissions(tmp_path, monkeypatch):
    pytest.importorskip("av")
    spec = importlib.util.spec_from_file_location(
        "tls_fixtures", ROOT / "device/tests/test_publisher.py"
    )
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    ca, cert, key = fixture.certificates(tmp_path)
    records = tmp_path / "recordings"
    records.mkdir()
    cloud_config = tmp_path / "cloud.yml"
    source_config = tmp_path / "source.yml"
    source_config.write_text(
        yaml.safe_dump(
            {
                "paths": {"cam": {"source": "publisher"}},
                "rtspTransports": ["tcp"],
                "hls": False,
                "webrtc": False,
                "rtmp": False,
            }
        )
    )
    names = [
        "rasprec-tls-source-" + uuid.uuid4().hex[:8],
        "rasprec-tls-cloud-" + uuid.uuid4().hex[:8],
    ]
    processes = []
    network = "rasprec-tls-" + uuid.uuid4().hex[:8]

    def docker(*args):
        return subprocess.check_output(
            ["docker", *args], text=True, stderr=subprocess.DEVNULL
        ).strip()

    def render(token):
        conf_path = tmp_path / "cameras.yml"
        conf_path.write_text(
            yaml.safe_dump(
                {
                    "retention": {"segment": "2s", "continuous": "1h"},
                    "cameras": [
                        {"name": "one", "source": "push", "publish_token": token},
                        {"name": "two", "source": "push", "publish_token": "B" * 43},
                    ],
                }
            )
        )
        result = render_config.build(config.load(conf_path))
        result["logLevel"] = "info"
        result["rtmpServerCert"] = "/fixture/cert.pem"
        result["rtmpServerKey"] = "/fixture/key.pem"
        cloud_config.write_text(yaml.safe_dump(result))
        # MediaMTX cannot accept plaintext RTMP when encryption is strict.
        assert result["rtmpEncryption"] == "strict"

    def launch(token="A" * 43):
        credentials = tmp_path / ("device-" + uuid.uuid4().hex)
        credentials.mkdir()
        settings = credentials / "publisher.json"
        settings.write_text(
            json.dumps(
                {
                    "server_host": "localhost",
                    "server_port": tls_port,
                    "camera": "one",
                    "publish_token": token,
                    "source_url": f"rtsp://127.0.0.1:{source_port}/cam",
                }
            )
        )
        settings.chmod(0o600)
        process = subprocess.Popen(
            [sys.executable, str(ROOT / "device/scripts/publish.py")],
            env={
                **os.environ,
                "CREDENTIALS_DIRECTORY": str(credentials),
                "SSL_CERT_FILE": str(ca),
            },
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        processes.append(process)
        return process

    def wait_live(process):
        deadline = time.monotonic() + 15
        before = sum(p.stat().st_size for p in records.rglob("*.mp4"))
        while time.monotonic() < deadline:
            assert process.poll() is None, (
                "Verified publisher exited unexpectedly: " + docker("logs", names[1])
            )
            if sum(p.stat().st_size for p in records.rglob("*.mp4")) > before:
                return
            time.sleep(0.25)
        pytest.fail(
            "No recording received over verified RTMPS: " + docker("logs", names[1])
        )

    try:
        docker("network", "create", network)
        subnet = json.loads(docker("network", "inspect", network))[0]["IPAM"]["Config"][
            0
        ]["Subnet"]
        # Explicit IPAM is required for Docker static-address clients. Reuse the
        # free subnet selected by Docker rather than guessing a host network.
        docker("network", "rm", network)
        docker("network", "create", "--subnet", subnet, network)
        reader_ip = str(ipaddress.ip_network(subnet)[10])
        monkeypatch.setenv("NVR_INTERNAL_READER_IP", reader_ip)
        render("A" * 43)
        docker(
            "run",
            "-d",
            "--name",
            names[0],
            "-p",
            "127.0.0.1::8554",
            "-v",
            f"{source_config}:/mediamtx.yml:ro",
            "bluenviron/mediamtx:1.19.3",
        )
        docker(
            "run",
            "-d",
            "--name",
            names[1],
            "--network",
            network,
            "-p",
            "127.0.0.1::1936",
            "-p",
            "127.0.0.1::1935",
            "-v",
            f"{cloud_config}:/mediamtx.yml:ro",
            "-v",
            f"{tmp_path}:/fixture:ro",
            "-v",
            f"{records}:/recordings",
            "bluenviron/mediamtx:1.19.3",
        )
        source_port = int(docker("port", names[0], "8554/tcp").rsplit(":", 1)[1])
        tls_port = int(docker("port", names[1], "1936/tcp").rsplit(":", 1)[1])
        plain_port = int(docker("port", names[1], "1935/tcp").rsplit(":", 1)[1])
        camera = subprocess.Popen(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-re",
                "-f",
                "lavfi",
                "-i",
                "testsrc2=size=320x240:rate=10",
                "-c:v",
                "libx264",
                "-threads",
                "1",
                "-preset",
                "ultrafast",
                "-g",
                "10",
                "-f",
                "rtsp",
                "-rtsp_transport",
                "tcp",
                f"rtsp://127.0.0.1:{source_port}/cam",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        processes.append(camera)
        time.sleep(2)
        publisher = launch()
        wait_live(publisher)
        # The actual NVR reader identity can decode the pushed stream internally.
        subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "--network",
                network,
                "--ip",
                reader_ip,
                "--entrypoint",
                "ffmpeg",
                "rasprec-nvr:test",
                "-v",
                "error",
                "-rtsp_transport",
                "tcp",
                "-i",
                f"rtsp://{names[1]}:8554/one",
                "-frames:v",
                "1",
                "-f",
                "null",
                "-",
            ],
            check=True,
            timeout=25,
            capture_output=True,
        )

        # A malicious RTMP client must not read, publish another camera, or
        # replace a connected publisher, even using a valid camera credential.
        script = tmp_path / "attempt.py"
        script.write_text("""
import importlib.util, sys
from urllib.parse import urlencode
import av
spec=importlib.util.spec_from_file_location('pub',sys.argv[1]); p=importlib.util.module_from_spec(spec);spec.loader.exec_module(p)
av.logging.set_level(av.logging.PANIC)
action,path,user,token=sys.argv[4:8]
try:
 with p.TLSBridge('localhost',int(sys.argv[2]),sys.argv[3]) as bridge:
  url=f'rtmp://127.0.0.1:{bridge.port_local}/{path}?'+urlencode({'user':user,'pass':token})
  if action=='read':
   with av.open(url,timeout=(5,5)) as stream: next(stream.demux())
  else:
   with av.open(sys.argv[8],options={'rtsp_transport':'tcp'},timeout=(5,5)) as source:
    video=source.streams.video[0]
    with av.open(url,'w',format='flv',options={'rw_timeout':'5000000'}) as output:
     target=output.add_stream_from_template(video) if hasattr(output,'add_stream_from_template') else output.add_stream(template=video)
     for packet in source.demux(video):
      if packet.dts is None:continue
      packet.stream=target;output.mux(packet)
except Exception:sys.exit(7)
sys.exit(0)
""")
        for action, path, user, token in [
            ("publish", "two", "camera_one", "A" * 43),
            ("publish", "one", "camera_one", "wrong" * 10),
            ("publish", "two", "", ""),
            ("read", "one", "camera_one", "A" * 43),
            ("read", "one", "", ""),
            ("publish", "one", "camera_one", "A" * 43),
        ]:
            result = subprocess.run(
                [
                    sys.executable,
                    str(script),
                    str(ROOT / "device/scripts/publish.py"),
                    str(tls_port),
                    str(ca),
                    action,
                    path,
                    user,
                    token,
                    f"rtsp://127.0.0.1:{source_port}/cam",
                ],
                timeout=20,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            assert result.returncode == 7, f"Access was not denied: {action} {path}"
        assert publisher.poll() is None

        # There is no plaintext listener. A socket may connect through Docker's
        # port proxy, but it cannot complete an RTMP handshake.
        import socket

        try:
            with socket.create_connection(
                ("127.0.0.1", plain_port), timeout=2
            ) as connection:
                connection.sendall(b"\x03" + b"\x00" * 1536)
                assert not connection.recv(1)
        except (OSError, TimeoutError):
            pass

        # Rotation/restart revokes the old token and closes its existing stream.
        render("C" * 43)
        docker("restart", names[1])
        publisher.wait(timeout=20)
        # Docker may reassign an ephemeral published port across restart.
        tls_port = int(docker("port", names[1], "1936/tcp").rsplit(":", 1)[1])
        old = launch()
        assert old.wait(timeout=20) != 0
        fresh = launch("C" * 43)
        wait_live(fresh)
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
            process.wait()
        for name in names:
            subprocess.run(
                ["docker", "rm", "-f", name], capture_output=True, check=False
            )

        subprocess.run(
            ["docker", "network", "rm", network], capture_output=True, check=False
        )
