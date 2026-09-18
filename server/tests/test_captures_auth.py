import asyncio
import sqlite3
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from nvr import __main__ as main
from nvr.auth import COOKIE, digest, hasher
from nvr.clips import ClipStore
from nvr.config import Camera, merge_motion
from nvr.db import SCHEMA, Database, Event


@pytest.fixture
def client(tmp_path, monkeypatch):
    config = tmp_path / "cameras.yml"
    config.write_text('cameras: [{name: cam, url: "rtsp://example.invalid/cam"}]')
    monkeypatch.setattr(main, "CONFIG_PATH", str(config))
    monkeypatch.setattr(main, "DATA_DIR", tmp_path / "data")
    monkeypatch.setenv("NVR_AUTH_MODE", "session")
    monkeypatch.setenv("NVR_PUBLIC_ORIGIN", "https://testserver")
    monkeypatch.setenv("NVR_PASSWORD_HASH", hasher.hash("synthetic-test-password"))
    monkeypatch.setenv("NVR_USER", "test-admin")

    async def idle(*args):
        await asyncio.Event().wait()

    monkeypatch.setattr(main.CameraWorker, "run", idle)
    monkeypatch.setattr(main, "clip_worker", idle)
    with TestClient(main.app, base_url="https://testserver") as c:
        yield c


def login(client):
    response = client.post(
        "/login",
        json={"username": "test-admin", "password": "synthetic-test-password"},
        headers={"Origin": "https://testserver"},
    )
    assert response.status_code == 200
    return {
        "Origin": "https://testserver",
        "X-CSRF-Token": client.get("/api/session").json()["csrf"],
    }


@pytest.mark.parametrize(
    "url",
    [
        "/api/cameras",
        "/api/events",
        "/api/session",
        "/media/image/1",
        "/media/clip/1",
        "/media/thumb/1",
        "/hls/cam/",
        "/api/timeline?camera=cam",
        "/archive.mp4?camera=cam&start=now&duration=1",
        "/static/app.js",
    ],
)
def test_session_routes_reject_anonymous(client, url):
    assert client.get(url).status_code == 401


def test_login_csrf_logout_and_expiry(client):
    assert client.get("/", follow_redirects=False).status_code == 303
    assert client.post("/login", json={}).status_code == 403
    headers = login(client)
    token = client.cookies.get(COOKIE)
    with main.state.db._connect() as conn:
        assert conn.execute("SELECT token FROM sessions").fetchone()[0] == digest(token)
    assert client.get("/").status_code == 200
    assert client.post("/logout").status_code == 403
    assert client.post("/logout", headers=headers).status_code == 200
    client.cookies.set(COOKIE, token)
    assert client.get("/api/session").status_code == 401
    client.cookies.clear()
    login(client)
    with main.state.db._connect() as conn:
        conn.execute("UPDATE sessions SET expires=0")
    assert client.get("/api/session").status_code == 401


def test_login_cookie_and_rate_limit(client):
    result = client.post(
        "/login",
        headers={"Origin": "https://testserver"},
        json={"username": "test-admin", "password": "synthetic-test-password"},
    )
    cookie = result.headers["set-cookie"].lower()
    assert all(
        value in cookie for value in ["secure", "httponly", "samesite=strict", "path=/"]
    )
    for _ in range(9):
        assert (
            client.post(
                "/login",
                json={"username": "wrong", "password": "wrong"},
                headers={"Origin": "https://testserver"},
            ).status_code
            == 401
        )
    assert (
        client.post(
            "/login", json={}, headers={"Origin": "https://testserver"}
        ).status_code
        == 429
    )


def test_manual_capture_validation_and_queue(client):
    headers = login(client)
    url = "/api/cameras/cam/captures"
    assert client.post(url, json={"kind": "photo"}, headers=headers).status_code == 409
    main.state.workers["cam"].connected = True
    assert client.post(url, json={"kind": "photo"}).status_code == 403
    assert (
        client.post(url, json={"kind": "invalid"}, headers=headers).status_code == 422
    )
    assert (
        client.post(
            "/api/cameras/missing/captures", json={"kind": "photo"}, headers=headers
        ).status_code
        == 404
    )
    for kind in ("photo", "clip"):
        r = client.post(url, json={"kind": kind}, headers=headers)
        assert r.status_code == 202
        row = main.state.db.get(r.json()["id"])
        assert (
            row["source"] == "manual"
            and row["kind"] == kind
            and row["status"] == "pending"
        )
    main.state.conf.cameras[0].record = False
    assert client.post(url, json={"kind": "clip"}, headers=headers).status_code == 409
    for _ in range(main.state.clip_queue.maxsize - 2):
        client.post(url, json={"kind": "photo"}, headers=headers)
    assert client.post(url, json={"kind": "photo"}, headers=headers).status_code == 429
    assert len(client.get("/api/events?source=manual&kind=clip").json()) == 1
    assert client.get("/api/events?since=100&until=10").status_code == 400


def test_image_download_and_path_guard(client):
    login(client)
    db = main.state.db
    event_id = db.insert(Event("cam", 1, 1, 0), kind="photo")
    image = main.DATA_DIR / "image.jpg"
    image.write_bytes(b"synthetic-image")
    db.set_media(event_id, None, None, "image.jpg")
    response = client.get(f"/media/image/{event_id}?download=true")
    assert response.content == b"synthetic-image"
    assert "attachment" in response.headers["content-disposition"]
    db.set_media(event_id, None, None, "../outside.jpg")
    assert client.get(f"/media/image/{event_id}").status_code == 404


def test_additive_migration_recovery_and_cursor(tmp_path):
    path = tmp_path / "db"
    with sqlite3.connect(path) as conn:
        conn.executescript(SCHEMA)
        conn.execute(
            "INSERT INTO events(camera,start_ts,end_ts,duration,created_at) VALUES('cam',1,2,1,1)"
        )
    db = Database(path)
    assert db.get(1)["status"] == "ready"
    a = db.insert(Event("cam", 30, 40, 0))
    b = db.insert(Event("cam", 10, 20, 0))
    c = db.insert(Event("cam", 20, 25, 0))
    assert [r["id"] for r in db.list(before_id=c)] == [b, 1]
    assert not any(r["id"] == a for r in db.expired(time.time()))
    db.recover()
    assert db.get(a)["status"] == "failed"
    assert Database(path).get(1)["camera"] == "cam"


@pytest.mark.asyncio
async def test_capture_images_photo_retention_and_cleanup(tmp_path, monkeypatch):
    db = Database(tmp_path / "db")
    store = ClipStore(tmp_path, "http://example.invalid", db)
    camera = Camera("cam", "rtsp://example.invalid", motion=merge_motion({}, {}))

    async def image(source, dest, *args, **kwargs):
        dest.write_bytes(b"synthetic-jpeg")
        return True

    async def download(camera, start, duration, dest):
        dest.write_bytes(b"synthetic-video")
        return True

    monkeypatch.setattr(store, "_still", image)
    monkeypatch.setattr(store, "_thumbnail", image)
    monkeypatch.setattr(store, "_download", download)
    event = Event("cam", 1, 2, 1)
    clip_id = db.insert(event)
    await store.capture(clip_id, event, camera)
    photo_id = db.insert(event, source="manual", kind="photo")
    await store.photo(photo_id, event, "rtsp://example.invalid")
    assert db.get(clip_id)["clip"] and db.get(clip_id)["image"]
    assert db.get(photo_id)["image"] and db.get(photo_id)["clip"] is None
    junk = store.images_dir / "crash.part"
    junk.write_bytes(b"partial")
    store.recover_files()
    assert not junk.exists()
    assert (tmp_path / db.get(photo_id)["image"]).exists()
    assert await store.purge(1) == 2
    assert not list(store.images_dir.rglob("*.jpg"))


@pytest.mark.asyncio
async def test_failed_capture_status(tmp_path, monkeypatch):
    db = Database(tmp_path / "db")
    old = main.state
    main.state = main.State()
    main.state.db = db
    main.state.store = SimpleNamespace(
        capture=AsyncMock(side_effect=RuntimeError("private upstream details"))
    )
    try:
        event = Event("cam", 1, 2, 1)
        event_id = db.insert(event)
        await main._capture(event_id, event, Camera("cam", "rtsp://example.invalid"))
        assert db.get(event_id)["status"] == "failed"
        assert "private" not in db.get(event_id)["error"]
    finally:
        main.state = old
