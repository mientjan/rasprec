import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import yaml
from nvr import clips, motion
from nvr import config as cfg
from nvr.__main__ import positive_env
from nvr.db import Event


def test_long_motion_splits_without_gaps():
    settings = cfg.merge_motion({}, {"max_duration": "2s", "consecutive": 1})
    detector = motion.Detector("cam", settings)
    events = [e for i in range(25) if (e := detector._advance(10, i / 4))]
    assert [(e.start_ts, e.end_ts) for e in events] == [(0, 2), (2, 4), (4, 6)]
    assert detector.active


@pytest.mark.parametrize(
    "key,value",
    [
        ("sensitivity", float("nan")),
        ("min_area", float("inf")),
        ("max_duration", "0s"),
        ("max_duration", "2h"),
        ("consecutive", 0),
        ("consecutive", 1.5),
        ("cooldown", -1),
        ("background_alpha", 0),
    ],
)
def test_motion_validation(key, value):
    with pytest.raises(cfg.ConfigError):
        cfg.merge_motion({}, {key: value})


@pytest.mark.parametrize(
    "retention",
    [
        {"continuous": 0},
        {"clips_days": float("nan")},
        {"segment": "0.1s"},
        {"segment": "100d"},
        {"clips_days": -1},
    ],
)
def test_retention_validation(tmp_path, retention):
    path = tmp_path / "c.yml"
    path.write_text(
        yaml.safe_dump(
            {
                "retention": retention,
                "cameras": [{"name": "cam", "url": "rtsp://host/cam"}],
            }
        )
    )
    with pytest.raises(cfg.ConfigError):
        cfg.load(path)


@pytest.mark.parametrize("reverse", [False, True])
def test_path_collision(tmp_path, reverse):
    cameras = [
        {"name": "cam", "url": "rtsp://host/main", "sub_url": "rtsp://host/sub"},
        {"name": "cam_sub", "url": "rtsp://host/main"},
    ]
    path = tmp_path / "c.yml"
    path.write_text(yaml.safe_dump({"cameras": cameras[::-1] if reverse else cameras}))
    with pytest.raises(cfg.ConfigError, match="collides"):
        cfg.load(path)


@pytest.mark.parametrize("value", ["0", "-1", "inf", "2.5", ""])
def test_queue_env_validation(monkeypatch, value):
    monkeypatch.setenv("NVR_CLIP_WORKERS", value)
    with pytest.raises(ValueError):
        positive_env("NVR_CLIP_WORKERS", 2)


class Process:
    returncode = None

    def __init__(self):
        self.stdout = SimpleNamespace(readexactly=self.block)
        self.stderr = SimpleNamespace(readline=self.block)
        self.killed = False
        self.waited = False

    async def block(self, *args):
        await asyncio.Event().wait()

    communicate = block

    def kill(self):
        self.killed = True
        self.returncode = -9

    async def wait(self):
        self.waited = True
        return self.returncode


@pytest.mark.asyncio
async def test_stalled_reader_is_killed_and_marked_disconnected(monkeypatch):
    proc = Process()
    monkeypatch.setattr(asyncio, "create_subprocess_exec", AsyncMock(return_value=proc))
    monkeypatch.setattr(motion, "FRAME_TIMEOUT", 0.01)
    camera = cfg.Camera("cam", "rtsp://host", motion=cfg.merge_motion({}, {}))
    worker = motion.CameraWorker(camera, camera.url, AsyncMock())
    worker.connected = True
    with pytest.raises(TimeoutError):
        await worker._session()
    assert proc.killed and proc.waited and not worker.connected


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel", [False, True])
async def test_thumbnail_cleanup_and_seek_order(monkeypatch, tmp_path, cancel):
    proc = Process()
    create = AsyncMock(return_value=proc)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", create)
    monkeypatch.setattr(clips, "THUMB_TIMEOUT", 0.01 if not cancel else 30)
    store = clips.ClipStore(tmp_path, "http://playback", None)
    dest = tmp_path / "thumb.jpg"
    dest.write_bytes(b"partial")
    task = asyncio.create_task(store._thumbnail(Path("clip.mp4"), dest, 100))
    if cancel:
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    else:
        assert await task is False
    assert proc.killed and proc.waited and not dest.exists()
    args = create.call_args.args
    assert args.index("-ss") < args.index("-i")


@pytest.mark.asyncio
async def test_capture_timeout_removes_partials(monkeypatch, tmp_path):
    monkeypatch.setattr(clips, "CAPTURE_TIMEOUT", 0.01)
    store = clips.ClipStore(tmp_path, "http://playback", None)

    async def download(camera, start, duration, dest):
        dest.with_suffix(".mp4.part").write_bytes(b"partial")
        await asyncio.Event().wait()

    monkeypatch.setattr(store, "_download", download)
    camera = cfg.Camera("cam", "rtsp://host", motion=cfg.merge_motion({}, {}))
    with pytest.raises(TimeoutError):
        await store.capture(1, Event("cam", 0, 1, 10), camera)
    assert not list(tmp_path.rglob("*.part"))
    assert not list(tmp_path.rglob("*.mp4"))


@pytest.mark.asyncio
async def test_queue_overflow_keeps_metadata_and_bounds_work(monkeypatch):
    from nvr import __main__ as main

    old = main.state
    main.state = main.State()
    try:
        camera = cfg.Camera("cam", "rtsp://host")
        main.state.conf = SimpleNamespace(cameras=[camera])
        inserted = []
        main.state.db = SimpleNamespace(
            insert=lambda event: inserted.append(event) or len(inserted)
        )
        main.state.clip_queue = asyncio.Queue(maxsize=2)
        for _ in range(4):
            await main.on_motion(Event("cam", 0, 1, 10))
        assert len(inserted) == 4 and main.state.clip_queue.qsize() == 2
        active = 0
        peak = 0
        release = asyncio.Event()

        async def capture(*args):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            try:
                await release.wait()
            finally:
                active -= 1

        monkeypatch.setattr(main, "_capture", capture)
        tasks = [asyncio.create_task(main.clip_worker()) for _ in range(2)]
        await asyncio.sleep(0)
        assert peak == 2
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        assert active == 0
        camera.record = False
        await main.on_motion(Event("cam", 0, 1, 10))
        assert main.state.clip_queue.empty()
    finally:
        main.state = old


@pytest.mark.asyncio
async def test_repeated_lifespan_cleans_collections(monkeypatch, tmp_path):
    from nvr import __main__ as main

    conf = tmp_path / "c.yml"
    conf.write_text('cameras: [{name: cam, url: "rtsp://host/cam"}]')
    monkeypatch.setattr(main, "CONFIG_PATH", str(conf))
    monkeypatch.setattr(main, "DATA_DIR", tmp_path)
    monkeypatch.setenv("NVR_PASS", "pw")

    async def idle(self):
        await asyncio.Event().wait()

    monkeypatch.setattr(main.CameraWorker, "run", idle)
    for _ in range(2):
        async with main.lifespan(main.app):
            assert len(main.state.workers) == 1
            assert len(main.state.tasks) == 2
            assert len(main.state.clip_tasks) == 2
        assert (
            not main.state.tasks
            and not main.state.clip_tasks
            and not main.state.workers
        )


@pytest.mark.asyncio
async def test_real_child_with_full_pipe_is_reaped():
    import sys

    from nvr.process import stop_process

    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        'import os\nwhile True: os.write(1, b"x"*65536)',
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        limit=1024,
    )
    await asyncio.sleep(0.1)
    await asyncio.wait_for(stop_process(proc), 5)
    assert proc.returncode is not None
