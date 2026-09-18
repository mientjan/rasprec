"""NVR web app: motion supervisor, event API and browsing UI in one process.

Everything the browser needs is proxied through this single authenticated
port -- live HLS, event clips and archive scrubbing -- so MediaMTX itself
never has to be exposed.
"""

from __future__ import annotations

import asyncio
import logging
import os
import secrets
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from . import config as cfg
from .clips import ClipStore
from .db import Database, Event
from .motion import CameraWorker

logging.basicConfig(
    level=os.environ.get("NVR_LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
)
log = logging.getLogger("nvr")

CONFIG_PATH = os.environ.get("NVR_CONFIG", "/config/cameras.yml")
DATA_DIR = Path(os.environ.get("NVR_DATA", "/data"))
MEDIAMTX_HOST = os.environ.get("MEDIAMTX_HOST", "mediamtx")
PLAYBACK_BASE = f"http://{MEDIAMTX_HOST}:9996"
HLS_BASE = f"http://{MEDIAMTX_HOST}:8888"
RTSP_BASE = f"rtsp://{MEDIAMTX_HOST}:8554"
PURGE_INTERVAL = 3600
WEB_DIR = Path(__file__).parent / "web"

security = HTTPBasic()


class State:
    conf: cfg.Config
    db: Database
    store: ClipStore
    client: httpx.AsyncClient
    def __init__(self):
        self.workers: dict[str, CameraWorker] = {}
        self.tasks: list[asyncio.Task] = []
        self.clip_tasks: list[asyncio.Task] = []
        self.clip_queue: asyncio.Queue = asyncio.Queue(maxsize=32)

    def camera(self, name: str) -> cfg.Camera:
        for cam in self.conf.cameras:
            if cam.name == name:
                return cam
        raise HTTPException(status_code=404, detail="unknown camera")


state = State()


def check_auth(credentials: HTTPBasicCredentials = Depends(security)) -> str:
    user = os.environ.get("NVR_USER", "admin")
    password = os.environ.get("NVR_PASS", "")
    ok_user = secrets.compare_digest(credentials.username, user)
    ok_pass = secrets.compare_digest(credentials.password, password)
    if not (ok_user and ok_pass):
        raise HTTPException(
            status_code=401,
            detail="invalid credentials",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username


async def on_motion(event: Event) -> None:
    event_id = await asyncio.to_thread(state.db.insert, event)
    camera = next(c for c in state.conf.cameras if c.name == event.camera)
    if not camera.record:
        return
    try:
        state.clip_queue.put_nowait((event_id, event, camera))
    except asyncio.QueueFull:
        log.warning("%s: clip queue full; event %d retained without clip; archive unaffected", event.camera, event_id)


def positive_env(name: str, default: int) -> int:
    value = os.environ.get(name, str(default))
    if not value.isascii() or not value.isdecimal() or int(value) <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return int(value)


async def clip_worker():
    while True:
        job = await state.clip_queue.get()
        try:
            await _capture(*job)
        finally:
            state.clip_queue.task_done()


async def _capture(event_id: int, event: Event, camera: cfg.Camera) -> None:
    try:
        await state.store.capture(event_id, event, camera)
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001
        log.exception("%s: clip capture failed for event %d", camera.name, event_id)


async def purge_loop() -> None:
    while True:
        try:
            await state.store.purge(state.conf.clips_retention)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("purge failed")
        await asyncio.sleep(PURGE_INTERVAL)


@asynccontextmanager
async def lifespan(app: FastAPI):
    if not os.environ.get("NVR_PASS"):
        raise SystemExit("NVR_PASS is not set -- refusing to start without a web password")

    state.workers = {}
    state.tasks = []
    state.clip_tasks = []
    state.clip_queue = asyncio.Queue(maxsize=positive_env("NVR_CLIP_QUEUE_SIZE", 32))
    clip_workers = positive_env("NVR_CLIP_WORKERS", 2)
    state.conf = cfg.load(CONFIG_PATH)
    state.db = Database(DATA_DIR / "events.db")
    state.store = ClipStore(DATA_DIR, PLAYBACK_BASE, state.db)
    state.client = httpx.AsyncClient(timeout=30.0)

    state.clip_tasks = [asyncio.create_task(clip_worker(), name=f"clip:{i}") for i in range(clip_workers)]
    for camera in state.conf.cameras:
        worker = CameraWorker(camera, f"{RTSP_BASE}/{camera.detect_path}", on_motion)
        state.workers[camera.name] = worker
        state.tasks.append(asyncio.create_task(worker.run(), name=f"motion:{camera.name}"))
    state.tasks.append(asyncio.create_task(purge_loop(), name="purge"))
    log.info("watching %d camera(s)", len(state.conf.cameras))

    try:
        yield
    finally:
        for task in state.tasks:
            task.cancel()
        await asyncio.gather(*state.tasks, return_exceptions=True)
        for task in state.clip_tasks:
            task.cancel()
        await asyncio.gather(*state.clip_tasks, return_exceptions=True)
        while not state.clip_queue.empty():
            state.clip_queue.get_nowait()
            state.clip_queue.task_done()
        state.tasks.clear()
        state.clip_tasks.clear()
        state.workers.clear()
        await state.client.aclose()


app = FastAPI(title="rasprec NVR", lifespan=lifespan, docs_url=None, redoc_url=None)


# --- pages ----------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def index(_: str = Depends(check_auth)) -> HTMLResponse:
    return HTMLResponse((WEB_DIR / "index.html").read_text())


@app.get("/static/{name}")
async def static(name: str, _: str = Depends(check_auth)) -> FileResponse:
    if name not in {"app.js", "style.css"}:
        raise HTTPException(status_code=404, detail="not found")
    return FileResponse(WEB_DIR / name)


# --- api ------------------------------------------------------------------

@app.get("/api/cameras")
async def api_cameras(_: str = Depends(check_auth)) -> list[dict[str, Any]]:
    now = time.time()
    result = []
    for camera in state.conf.cameras:
        worker = state.workers.get(camera.name)
        result.append(
            {
                "name": camera.name,
                "recording": camera.record,
                "detecting": bool(worker and worker.connected),
                "last_frame_age": round(now - worker.last_frame_ts, 1)
                if worker and worker.last_frame_ts
                else None,
            }
        )
    return result


@app.get("/api/events")
async def api_events(
    camera: str | None = None,
    date: str | None = Query(default=None, description="YYYY-MM-DD, local time"),
    limit: int = 100,
    before_id: int | None = None,
    _: str = Depends(check_auth),
) -> list[dict[str, Any]]:
    since = until = None
    if date:
        try:
            day = datetime.strptime(date, "%Y-%m-%d")
        except ValueError:
            raise HTTPException(status_code=400, detail="date must be YYYY-MM-DD") from None
        since = day.timestamp()
        until = (day + timedelta(days=1)).timestamp()
    return await asyncio.to_thread(
        state.db.list, camera, since, until, limit, before_id
    )


async def _upstream(
    method: str, url: str, params: dict[str, Any], follow_redirects: bool = False
) -> httpx.Response:
    """Send a streaming request to MediaMTX, turning outages into a clean 502."""
    request = state.client.build_request(method, url, params=params)
    try:
        return await state.client.send(
            request, stream=True, follow_redirects=follow_redirects
        )
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"mediamtx unreachable: {exc}") from exc


async def _relay(response: httpx.Response):
    try:
        async for chunk in response.aiter_bytes():
            yield chunk
    finally:
        await response.aclose()


@app.get("/api/timeline")
async def api_timeline(camera: str, _: str = Depends(check_auth)) -> JSONResponse:
    """Segments MediaMTX currently holds for a camera."""
    state.camera(camera)
    try:
        response = await state.client.get(f"{PLAYBACK_BASE}/list", params={"path": camera})
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"mediamtx unreachable: {exc}") from exc
    if response.status_code != 200:
        # A camera that has not recorded anything yet is not an error.
        return JSONResponse([])
    return JSONResponse(response.json())


# --- media ----------------------------------------------------------------

def _media_path(event_id: int, key: str) -> Path:
    row = state.db.get(event_id)
    if not row or not row[key]:
        raise HTTPException(status_code=404, detail="not found")
    path = (DATA_DIR / row[key]).resolve()
    if not path.is_relative_to(DATA_DIR.resolve()) or not path.exists():
        raise HTTPException(status_code=404, detail="not found")
    return path


@app.get("/media/clip/{event_id}")
async def media_clip(event_id: int, _: str = Depends(check_auth)) -> FileResponse:
    return FileResponse(_media_path(event_id, "clip"), media_type="video/mp4")


@app.get("/media/thumb/{event_id}")
async def media_thumb(event_id: int, _: str = Depends(check_auth)) -> FileResponse:
    return FileResponse(_media_path(event_id, "thumb"), media_type="image/jpeg")


@app.get("/archive.mp4")
async def archive(
    camera: str,
    start: str = Query(description="RFC3339 timestamp"),
    duration: float = Query(gt=0, le=3600),
    _: str = Depends(check_auth),
) -> StreamingResponse:
    """Cut an arbitrary window out of the continuous recording."""
    state.camera(camera)
    params = {"path": camera, "start": start, "duration": f"{duration:.3f}", "format": "mp4"}
    response = await _upstream("GET", f"{PLAYBACK_BASE}/get", params)
    if response.status_code != 200:
        body = (await response.aread()).decode(errors="replace").strip()
        await response.aclose()
        raise HTTPException(status_code=response.status_code, detail=body or "no recording")
    return StreamingResponse(_relay(response), media_type="video/mp4")


@app.get("/hls/{camera}/{asset:path}")
async def hls(camera: str, asset: str, request: Request, _: str = Depends(check_auth)):
    """Proxy MediaMTX's HLS output so only this port has to be published."""
    state.camera(camera)
    # MediaMTX bounces the first playlist request through a cookie probe whose
    # cookie is Secure + SameSite=None. Behind this proxy on plain HTTP the
    # browser would drop it and loop, so the redirect is resolved server-side.
    response = await _upstream(
        "GET", f"{HLS_BASE}/{camera}/{asset}", dict(request.query_params),
        follow_redirects=True,
    )
    headers = {
        key: value
        for key, value in response.headers.items()
        if key.lower() in {"content-type", "cache-control"}
    }
    return StreamingResponse(_relay(response), status_code=response.status_code, headers=headers)


def main() -> None:
    uvicorn.run(app, host="0.0.0.0", port=8080, log_config=None, access_log=False)


if __name__ == "__main__":
    main()
