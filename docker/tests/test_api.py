"""API tests against a live app instance with no MediaMTX and no cameras.

The motion workers start, fail to reach their fake camera and back off, which
is exactly the state the app has to survive on a cold start.
"""

import importlib
import os

import pytest
from fastapi.testclient import TestClient

CONFIG = """
retention: {continuous: 1h, clips_days: 1, segment: 1m}
cameras:
  - name: fake
    url: rtsp://user:${FAKE_PASS}@127.0.0.1:1/cam
"""

AUTH = ("admin", "pw")


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    data = tmp_path_factory.mktemp("data")
    config = data / "cameras.yml"
    config.write_text(CONFIG)
    os.environ.update(
        NVR_CONFIG=str(config),
        NVR_DATA=str(data),
        NVR_USER="admin",
        NVR_PASS="pw",
        FAKE_PASS="x",
        NVR_LOG_LEVEL="CRITICAL",
        # Nothing is listening here, which is the point.
        MEDIAMTX_HOST="127.0.0.1",
    )
    main = importlib.reload(importlib.import_module("nvr.__main__"))
    with TestClient(main.app) as test_client:
        yield test_client


@pytest.mark.parametrize("path", ["/", "/api/cameras", "/api/events", "/media/clip/1"])
def test_every_route_requires_auth(client, path):
    assert client.get(path).status_code == 401


def test_wrong_password_is_rejected(client):
    assert client.get("/api/cameras", auth=("admin", "nope")).status_code == 401


def test_index_and_assets_are_served(client):
    assert "rasprec" in client.get("/", auth=AUTH).text
    assert client.get("/static/app.js", auth=AUTH).status_code == 200
    assert client.get("/static/style.css", auth=AUTH).status_code == 200


def test_static_route_does_not_serve_arbitrary_files(client):
    assert client.get("/static/config.py", auth=AUTH).status_code == 404
    assert client.get("/static/..%2Fconfig.py", auth=AUTH).status_code == 404


def test_cameras_report_no_signal_before_a_frame_arrives(client):
    assert client.get("/api/cameras", auth=AUTH).json() == [
        {"name": "fake", "recording": True, "detecting": False, "last_frame_age": None}
    ]


def test_events_start_empty_and_validate_the_date_filter(client):
    assert client.get("/api/events", auth=AUTH).json() == []
    assert client.get("/api/events?date=2026-01-01", auth=AUTH).json() == []
    assert client.get("/api/events?date=nonsense", auth=AUTH).status_code == 400


def test_missing_media_is_a_404_not_a_crash(client):
    assert client.get("/media/clip/999", auth=AUTH).status_code == 404
    assert client.get("/media/thumb/999", auth=AUTH).status_code == 404


def test_unknown_camera_is_rejected(client):
    assert client.get("/api/timeline?camera=ghost", auth=AUTH).status_code == 404
    assert client.get("/hls/ghost/index.m3u8", auth=AUTH).status_code == 404


def test_mediamtx_being_down_gives_502_not_500(client):
    assert client.get("/api/timeline?camera=fake", auth=AUTH).status_code == 502
    assert client.get("/hls/fake/index.m3u8", auth=AUTH).status_code == 502
    archive = client.get(
        "/archive.mp4?camera=fake&start=2026-01-01T00:00:00%2B00:00&duration=10", auth=AUTH
    )
    assert archive.status_code == 502


def test_archive_rejects_an_absurd_duration(client):
    response = client.get(
        "/archive.mp4?camera=fake&start=2026-01-01T00:00:00%2B00:00&duration=99999", auth=AUTH
    )
    assert response.status_code == 422


def test_database_is_created_on_startup(client):
    assert (importlib.import_module("nvr.__main__").DATA_DIR / "events.db").exists()
