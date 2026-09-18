"""Pure setup helpers and bounded stream verification (no root required)."""

import argparse
import base64
import hashlib
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import quote

try:
    import yaml
except ImportError:
    yaml = None

VERSION = "v1.21.0"


class SetupError(ValueError):
    """Safe, actionable setup error without secret values."""


def architecture(userspace, machine, model, os_id, codename):
    if (
        "Raspberry Pi" not in model
        or os_id not in ("raspbian", "debian")
        or codename not in ("bookworm", "trixie")
    ):
        raise SetupError("Requires Raspberry Pi OS Bookworm/Trixie on a Raspberry Pi")
    if userspace == "arm64" and machine in ("aarch64", "arm64"):
        return "linux_arm64"
    if userspace == "armhf":
        if machine == "armv6l":
            return "linux_armv6"
        if machine in ("armv7l", "aarch64", "arm64"):
            return "linux_armv7"
    raise SetupError("Unsupported CPU/userspace architecture combination")


def memory_limit(model, total_kb, override=None):
    value = str(override if override is not None else (512 if "Pi 5" in model else 200))
    if not re.fullmatch(r"[1-9][0-9]*", value) or int(value) * 1024 > total_kb // 2:
        raise SetupError(
            "CAMERA_MEMORY_MAX_MB must be a positive integer no greater than half of MemTotal"
        )
    return int(value)


def credential(value):
    # MediaMTX rejects spaces/colon/slashes in plaintext credentials even in valid
    # YAML. Its supported SHA-256 representation preserves the exact login value.
    if re.fullmatch(r"[a-zA-Z0-9!$()*+.;<=>\[\]^_{}@#&-]+", value):
        return value
    return (
        "sha256:" + base64.b64encode(hashlib.sha256(value.encode()).digest()).decode()
    )


def render(template, user, password, width, height, fps, bitrate):
    if not user or not password or user == "any" or ":" in user or ":" in password:
        raise SetupError(
            'Nonempty credentials required; username "any" is reserved and MediaMTX RTSP does not support colons in credentials'
        )
    data = yaml.safe_load(template)
    data["authInternalUsers"][0].update(
        user=credential(user), **{"pass": credential(password)}
    )
    camera = data["paths"]["cam"]
    for key, value in zip(
        ("Width", "Height", "FPS", "Bitrate"), (width, height, fps, bitrate)
    ):
        if int(value) <= 0:
            raise SetupError("Camera settings must be positive")
        camera["rpiCamera" + key] = int(value)
    # Check structure separately from credentials: literal placeholder-like passwords are valid.
    structure = yaml.safe_dump({**data, "authInternalUsers": []})
    if "PLACEHOLDER" in structure or camera["source"] != "rpiCamera":
        raise SetupError(
            "Unresolved configuration placeholder or invalid camera source"
        )
    return yaml.safe_dump(data, sort_keys=False)


def verify_checksum(archive, manifest):
    matches = []
    for line in Path(manifest).read_text().splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1].lstrip("*") == Path(archive).name:
            matches.append(parts[0])
    hasher = hashlib.sha256()
    with Path(archive).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    digest = hasher.hexdigest()
    if matches != [digest]:
        raise SetupError("Missing, ambiguous or mismatched release checksum")


def watchdog_config(text):
    begin, end = "# BEGIN RaspRec watchdog", "# END RaspRec watchdog"
    text = re.sub(
        re.escape(begin) + r".*?" + re.escape(end) + r"\n?", "", text, flags=re.DOTALL
    )
    return text.rstrip() + f"\n\n{begin}\n[all]\ndtparam=watchdog=on\n{end}\n"


def probe(config, deadline=45, user=None, password=None, address="127.0.0.1:8554"):
    data = yaml.safe_load(Path(config).read_text())
    account = data["authInternalUsers"][0]
    user = user if user is not None else account["user"]
    password = password if password is not None else account["pass"]
    url = "rtsp://{}:{}@{}/cam".format(
        quote(user, safe=""), quote(password, safe=""), address
    )
    end = time.monotonic() + deadline
    while time.monotonic() < end:
        try:
            result = subprocess.run(
                [
                    "ffmpeg",
                    "-nostdin",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-rtsp_transport",
                    "tcp",
                    "-threads",
                    "1",
                    "-i",
                    url,
                    "-map",
                    "0:v:0",
                    "-frames:v",
                    "1",
                    "-f",
                    "framehash",
                    "-",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                timeout=min(10, max(0.1, end - time.monotonic())),
                check=False,
            )
            if result.returncode == 0 and any(
                line and not line.startswith(b"#")
                for line in result.stdout.splitlines()
            ):
                return
        except subprocess.TimeoutExpired:
            pass
        time.sleep(min(1, max(0, end - time.monotonic())))
    raise SetupError(
        "Authenticated video probe failed (credentials and FFmpeg output suppressed)"
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "action", choices=("preflight", "render", "checksum", "watchdog", "probe")
    )
    parser.add_argument("paths", nargs="*")
    args = parser.parse_args()
    if args.action == "preflight":
        info = {}
        for line in Path("/etc/os-release").read_text().splitlines():
            if "=" in line:
                key, value = line.split("=", 1)
                info[key] = value.strip('"')
        model = Path("/proc/device-tree/model").read_text().rstrip("\0")
        arch = architecture(
            subprocess.check_output(
                ["dpkg", "--print-architecture"], text=True
            ).strip(),
            os.uname().machine,
            model,
            info.get("ID"),
            info.get("VERSION_CODENAME"),
        )
        total = int(
            re.search(
                r"^MemTotal:\s+(\d+)", Path("/proc/meminfo").read_text(), re.MULTILINE
            )[1]
        )
        print(arch)
        print(memory_limit(model, total, os.environ.get("CAMERA_MEMORY_MAX_MB")))
    elif args.action == "render":
        print(
            render(
                Path(args.paths[0]).read_text(),
                os.environ["STREAM_USER"],
                os.environ["STREAM_PASS"],
                *[os.environ[k] for k in ("WIDTH", "HEIGHT", "FRAMERATE", "BITRATE")],
            ),
            end="",
        )
    elif args.action == "checksum":
        verify_checksum(*args.paths)
    elif args.action == "watchdog":
        print(watchdog_config(Path(args.paths[0]).read_text()), end="")
    elif args.action == "probe":
        probe(
            args.paths[0],
            user=os.environ.get("STREAM_USER"),
            password=os.environ.get("STREAM_PASS"),
        )


if __name__ == "__main__":
    try:
        main()
    except SetupError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
    except Exception:  # noqa: BLE001 - never echo secret-bearing parser errors
        # Never echo credentials, malformed YAML or child command arguments.
        print(
            "Camera setup validation failed; check platform, settings, checksum or stream readiness.",
            file=sys.stderr,
        )
        sys.exit(1)
