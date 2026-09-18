"""Load and validate cameras.yml.

The user edits a single YAML file. Secrets stay in the environment: any
``${VAR}`` in the file is expanded from the process environment (docker
compose loads .env into it), so cameras.yml itself can be shared safely.
"""

from __future__ import annotations

import math
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
VAR_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
DURATION_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([smhd])\s*$")

# Detection runs on a fixed-size grayscale frame so the raw byte stream from
# ffmpeg has a known frame length and the buffers can be preallocated.
DETECT_WIDTH = 480
DETECT_HEIGHT = 270
DETECT_FPS = 4

MOTION_DEFAULTS: dict[str, Any] = {
    "sensitivity": 25,  # per-pixel |frame - background| threshold, 0-255
    "min_area": 0.4,  # % of frame that must differ to count as motion
    "consecutive": 3,  # motion frames needed before an event opens
    "cooldown": "15s",  # quiet time before an event closes
    "pre_roll": "5s",
    "post_roll": "10s",
    "max_duration": "5m",  # split sustained motion into bounded clips
    "min_duration": "1s",  # discard events shorter than this
    "background_alpha": 0.02,  # how fast the background image adapts
    "mask": [],  # [[x, y, w, h], ...] in % of frame, ignored regions
}


class ConfigError(Exception):
    pass


def parse_duration(value: Any) -> float:
    """'15s' / '10m' / '72h' / '30d' / 90 -> seconds."""
    if isinstance(value, (int, float)):
        if isinstance(value, bool) or not math.isfinite(value) or value < 0:
            raise ConfigError("duration must be finite and nonnegative")
        return float(value)
    match = DURATION_RE.match(str(value))
    if not match:
        raise ConfigError(f"bad duration {value!r}; use forms like 30s, 10m, 72h, 7d")
    scale = {"s": 1, "m": 60, "h": 3600, "d": 86400}[match.group(2)]
    result = float(match.group(1)) * scale
    if not math.isfinite(result):
        raise ConfigError("duration must be finite")
    return result


def go_duration(seconds: float) -> str:
    """MediaMTX parses Go durations, which have no day unit -- emit seconds."""
    return f"{int(round(seconds))}s"


def expand_vars(data: Any, env: dict[str, str] | None = None) -> Any:
    """Replace ``${VAR}`` in every string value of a parsed config tree.

    Expanding after the YAML is parsed, rather than on the raw text, means a
    ``${VAR}`` mentioned in a comment is left alone and a password containing
    a colon or a newline cannot break the document.
    """
    env = os.environ if env is None else env
    missing: list[str] = []

    def sub(match: re.Match[str]) -> str:
        name = match.group(1)
        if not env.get(name):
            missing.append(name)
            return ""
        return env[name]

    def walk(node: Any) -> Any:
        if isinstance(node, str):
            return VAR_RE.sub(sub, node)
        if isinstance(node, dict):
            return {key: walk(value) for key, value in node.items()}
        if isinstance(node, list):
            return [walk(item) for item in node]
        return node

    result = walk(data)
    if missing:
        names = ", ".join(sorted(set(missing)))
        raise ConfigError(f"unset environment variables referenced in config: {names}")
    return result


@dataclass
class Camera:
    name: str
    url: str
    sub_url: str | None = None
    motion: dict[str, Any] = field(default_factory=dict)
    record: bool = True

    @property
    def detect_path(self) -> str:
        """MediaMTX path the motion detector reads from."""
        return f"{self.name}_sub" if self.sub_url else self.name


@dataclass
class Config:
    cameras: list[Camera]
    continuous_retention: float  # seconds
    clips_days: float
    segment_duration: float  # seconds

    @property
    def clips_retention(self) -> float:
        return self.clips_days * 86400


def merge_motion(defaults: dict[str, Any], override: dict[str, Any] | None) -> dict[str, Any]:
    merged = {**MOTION_DEFAULTS, **defaults, **(override or {})}
    unknown = set(merged) - set(MOTION_DEFAULTS)
    if unknown:
        raise ConfigError(f"unknown motion setting(s): {', '.join(sorted(unknown))}")

    for key in ("cooldown", "pre_roll", "post_roll", "min_duration", "max_duration"):
        merged[key] = parse_duration(merged[key])
    if not isinstance(merged["consecutive"], int) or isinstance(merged["consecutive"], bool):
        raise ConfigError("consecutive must be a positive integer")
    merged["sensitivity"] = float(merged["sensitivity"])
    merged["min_area"] = float(merged["min_area"])
    merged["consecutive"] = int(merged["consecutive"])
    merged["background_alpha"] = float(merged["background_alpha"])

    values = ("sensitivity", "min_area", "background_alpha")
    if any(not math.isfinite(merged[k]) for k in values):
        raise ConfigError("motion settings must be finite")
    if not (0 <= merged["sensitivity"] <= 255 and 0 < merged["min_area"] <= 100
            and 0 < merged["background_alpha"] <= 1 and merged["consecutive"] > 0
            and 0 < merged["max_duration"] <= 3600
            and merged["min_duration"] <= merged["max_duration"]):
        raise ConfigError("motion settings out of range (max_duration must be > 0 and <= 1h)")

    mask = merged["mask"] or []
    for region in mask:
        if len(region) != 4 or any(not 0 <= float(v) <= 100 for v in region):
            raise ConfigError(f"mask region {region!r} must be [x, y, w, h] in percent (0-100)")
    merged["mask"] = [[float(v) for v in region] for region in mask]
    return merged


def load(path: str | Path, env: dict[str, str] | None = None) -> Config:
    try:
        return _load(path, env)
    except (ValueError, TypeError, AttributeError, OverflowError, yaml.YAMLError) as exc:
        # Avoid including malformed configuration values, which may contain secrets.
        raise ConfigError("invalid configuration types or values") from exc


def _load(path: str | Path, env: dict[str, str] | None = None) -> Config:
    data = yaml.safe_load(Path(path).read_text()) or {}
    if not isinstance(data, dict):
        raise ConfigError("config root must be a mapping")
    data = expand_vars(data, env)

    retention = data.get("retention") or {}
    motion_defaults = data.get("motion") or {}
    entries = data.get("cameras") or []
    if not entries:
        raise ConfigError("no cameras defined")

    cameras: list[Camera] = []
    seen: set[str] = set()
    for entry in entries:
        name = str(entry.get("name", "")).strip()
        if not NAME_RE.match(name):
            raise ConfigError(
                f"camera name {name!r} must be lowercase letters, digits, '_' or '-'"
            )
        if name in seen:
            raise ConfigError(f"duplicate camera name {name!r}")
        seen.add(name)

        url = entry.get("url")
        if not url or not str(url).startswith(("rtsp://", "rtsps://", "http://", "https://")):
            raise ConfigError(f"camera {name!r} needs an rtsp:// (or http://) url")

        cameras.append(
            Camera(
                name=name,
                url=str(url),
                sub_url=str(entry["sub_url"]) if entry.get("sub_url") else None,
                motion=merge_motion(motion_defaults, entry.get("motion")),
                record=bool(entry.get("record", True)),
            )
        )

    generated = [cam.detect_path for cam in cameras if cam.sub_url]
    if any(name in seen for name in generated) or len(generated) != len(set(generated)):
        raise ConfigError("generated substream path collides with a camera name")
    continuous = parse_duration(retention.get("continuous", "72h"))
    segment = parse_duration(retention.get("segment", "10m"))
    clips_days = float(retention.get("clips_days", 30))
    if not (continuous >= 1 and 1 <= segment <= continuous and math.isfinite(clips_days * 86400) and clips_days > 0):
        raise ConfigError("retention must be finite and positive; segment must be >= 1s and <= continuous")
    return Config(
        cameras=cameras,
        continuous_retention=continuous,
        clips_days=clips_days,
        segment_duration=segment,
    )
