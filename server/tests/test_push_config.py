import base64
import hashlib

import pytest
import yaml
from nvr import config, render_config


def write_config(tmp_path, entries):
    path = tmp_path / "cameras.yml"
    path.write_text(yaml.safe_dump({"cameras": entries}))
    return path


def test_push_tls_and_scoped_auth(tmp_path):
    tokens = {"ONE": "A" * 43, "TWO": "B" * 43}
    path = write_config(
        tmp_path,
        [
            {"name": "one", "source": "push", "publish_token": "${ONE}"},
            {"name": "two", "source": "push", "publish_token": "${TWO}"},
        ],
    )
    conf = config.load(path, tokens)
    assert tokens["ONE"] not in repr(conf)
    result = render_config.build(conf)
    assert result["rtmp"] is True and result["rtmpEncryption"] == "strict"
    assert result["rtmpsAddress"] == ":1936"
    assert result["paths"]["one"]["source"] == "publisher"
    assert result["paths"]["one"]["overridePublisher"] is False
    reader, first, second = result["authInternalUsers"]
    assert reader["ips"] == ["172.30.88.10/32"]
    assert {p["action"] for p in reader["permissions"]} == {"read", "playback"}
    assert first["permissions"] == [{"action": "publish", "path": "one"}]
    assert second["permissions"] == [{"action": "publish", "path": "two"}]
    assert first["user"] == "camera_one"
    assert (
        first["pass"]
        == "sha256:"
        + base64.b64encode(hashlib.sha256(tokens["ONE"].encode()).digest()).decode()
    )
    assert tokens["ONE"] not in yaml.safe_dump(result)


@pytest.mark.parametrize(
    "entry",
    [
        {"name": "one", "source": "other"},
        {"name": "one", "source": "push"},
        {"name": "one", "source": "push", "publish_token": "short"},
        {
            "name": "one",
            "source": "push",
            "publish_token": "A" * 43,
            "url": "rtsp://host",
        },
        {
            "name": "one",
            "source": "push",
            "publish_token": "A" * 43,
            "sub_url": "rtsp://host",
        },
        {"name": "one", "url": "rtsp://host", "publish_token": "A" * 43},
    ],
)
def test_ambiguous_or_insecure_push_rejected(tmp_path, entry):
    with pytest.raises(config.ConfigError):
        config.load(write_config(tmp_path, [entry]))


def test_shared_credentials_rejected(tmp_path):
    entries = [
        {"name": name, "source": "push", "publish_token": "A" * 43}
        for name in ["one", "two"]
    ]
    with pytest.raises(config.ConfigError, match="different"):
        config.load(write_config(tmp_path, entries))
