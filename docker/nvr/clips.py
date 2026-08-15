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

log = logging.getLogger("nvr.clips")

# The archive is written in ~1s parts; give the last one time to land.
SETTLE_SECONDS = 2.0
DOWNLOAD_TIMEOUT = 120.0
THUMB_WIDTH = 320


class ClipStore:
    def __init__(self, data_dir: str | Path, playback_base: str, db: Database):
        self.root = Path(data_dir)
        self.clips_dir = self.root / "clips"
        self.thumbs_dir = self.root / "thumbs"
        self.playback_base = playback_base.rstrip("/")
        self.db = db

    def _relative(self, kind: str, camera: str, event_id: int, ts: float, ext: str) -> Path:
        moment = datetime.fromtimestamp(ts)
        return Path(kind, camera, moment.strftime("%Y-%m-%d"), f"{moment:%H%M%S}-{event_id}.{ext}")

    async def capture(self, event_id: int, event: Event, camera: Camera) -> None:
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

        if not await self._download(camera.name, start, duration, clip_abs):
            return

        thumb_rel = self._relative("thumbs", camera.name, event_id, event.start_ts, "jpg")
        thumb_abs = self.root / thumb_rel
        thumb_abs.parent.mkdir(parents=True, exist_ok=True)
        has_thumb = await self._thumbnail(clip_abs, thumb_abs, duration / 2)

        await asyncio.to_thread(
            self.db.set_media,
            event_id,
            str(clip_rel),
            str(thumb_rel) if has_thumb else None,
        )
        log.info("%s: saved clip %s (%.1fs)", camera.name, clip_rel, duration)

    async def _download(self, path: str, start: float, duration: float, dest: Path) -> bool:
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
                        body = (await response.aread()).decode(errors="replace").strip()
                        log.warning(
                            "%s: playback returned %s: %s", path, response.status_code, body
                        )
                        return False
                    with partial.open("wb") as handle:
                        async for chunk in response.aiter_bytes():
                            handle.write(chunk)
        except (httpx.HTTPError, OSError) as exc:
            log.warning("%s: clip download failed: %s", path, exc)
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
            "-hide_banner", "-loglevel", "error", "-y",
            "-i", str(clip),
            "-ss", f"{max(offset, 0):.3f}",
            "-frames:v", "1",
            "-vf", f"scale={THUMB_WIDTH}:-2",
            "-q:v", "5",
            str(dest),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0 or not dest.exists():
            log.warning("thumbnail failed for %s: %s", clip, stderr.decode(errors="replace").strip())
            return False
        return True

    async def purge(self, retention_seconds: float) -> int:
        """Delete clips (and their event rows) past the retention window."""
        cutoff = time.time() - retention_seconds
        removed = 0
        while True:
            rows = await asyncio.to_thread(self.db.expired, cutoff)
            if not rows:
                break
            for row in rows:
                for key in ("clip", "thumb"):
                    if row[key]:
                        (self.root / row[key]).unlink(missing_ok=True)
            await asyncio.to_thread(self.db.delete, [row["id"] for row in rows])
            removed += len(rows)
            if len(rows) < 500:
                break
        if removed:
            log.info("purged %d expired event(s)", removed)
        self._prune_empty_dirs()
        return removed

    def _prune_empty_dirs(self) -> None:
        for base in (self.clips_dir, self.thumbs_dir):
            if not base.exists():
                continue
            for day_dir in sorted(base.glob("*/*"), reverse=True):
                if day_dir.is_dir() and not any(day_dir.iterdir()):
                    day_dir.rmdir()
