import pytest
import yaml

from nvr import config as cfg
from nvr import render_config

SAMPLE = """
retention:
  continuous: 48h
  clips_days: 14
  segment: 5m

motion:
  sensitivity: 30

cameras:
  - name: garage
    url: rtsp://view:${GARAGE_PASS}@garage-pi:8554/cam
  - name: driveway
    url: rtsp://admin:${DRIVEWAY_PASS}@10.0.0.5:554/main
    sub_url: rtsp://admin:${DRIVEWAY_PASS}@10.0.0.5:554/sub
    motion:
      min_area: 1.5
      mask:
        - [0, 0, 100, 20]
"""

ENV = {"GARAGE_PASS": "s3cret", "DRIVEWAY_PASS": "hunter2"}


@pytest.fixture
def conf(tmp_path):
    path = tmp_path / "cameras.yml"
    path.write_text(SAMPLE)
    return cfg.load(path, ENV)


def test_parse_duration_units():
    assert cfg.parse_duration("30s") == 30
    assert cfg.parse_duration("10m") == 600
    assert cfg.parse_duration("72h") == 259200
    assert cfg.parse_duration("7d") == 604800
    assert cfg.parse_duration(45) == 45
    with pytest.raises(cfg.ConfigError):
        cfg.parse_duration("7 weeks")


def test_go_duration_has_no_day_unit():
    # MediaMTX parses Go durations, which reject "3d".
    assert cfg.go_duration(cfg.parse_duration("3d")) == "259200s"


def test_env_expansion(conf):
    assert conf.cameras[0].url == "rtsp://view:s3cret@garage-pi:8554/cam"


def test_vars_in_comments_are_left_alone(tmp_path):
    # Expansion runs on the parsed tree, so documentation like "use ${VAR}"
    # in a comment is not treated as a reference.
    path = tmp_path / "cameras.yml"
    path.write_text("# passwords are written as ${SOME_VAR}\ncameras:\n  - {name: a, url: 'rtsp://h/1'}\n")
    assert cfg.load(path, {}).cameras[0].url == "rtsp://h/1"


def test_password_with_yaml_metacharacters_survives(tmp_path):
    path = tmp_path / "cameras.yml"
    path.write_text("cameras:\n  - {name: a, url: 'rtsp://u:${P}@h/1'}\n")
    conf = cfg.load(path, {"P": "a:b#c d"})
    assert conf.cameras[0].url == "rtsp://u:a:b#c d@h/1"


def test_missing_env_var_is_an_error(tmp_path):
    path = tmp_path / "cameras.yml"
    path.write_text(SAMPLE)
    with pytest.raises(cfg.ConfigError, match="DRIVEWAY_PASS"):
        cfg.load(path, {"GARAGE_PASS": "x"})


def test_retention_and_motion_merge(conf):
    assert conf.continuous_retention == 172800
    assert conf.clips_retention == 14 * 86400
    garage, driveway = conf.cameras
    assert garage.motion["sensitivity"] == 30  # from the global block
    assert garage.motion["min_area"] == cfg.MOTION_DEFAULTS["min_area"]
    assert driveway.motion["min_area"] == 1.5  # per-camera override wins
    assert driveway.motion["cooldown"] == 15.0  # duration strings become seconds


def test_detect_path_prefers_substream(conf):
    garage, driveway = conf.cameras
    assert garage.detect_path == "garage"
    assert driveway.detect_path == "driveway_sub"


@pytest.mark.parametrize(
    "camera, message",
    [
        ({"name": "Garage", "url": "rtsp://x/y"}, "lowercase"),
        ({"name": "ok", "url": "ftp://x/y"}, "rtsp"),
        ({"name": "ok", "url": "rtsp://x/y", "motion": {"nope": 1}}, "unknown motion"),
        ({"name": "ok", "url": "rtsp://x/y", "motion": {"mask": [[0, 0, 100]]}}, "mask region"),
    ],
)
def test_validation_errors(tmp_path, camera, message):
    path = tmp_path / "cameras.yml"
    path.write_text(yaml.safe_dump({"cameras": [camera]}))
    with pytest.raises(cfg.ConfigError, match=message):
        cfg.load(path, {})


def test_duplicate_camera_names_rejected(tmp_path):
    path = tmp_path / "cameras.yml"
    path.write_text(
        yaml.safe_dump({"cameras": [{"name": "a", "url": "rtsp://x/1"},
                                    {"name": "a", "url": "rtsp://x/2"}]})
    )
    with pytest.raises(cfg.ConfigError, match="duplicate"):
        cfg.load(path, {})


def test_rendered_mediamtx_config(conf):
    rendered = render_config.build(conf)

    assert rendered["playback"] is True
    assert rendered["rtmp"] is False and rendered["webrtc"] is False
    assert rendered["pathDefaults"]["rtspTransport"] == "tcp"

    paths = rendered["paths"]
    assert set(paths) == {"garage", "driveway", "driveway_sub"}
    assert paths["garage"]["record"] is True
    assert paths["garage"]["recordDeleteAfter"] == "172800s"
    assert paths["garage"]["recordSegmentDuration"] == "300s"
    assert paths["garage"]["recordPath"].startswith("/recordings/%path/")
    # Detection-only stream is pulled but never written.
    assert paths["driveway_sub"]["record"] is False
    assert "recordPath" not in paths["driveway_sub"]

    # Round-trips as valid YAML with the secrets expanded.
    dumped = yaml.safe_load(yaml.safe_dump(rendered))
    assert dumped["paths"]["garage"]["source"].endswith("@garage-pi:8554/cam")
