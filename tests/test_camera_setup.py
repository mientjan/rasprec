import hashlib
import importlib.util
import subprocess
from pathlib import Path
from unittest.mock import Mock

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "camera_setup", ROOT / "scripts/camera_setup.py"
)
setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)


@pytest.mark.parametrize(
    "user,cpu,expected",
    [
        ("armhf", "armv6l", "linux_armv6"),
        ("armhf", "armv7l", "linux_armv7"),
        ("armhf", "aarch64", "linux_armv7"),
        ("arm64", "aarch64", "linux_arm64"),
    ],
)
def test_architecture(user, cpu, expected):
    assert (
        setup.architecture(user, cpu, "Raspberry Pi Zero 2 W", "debian", "bookworm")
        == expected
    )


@pytest.mark.parametrize(
    "args",
    [
        ("amd64", "x86_64", "Raspberry Pi", "debian", "bookworm"),
        ("arm64", "armv7l", "Raspberry Pi", "debian", "bookworm"),
        ("arm64", "aarch64", "Raspberry Pi", "ubuntu", "noble"),
        ("armhf", "armv6l", "Raspberry Pi", "raspbian", "bullseye"),
        ("arm64", "aarch64", "other board", "debian", "trixie"),
    ],
)
def test_platform_rejected(args):
    with pytest.raises(ValueError):
        setup.architecture(*args)


def test_memory_profiles_and_overrides():
    assert setup.memory_limit("Raspberry Pi 5", 2 * 1024 * 1024) == 512
    assert setup.memory_limit("Raspberry Pi Zero 2 W", 450 * 1024) == 200
    assert setup.memory_limit("Raspberry Pi", 240 * 1024, "100") == 100
    for value in ("0", "-1", "1.5", "999999", "1M"):
        with pytest.raises(ValueError):
            setup.memory_limit("Raspberry Pi", 512 * 1024, value)


@pytest.mark.parametrize(
    "password", ["abc #def", "yes", "123", "a/b&c\\d", '"quote"\nnext', "PLACEHOLDER"]
)
def test_credentials_round_trip(password):
    rendered = setup.render(
        (ROOT / "mediamtx.yml").read_text(), "a/b&c", password, 1280, 720, 24, 2000000
    )
    data = yaml.safe_load(rendered)
    assert data["authInternalUsers"][0]["pass"] == setup.credential(password)
    assert data["authInternalUsers"][0]["user"] == setup.credential("a/b&c")
    assert data["paths"]["cam"]["rpiCameraCodec"] == "auto"
    assert data["srt"] is False and data["moq"] is False


def test_unresolved_template_and_reserved_user():
    text = (ROOT / "mediamtx.yml").read_text()
    with pytest.raises(ValueError):
        setup.render(text + "\nextra: LEFT_PLACEHOLDER\n", "view", "pw", 1, 1, 1, 1)
    with pytest.raises(ValueError):
        setup.render(text, "any", "pw", 1, 1, 1, 1)
    with pytest.raises(ValueError, match="colons"):
        setup.render(text, "view", "abc: def", 1, 1, 1, 1)


def test_checksum(tmp_path):
    archive = tmp_path / "archive.tar.gz"
    archive.write_bytes(b"archive")
    manifest = tmp_path / "checksums.sha256"
    digest = hashlib.sha256(b"archive").hexdigest()
    manifest.write_text(f"{digest} *archive.tar.gz\n")
    setup.verify_checksum(archive, manifest)
    archive.write_bytes(b"tampered")
    with pytest.raises(ValueError):
        setup.verify_checksum(archive, manifest)


def test_conditional_watchdog_idempotence():
    original = "[all]\nfoo=1\n[pi4]\nbar=2\n"
    result = setup.watchdog_config(original)
    assert result.startswith(original)
    assert "[all]\ndtparam=watchdog=on" in result
    assert setup.watchdog_config(result) == result


def test_probe_requires_actual_frame_and_escapes_credentials(tmp_path, monkeypatch):
    config = tmp_path / "config.yml"
    config.write_text(
        setup.render((ROOT / "mediamtx.yml").read_text(), "a/b", "p# @", 1, 1, 1, 1)
    )
    run = Mock(
        return_value=subprocess.CompletedProcess([], 0, b"# headers\n0, frame\n")
    )
    monkeypatch.setattr(setup.subprocess, "run", run)
    setup.probe(config, user="a/b", password="p# @")
    assert "rtsp://a%2Fb:p%23%20%40@127.0.0.1:8554/cam" in run.call_args.args[0]
    assert run.call_args.kwargs["stderr"] == subprocess.DEVNULL
    run.return_value = subprocess.CompletedProcess([], 0, b"# headers only\n")
    with pytest.raises(ValueError):
        setup.probe(config, deadline=0.001)


@pytest.mark.parametrize("existing", [False, True])
def test_transaction_restores_files_and_services(tmp_path, existing):
    files = [
        "/usr/local/bin/mediamtx",
        "/usr/local/etc/mediamtx.yml",
        "/etc/systemd/system/mediamtx.service",
    ]
    for file in files:
        path = tmp_path / file.lstrip("/")
        path.parent.mkdir(parents=True, exist_ok=True)
        if existing:
            path.write_text("old")
    log = tmp_path / "calls"
    script = f"""
set -eo pipefail
source '{ROOT}/scripts/camera-transaction.sh'
CAMERA_ROOT='{tmp_path}'
systemctl() {{ echo "$*" >> '{log}'; if [[ "$1" == is-* ]]; then {"return 0" if existing else "return 1"}; fi; }}
sudo() {{ "$@"; }}
camera_snapshot
for f in {" ".join(files)}; do echo new > "$CAMERA_ROOT$f"; done
camera_rollback
"""
    subprocess.run(["bash", "-c", script], check=True)
    for file in files:
        path = tmp_path / file.lstrip("/")
        if existing:
            assert path.read_text() == "old"
            assert list(path.parent.glob(path.name + ".backup.*"))
        else:
            assert not path.exists()
    calls = log.read_text()
    assert "stop mediamtx" in calls and "daemon-reload" in calls
    assert ("start mediamtx" in calls) == existing
    assert ("start rtsp-camera" in calls) == existing
