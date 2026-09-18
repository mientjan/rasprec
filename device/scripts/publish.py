#!/usr/bin/env python3
"""Outbound H.264 publisher. TLS verification is owned by Python, not FFmpeg.

PyAV remuxes without decoding/re-encoding and keeps credentials out of process
arguments. The plaintext RTMP hop exists only on an ephemeral IPv4 loopback socket
on the Pi. It is never sent to the network.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import select
import ssl
import subprocess
import sys
from pathlib import Path
from urllib.parse import quote, urlencode, urlsplit

TIMEOUT = 15
TOKEN = re.compile(r"[A-Za-z0-9_-]{32,128}")
NAME = re.compile(r"[a-z0-9][a-z0-9_-]*")


class PublisherError(ValueError):
    """Only fixed, secret-free diagnostic messages may be used here."""


def validate(data):
    if not isinstance(data, dict) or set(data) - {
        "server_host",
        "server_port",
        "camera",
        "publish_token",
        "source_url",
    }:
        raise PublisherError("invalid publisher configuration fields")
    host = data.get("server_host", "")
    if not isinstance(host, str) or not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9.-]{0,252}", host
    ):
        raise PublisherError("server_host must be a DNS hostname or IPv4 address")
    port = data.get("server_port", 1936)
    if type(port) is not int or not 1 <= port <= 65535:
        raise PublisherError("invalid server_port")
    if not isinstance(data.get("camera"), str) or not NAME.fullmatch(data["camera"]):
        raise PublisherError("invalid camera name")
    if not isinstance(data.get("publish_token"), str) or not TOKEN.fullmatch(
        data["publish_token"]
    ):
        raise PublisherError("publish_token must be 32-128 URL-safe characters")
    source = urlsplit(data.get("source_url", ""))
    if (
        source.scheme != "rtsp"
        or source.hostname not in {"127.0.0.1", "::1"}
        or not source.path
    ):
        raise PublisherError("source_url must be the Pi's loopback RTSP stream")
    # Validate the port without ever printing a malformed credential-bearing URL.
    if source.port is not None and not 1 <= source.port <= 65535:
        raise PublisherError("invalid local source port")
    return {**data, "server_port": port}


def load(path, *, systemd_credential=False):
    path = Path(path)
    info = path.stat()
    mode = info.st_mode & 0o777
    # LoadCredential + DynamicUser on systemd 252 uses a named-user ACL.
    # Its read-only ACL mask appears as 0440 to stat(), although the owning
    # group has no access. Only trust this on systemd's root-owned runtime copy;
    # the original /etc configuration must still be private (0600 or 0400).
    managed_acl = (
        systemd_credential
        and path.parent == Path("/run/credentials/rasprec-publisher.service")
        and info.st_uid == 0
        and mode == 0o440
    )
    if mode & 0o077 and not managed_acl:
        raise PublisherError(
            "configuration permissions rejected; source file must be mode 0600 or 0400"
        )
    return validate(json.loads(path.read_text()))


def tls_context(cafile=None):
    # cafile is used only by tests. Production always uses system trust.
    context = ssl.create_default_context(cafile=cafile)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.check_hostname = True
    context.verify_mode = ssl.CERT_REQUIRED
    return context


class TLSBridge:
    """Verified TLS in a separate process: PyAV may hold the GIL during RTMP setup.

    The helper's argv contains only a public hostname, port and optional test CA
    filename, never RTSP or publishing credentials.
    """

    def __init__(self, host, port, cafile=None):
        self.host, self.port, self.cafile = host, port, cafile
        self.process = None

    async def serve(self):
        context = tls_context(self.cafile)
        remote_reader, remote_writer = await asyncio.wait_for(
            asyncio.open_connection(
                self.host,
                self.port,
                ssl=context,
                server_hostname=self.host,
                ssl_handshake_timeout=TIMEOUT,
            ),
            TIMEOUT + 1,
        )
        stop = asyncio.Event()
        clients = []
        tasks = set()
        claimed = False

        async def copy(reader, writer, idle_timeout):
            while True:
                block = await asyncio.wait_for(reader.read(65536), idle_timeout)
                if not block:
                    return
                writer.write(block)
                await asyncio.wait_for(writer.drain(), TIMEOUT)

        async def relay(reader, writer):
            nonlocal claimed
            if claimed:
                writer.close()
                return
            claimed = True
            clients.append(writer)
            server.close()
            pair = {
                asyncio.create_task(copy(reader, remote_writer, TIMEOUT)),
                asyncio.create_task(copy(remote_reader, writer, None)),
            }
            tasks.update(pair)
            try:
                done, pending = await asyncio.wait(
                    pair, return_when=asyncio.FIRST_COMPLETED
                )
                for task in done:
                    task.result()
            except (OSError, TimeoutError, asyncio.CancelledError):
                pass
            finally:
                for task in pair:
                    task.cancel()
                await asyncio.gather(*pair, return_exceptions=True)
                stop.set()

        server = await asyncio.start_server(relay, "127.0.0.1", 0, limit=65536)
        # Internal IPC only. No plaintext listener is created before TLS validation.
        print(server.sockets[0].getsockname()[1], flush=True)
        try:
            await stop.wait()
        finally:
            server.close()
            # Abort on session termination; never wait indefinitely for close_notify.
            for writer in [*clients, remote_writer]:
                writer.transport.abort()
            await server.wait_closed()
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    def __enter__(self):
        args = [
            sys.executable,
            "-I",
            str(Path(__file__).resolve()),
            "--tls-bridge",
            self.host,
            str(self.port),
        ]
        if self.cafile:
            args.append(str(self.cafile))
        self.process = subprocess.Popen(
            args, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL
        )
        try:
            if not select.select([self.process.stdout], [], [], TIMEOUT + 5)[0]:
                raise TimeoutError("TLS connection timed out")
            value = self.process.stdout.readline(16).strip()
            if not value.isdigit():
                status = self.process.wait(timeout=2)
                if status == 65:
                    raise ssl.SSLCertVerificationError(
                        "Server certificate verification failed"
                    )
                raise ConnectionError("TLS connection failed")
            self.port_local = int(value)
            return self
        except BaseException:
            self.close()
            raise

    def close(self):
        if self.process:
            if self.process.poll() is None:
                self.process.terminate()
                try:
                    self.process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait()
            self.process.stdout.close()

    def __exit__(self, *args):
        self.close()


def publish(data, *, cafile=None):
    stage = "loading PyAV"
    try:
        import av

        # Never emit native FFmpeg errors that may include stream passwords.
        av.logging.set_level(av.logging.PANIC)
        stage = "opening local RTSP stream"
        with av.open(
            data["source_url"],
            options={"rtsp_transport": "tcp"},
            timeout=(TIMEOUT, TIMEOUT),
        ) as source:
            stage = "checking local H.264 video"
            video = source.streams.video[0]
            if video.codec_context.name != "h264":
                raise PublisherError("cloud publisher requires an H.264 camera stream")
            stage = "connecting verified TLS to server"
            with TLSBridge(data["server_host"], data["server_port"], cafile) as bridge:
                query = urlencode(
                    {"user": "camera_" + data["camera"], "pass": data["publish_token"]}
                )
                destination = (
                    f"rtmp://127.0.0.1:{bridge.port_local}/{data['camera']}?{query}"
                )
                stage = "opening RTMP output"
                with av.open(
                    destination,
                    "w",
                    format="flv",
                    options={
                        "rtmp_live": "live",
                        "rw_timeout": str(TIMEOUT * 1_000_000),
                    },
                ) as output:
                    stage = "copying H.264 stream template"
                    if hasattr(output, "add_stream_from_template"):
                        out_stream = output.add_stream_from_template(video)
                    else:  # Debian Bookworm ships PyAV 10.
                        out_stream = output.add_stream(template=video)
                    started = False
                    origin = None
                    stage = "remuxing video packets"
                    for packet in source.demux(video):
                        if packet.dts is None:
                            continue
                        if not started:
                            if not packet.is_keyframe:
                                continue
                            started = True
                            origin = packet.dts
                        packet.dts -= origin
                        if packet.pts is not None:
                            packet.pts -= origin
                        packet.stream = out_stream
                        output.mux(packet)
    except PublisherError:
        raise
    except Exception as exc:
        # Do not chain native errors: they may contain source/publishing secrets.
        raise PublisherError(f"{stage} failed ({type(exc).__name__})") from None


def configure():
    from getpass import getpass

    if os.geteuid() != 0:
        raise PublisherError("configuration must be installed as root")
    path = Path("/etc/rasprec/publisher.json")
    if (
        path.exists()
        and input("Replace existing publisher configuration? [y/N] ").lower() != "y"
    ):
        return
    host = input("AWS hostname (same as website certificate): ").strip()
    camera = input("Camera name configured on the server: ").strip()
    token = getpass("This camera's publishing token: ")
    user = input("Local Pi stream username [view]: ").strip() or "view"
    password = getpass("Local Pi stream password: ")
    data = validate(
        {
            "server_host": host,
            "server_port": 1936,
            "camera": camera,
            "publish_token": token,
            "source_url": f"rtsp://{quote(user, safe='')}:{quote(password, safe='')}@127.0.0.1:8554/cam",
        }
    )
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    temporary = path.with_suffix(".tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as handle:
        os.fchmod(handle.fileno(), 0o600)
        json.dump(data, handle, indent=2)
    temporary.replace(path)
    print("Saved private publisher configuration (values not printed).")


def main():
    stage = "startup"
    try:
        if len(sys.argv) in (4, 5) and sys.argv[1] == "--tls-bridge":
            bridge = TLSBridge(
                sys.argv[2],
                int(sys.argv[3]),
                sys.argv[4] if len(sys.argv) == 5 else None,
            )
            try:
                asyncio.run(bridge.serve())
                return 0
            except ssl.SSLCertVerificationError:
                return 65
        if sys.argv[1:] == ["--configure"]:
            configure()
            return 0
        if sys.argv[1:]:
            raise PublisherError("unsupported arguments")
        credential_dir = os.environ.get("CREDENTIALS_DIRECTORY", "/etc/rasprec")
        stage = "reading private configuration"
        data = load(
            Path(credential_dir) / "publisher.json",
            systemd_credential="CREDENTIALS_DIRECTORY" in os.environ,
        )
        stage = "publishing"
        publish(data)
        print("Camera stream ended; service will reconnect.", flush=True)
        return 1
    except KeyboardInterrupt:
        return 0
    except PublisherError as exc:
        print(f"Publishing stopped: {exc}.", file=sys.stderr, flush=True)
        return 1
    except Exception as exc:
        # Never print exception text, URLs, configuration, or native media logs.
        print(
            f"Publishing stopped during {stage} ({type(exc).__name__}); verify configuration, certificate and camera availability.",
            file=sys.stderr,
            flush=True,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
