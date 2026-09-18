"""Motion detection: a running-background frame differencer fed by ffmpeg.

ffmpeg decodes each camera down to a small grayscale raw stream, so detection
costs a few percent of a core per camera and needs no OpenCV. Everything here
is plain numpy, which keeps the arm64 image small and the build boring.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable, Iterable

import numpy as np

from .config import DETECT_FPS, DETECT_HEIGHT, DETECT_WIDTH, Camera
from .db import Event
from .process import stop_process

log = logging.getLogger("nvr.motion")

FRAME_TIMEOUT = 15.0
RESTART_DELAY_MIN = 2.0
RESTART_DELAY_MAX = 30.0


def build_mask(
    regions: Iterable[Iterable[float]], width: int, height: int
) -> np.ndarray | None:
    """Regions are [x, y, w, h] in percent and mark areas to IGNORE."""
    regions = [list(r) for r in regions]
    if not regions:
        return None
    mask = np.ones((height, width), dtype=bool)
    for x, y, w, h in regions:
        x0 = int(round(x / 100 * width))
        y0 = int(round(y / 100 * height))
        x1 = min(width, x0 + int(round(w / 100 * width)))
        y1 = min(height, y0 + int(round(h / 100 * height)))
        mask[y0:y1, x0:x1] = False
    return mask


class Detector:
    """Feed frames in, get a MotionEvent back on the frame that closes one."""

    def __init__(
        self,
        camera: str,
        settings: dict,
        width: int = DETECT_WIDTH,
        height: int = DETECT_HEIGHT,
    ):
        self.camera = camera
        self.sensitivity = float(settings["sensitivity"])
        self.min_area = float(settings["min_area"])
        self.consecutive = int(settings["consecutive"])
        self.cooldown = float(settings["cooldown"])
        self.min_duration = float(settings["min_duration"])
        self.alpha = float(settings["background_alpha"])
        self.max_duration = float(settings["max_duration"])

        self.mask = build_mask(settings.get("mask") or [], width, height)
        self.total_pixels = int(self.mask.sum()) if self.mask is not None else width * height
        self.total_pixels = max(self.total_pixels, 1)

        self._bg: np.ndarray | None = None
        self._streak = 0
        self._start_ts: float | None = None
        self._last_motion_ts = 0.0
        self._peak = 0.0

    @property
    def active(self) -> bool:
        return self._start_ts is not None

    def reset(self) -> None:
        """Drop the background model -- used when the stream reconnects."""
        self._bg = None
        self._streak = 0

    def update(self, frame: np.ndarray, ts: float) -> Event | None:
        current = frame.astype(np.float32)
        if self._bg is None:
            self._bg = current
            return None

        delta = current - self._bg
        moving = np.abs(delta) > self.sensitivity
        if self.mask is not None:
            moving &= self.mask

        # The background follows the whole frame, including the parts that are
        # moving. Holding those pixels back sounds smarter -- a subject who
        # stops moving would stay detected -- but any region that differs at
        # startup then never gets corrected, and the camera reports motion
        # forever. The cost of updating everywhere is that a subject who stops
        # moving is absorbed after roughly 1/background_alpha frames (~12s at
        # the defaults), which is how motion detection is supposed to behave.
        self._bg += self.alpha * delta

        area = float(moving.sum()) / self.total_pixels * 100.0
        return self._advance(area, ts)

    def _advance(self, area: float, ts: float) -> Event | None:
        if area >= self.min_area:
            self._streak += 1
            self._last_motion_ts = ts
            self._peak = max(self._peak, area)
            if self._start_ts is None and self._streak >= self.consecutive:
                self._start_ts = ts - (self.consecutive - 1) / DETECT_FPS
                log.info("%s: motion started (%.2f%% of frame)", self.camera, area)
        else:
            self._streak = 0

        if self._start_ts is not None and ts - self._start_ts >= self.max_duration:
            boundary = self._start_ts + self.max_duration
            event = Event(self.camera, self._start_ts, boundary, self._peak)
            # Continue a sustained event at the exact boundary; do not wait for quiet.
            if area >= self.min_area:
                self._start_ts = boundary
                self._peak = area
            else:
                self._start_ts = None
                self._peak = 0.0
                self._streak = 0
            return event

        if self._start_ts is not None and ts - self._last_motion_ts >= self.cooldown:
            return self._close()
        return None

    def _close(self) -> Event | None:
        assert self._start_ts is not None
        event = Event(
            camera=self.camera,
            start_ts=self._start_ts,
            end_ts=self._last_motion_ts,
            peak_area=self._peak,
        )
        self._start_ts = None
        self._peak = 0.0
        self._streak = 0
        if event.duration < self.min_duration:
            log.debug("%s: discarding %.1fs event", self.camera, event.duration)
            return None
        log.info("%s: motion ended after %.1fs", self.camera, event.duration)
        return event

    def flush(self) -> Event | None:
        """Close an in-progress event, e.g. on shutdown or stream loss."""
        return self._close() if self._start_ts is not None else None


def ffmpeg_args(url: str) -> list[str]:
    return [
        "ffmpeg",
        "-hide_banner",
        "-loglevel", "warning",
        "-rtsp_transport", "tcp",
        "-threads", "1",
        "-i", url,
        "-filter_threads", "1",
        "-an",
        "-vf", f"fps={DETECT_FPS},scale={DETECT_WIDTH}:{DETECT_HEIGHT}",
        "-pix_fmt", "gray",
        "-f", "rawvideo",
        "-",
    ]


class CameraWorker:
    """Keeps one ffmpeg reader alive per camera and forwards its events."""

    def __init__(
        self,
        camera: Camera,
        source_url: str,
        on_event: Callable[[Event], Awaitable[None]],
    ):
        self.camera = camera
        self.source_url = source_url
        self.on_event = on_event
        self.detector = Detector(camera.name, camera.motion)
        self.frame_bytes = DETECT_WIDTH * DETECT_HEIGHT
        self.connected = False
        self.last_frame_ts = 0.0

    async def run(self) -> None:
        delay = RESTART_DELAY_MIN
        while True:
            try:
                started = time.monotonic()
                await self._session()
                if time.monotonic() - started >= FRAME_TIMEOUT:
                    delay = RESTART_DELAY_MIN
            except asyncio.CancelledError:
                self.connected = False
                raise
            except Exception as exc:  # noqa: BLE001 - a dead reader must not kill the app
                log.warning("%s: reader failed (%s)", self.camera.name, type(exc).__name__)

            # Losing the stream ends any open event; the background model is
            # stale by the time we reconnect, so start it over.
            self.connected = False
            await self._emit(self.detector.flush())
            self.detector.reset()

            log.info("%s: reconnecting in %.0fs", self.camera.name, delay)
            await asyncio.sleep(delay)
            delay = min(delay * 2, RESTART_DELAY_MAX)

    async def _session(self) -> None:
        proc = await asyncio.create_subprocess_exec(
            *ffmpeg_args(self.source_url),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=self.frame_bytes * 4,
        )
        stderr_task = asyncio.create_task(self._log_stderr(proc.stderr))
        try:
            assert proc.stdout is not None
            while True:
                chunk = await asyncio.wait_for(proc.stdout.readexactly(self.frame_bytes), FRAME_TIMEOUT)
                ts = time.time()
                self.connected = True
                self.last_frame_ts = ts
                frame = np.frombuffer(chunk, dtype=np.uint8).reshape(
                    DETECT_HEIGHT, DETECT_WIDTH
                )
                await self._emit(self.detector.update(frame, ts))
        except asyncio.IncompleteReadError:
            log.info("%s: stream ended", self.camera.name)
        finally:
            self.connected = False
            stderr_task.cancel()
            await asyncio.gather(stderr_task, return_exceptions=True)
            await stop_process(proc)

    async def _emit(self, event: Event | None) -> None:
        if event is None:
            return
        try:
            await self.on_event(event)
        except Exception:  # noqa: BLE001
            log.exception("%s: event handler failed", self.camera.name)

    async def _log_stderr(self, stream: asyncio.StreamReader | None) -> None:
        if stream is None:
            return
        while True:
            line = await stream.readline()
            if not line:
                return
            log.warning("%s: ffmpeg reported a stream error", self.camera.name)
