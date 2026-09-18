"""Local Git fixtures only; never executes camera setup or privileged commands."""

import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def git(path, *args):
    return subprocess.check_output(["git", "-C", str(path), *args], text=True).strip()


@pytest.fixture
def repo(tmp_path):
    upstream = tmp_path / "upstream"
    upstream.mkdir()
    git(upstream, "init", "-b", "main")
    git(upstream, "config", "user.name", "Fixture")
    git(upstream, "config", "user.email", "fixture@example.invalid")
    (upstream / "device").mkdir()
    (upstream / "device/run.sh").write_text("echo DEVICE-ONLY\n")
    (upstream / "server").mkdir()
    (upstream / "server/README.md").write_text("Not needed on the Pi")
    (upstream / "run.sh").write_text((ROOT / "run.sh").read_text())
    git(upstream, "add", ".")
    git(upstream, "commit", "-m", "fixture")
    home = tmp_path / "home"
    home.mkdir()
    script = tmp_path / "install.sh"
    script.write_text(
        (ROOT / "install.sh")
        .read_text()
        .replace("https://github.com/mientjan/rasprec.git", upstream.as_uri())
    )
    return upstream, home, script


def install(repo, answer="n\n"):
    _, home, script = repo
    return subprocess.run(
        ["bash", str(script)],
        input=answer,
        text=True,
        capture_output=True,
        env={**os.environ, "HOME": str(home)},
    )


def test_new_device_only_install_and_wrapper(repo, tmp_path):
    upstream, home, _ = repo
    result = install(repo, "y\n")
    assert result.returncode == 0, result.stderr
    assert "DEVICE-ONLY" in result.stdout
    checkout = home / "rasprec"
    assert (checkout / "device/run.sh").exists()
    assert not (checkout / "server").exists()
    result = subprocess.run(
        ["bash", str(checkout / "run.sh")], cwd=tmp_path, capture_output=True, text=True
    )
    assert result.stdout.strip() == "DEVICE-ONLY"
    (upstream / "device/new.txt").write_text("update")
    git(upstream, "add", ".")
    git(upstream, "commit", "-m", "update")
    assert install(repo).returncode == 0
    assert (checkout / "device/new.txt").exists()
    assert not (checkout / "server").exists()


def test_full_checkout_preserved_and_dirty_update_refused(repo):
    upstream, home, _ = repo
    subprocess.run(
        ["git", "clone", str(upstream), str(home / "rasprec")],
        check=True,
        capture_output=True,
    )
    assert install(repo).returncode == 0
    checkout = home / "rasprec"
    assert (checkout / "server").exists()
    (checkout / "device/run.sh").write_text("local customization")
    assert install(repo).returncode != 0
    assert (checkout / "device/run.sh").read_text() == "local customization"


def test_local_commit_refused(repo):
    assert install(repo).returncode == 0
    checkout = repo[1] / "rasprec"
    git(checkout, "config", "user.name", "Fixture")
    git(checkout, "config", "user.email", "fixture@example.invalid")
    (checkout / "device/local.txt").write_text("local")
    git(checkout, "add", ".")
    git(checkout, "commit", "-m", "local work")
    head = git(checkout, "rev-parse", "HEAD")
    assert install(repo).returncode != 0
    assert git(checkout, "rev-parse", "HEAD") == head
