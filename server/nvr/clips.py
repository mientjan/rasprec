"""Turn a motion event into a clip by cutting it out of the archive.

Nothing is captured twice: MediaMTX already writes a continuous recording, and
its playback server can hand back any ``[start, duration]`` window as MP4. A
clip is therefore an extract plus a thumbnail, with its own longer retention.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

from .config import Camera
from .db import Database, Event
from .process import stop_process

log = logging.getLogger("nvr.clips")

# The archive is written in ~1s parts; give the last one time to land.
SETTLE_SECONDS = 2.0
DOWNLOAD_TIMEOUT = 120.0
THUMB_WIDTH = 320
THUMB_TIMEOUT = 30.0
CAPTURE_TIMEOUT = 300.0


class ClipStore:
    def __init__(self, data_dir: str | Path, playback_base: str, db: Database):
        self.root = Path(data_dir)
        self.clips_dir = self.root / "clips"
        self.thumbs_dir = self.root / "thumbs"
        self.images_dir = self.root / "images"
        self.playback_base = playback_base.rstrip("/")
        self.db = db

    def _relative(
        self, kind: str, camera: str, event_id: int, ts: float, ext: str
    ) -> Path:
        moment = datetime.fromtimestamp(ts, tz=timezone.utc)
        return Path(
            kind,
            camera,
            moment.strftime("%Y-%m-%d"),
            f"{moment:%H%M%S}-{event_id}.{ext}",
        )

    async def capture(self, event_id: int, event: Event, camera: Camera) -> None:
        async with asyncio.timeout(CAPTURE_TIMEOUT):
            await self._capture(event_id, event, camera)

    async def _capture(self, event_id: int, event: Event, camera: Camera) -> None:
        pre_roll = float(camera.motion["pre_roll"])
        post_roll = float(camera.motion["post_roll"])
        start = event.start_ts - pre_roll
        duration = event.duration + pre_roll + post_roll

        wait = (event.end_ts + post_roll + SETTLE_SECONDS) - time.time()
        if wait > 0:
            await asyncio.sleep(wait)

        clip_rel = self._relative("clips", camera.name, event_id, event.start_ts, "mp4")
        clip_abs = self.root / clip_rel
        clip_abs.parent.mkdir(parents=True, exist_ok=True)

        thumb_rel = self._relative(
            "thumbs", camera.name, event_id, event.start_ts, "jpg"
        )
        thumb_abs = self.root / thumb_rel
        thumb_abs.parent.mkdir(parents=True, exist_ok=True)
        image_rel = self._relative(
            "images", camera.name, event_id, event.start_ts, "jpg"
        )
        image_abs = self.root / image_rel
        image_abs.parent.mkdir(parents=True, exist_ok=True)
        committed = False
        try:
            if not await self._download(camera.name, start, duration, clip_abs):
                return
            has_image = await self._still(str(clip_abs), image_abs, pre_roll)
            has_thumb = await self._thumbnail(clip_abs, thumb_abs, pre_roll)
            if not has_image:
                raise RuntimeError("Image extraction failed")
            # Small SQLite transaction: no cancellation point between commit and ownership flag.
            self.db.set_media(
                event_id,
                str(clip_rel),
                str(thumb_rel) if has_thumb else None,
                str(image_rel),
            )
            committed = True
            log.info("%s: saved clip %s (%.1fs)", camera.name, clip_rel, duration)
        finally:
            if not committed:
                clip_abs.unlink(missing_ok=True)
                thumb_abs.unlink(missing_ok=True)
                image_abs.unlink(missing_ok=True)
            clip_abs.with_suffix(".mp4.part").unlink(missing_ok=True)

    async def _download(
        self, path: str, start: float, duration: float, dest: Path
    ) -> bool:
        params = {
            "path": path,
            "start": datetime.fromtimestamp(start, tz=timezone.utc).isoformat(),
            "duration": f"{duration:.3f}",
            "format": "mp4",
        }
        partial = dest.with_suffix(dest.suffix + ".part")
        try:
            async with httpx.AsyncClient(timeout=DOWNLOAD_TIMEOUT) as client:
                async with client.stream(
                    "GET", f"{self.playback_base}/get", params=params
                ) as response:
                    if response.status_code != 200:
                        log.warning(
                            "%s: playback returned %s", path, response.status_code
                        )
                        return False
                    with partial.open("wb") as handle:
                        async for chunk in response.aiter_bytes():
                            handle.write(chunk)
        except (httpx.HTTPError, OSError) as exc:
            log.warning("%s: clip download failed (%s)", path, type(exc).__name__)
            partial.unlink(missing_ok=True)
            return False

        if partial.stat().st_size == 0:
            partial.unlink(missing_ok=True)
            log.warning("%s: playback returned an empty clip", path)
            return False
        partial.rename(dest)
        return True

    async def _thumbnail(self, clip: Path, dest: Path, offset: float) -> bool:
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-ss",
            f"{max(offset, 0):.3f}",
            "-threads",
            "1",
            "-i",
            str(clip),
            "-threads",
            "1",
            "-filter_threads",
            "1",
            "-frames:v",
            "1",
            "-vf",
            f"scale={THUMB_WIDTH}:-2",
            "-q:v",
            "5",
            str(dest),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        success = False
        try:
            _, stderr = await asyncio.wait_for(proc.communicate(), THUMB_TIMEOUT)
            success = proc.returncode == 0 and dest.exists() and dest.stat().st_size > 0
            if not success:
                log.warning("thumbnail extraction failed")
            return success
        except TimeoutError:
            log.warning("thumbnail timed out for %s", clip)
            return False
        finally:
            await stop_process(proc)
            if not success:
                dest.unlink(missing_ok=True)

    async def purge(self, retention_seconds: float) -> int:
        """Delete clips (and their event rows) past the retention window."""
        cutoff = time.time() - retention_seconds
        removed = 0
        while True:
            rows = await asyncio.to_thread(self.db.expired, cutoff)
            if not rows:
                break
            for row in rows:
                for key in ("clip", "thumb", "image"):
                    if row[key]:
                        target = (self.root / row[key]).resolve()
                        if target.is_relative_to(self.root.resolve()):
                            target.unlink(missing_ok=True)
            await asyncio.to_thread(self.db.delete, [row["id"] for row in rows])
            removed += len(rows)
            if len(rows) < 500:
                break
        if removed:
            log.info("purged %d expired event(s)", removed)
        self._prune_empty_dirs()
        return removed

    def _prune_empty_dirs(self) -> None:
        for base in (self.clips_dir, self.thumbs_dir, self.images_dir):
            if not base.exists():
                continue
            for day_dir in sorted(base.glob("*/*"), reverse=True):
                if day_dir.is_dir() and not any(day_dir.iterdir()):
                    day_dir.rmdir()

    async def _still(
        self, source: str, dest: Path, offset: float = 0, live: bool = False
    ) -> bool:
        args = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y"]
        if live:
            args += ["-rtsp_transport", "tcp"]
        else:
            args += ["-ss", f"{max(offset, 0):.3f}"]
        args += [
            "-threads",
            "1",
            "-i",
            source,
            "-threads",
            "1",
            "-frames:v",
            "1",
            "-q:v",
            "2",
            str(dest),
        ]
        proc = await asyncio.create_subprocess_exec(
            *args, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL
        )
        success = False
        try:
            await asyncio.wait_for(proc.wait(), THUMB_TIMEOUT)
            success = proc.returncode == 0 and dest.exists() and dest.stat().st_size > 0
            return success
        finally:
            await stop_process(proc)
            if not success:
                dest.unlink(missing_ok=True)

    async def photo(self, event_id: int, event: Event, source: str) -> None:
        image_rel = self._relative(
            "images", event.camera, event_id, event.start_ts, "jpg"
        )
        thumb_rel = self._relative(
            "thumbs", event.camera, event_id, event.start_ts, "jpg"
        )
        dest, thumb = self.root / image_rel, self.root / thumb_rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        thumb.parent.mkdir(parents=True, exist_ok=True)
        committed = False
        try:
            if not await self._still(source, dest, live=True):
                raise RuntimeError("Camera image unavailable")
            has_thumb = await self._thumbnail(dest, thumb, 0)
            self.db.set_media(
                event_id, None, str(thumb_rel) if has_thumb else None, str(image_rel)
            )
            committed = True
        finally:
            if not committed:
                dest.unlink(missing_ok=True)
                thumb.unlink(missing_ok=True)

    def recover_files(self) -> None:
        # Runs once before workers start. Remove crash leftovers, not archive segments.
        with self.db._connect() as conn:
            referenced = {
                str((self.root / value).resolve())
                for row in conn.execute("SELECT clip, thumb, image FROM events")
                for value in row
                if value
            }
        for base in (self.clips_dir, self.thumbs_dir, self.images_dir):
            for path in base.rglob("*"):
                if path.is_file() and str(path.resolve()) not in referenced:
                    path.unlink()
