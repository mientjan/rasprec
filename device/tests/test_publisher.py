"""Ephemeral TLS fixtures only. Production publisher has no insecure-TLS switch."""

import importlib.util
import json
import socket
import ssl
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

SPEC = importlib.util.spec_from_file_location(
    "publisher", Path(__file__).resolve().parents[1] / "scripts/publish.py"
)
publisher = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(publisher)


def certificates(root, hostname="localhost", expired=False):
    root.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    ca_name = x509.Name(
        [x509.NameAttribute(NameOID.COMMON_NAME, "Synthetic fixture CA")]
    )
    ca = (
        x509.CertificateBuilder()
        .subject_name(ca_name)
        .issuer_name(ca_name)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=3))
        .not_valid_after(now + timedelta(days=3))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(False, False, False, False, False, True, True, False, False),
            critical=True,
        )
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()),
            critical=False,
        )
        .sign(ca_key, hashes.SHA256())
    )
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    cert = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, hostname)]))
        .issuer_name(ca_name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=2))
        .not_valid_after(now + timedelta(days=-1 if expired else 1))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.SubjectAlternativeName([x509.DNSName(hostname)]), critical=False
        )
        .add_extension(
            x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False
        )
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()),
            critical=False,
        )
        .sign(ca_key, hashes.SHA256())
    )
    for name, data in [
        ("ca.pem", ca.public_bytes(serialization.Encoding.PEM)),
        ("cert.pem", cert.public_bytes(serialization.Encoding.PEM)),
        (
            "key.pem",
            key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            ),
        ),
    ]:
        (root / name).write_bytes(data)
    return root / "ca.pem", root / "cert.pem", root / "key.pem"


@contextmanager
def echo_server(cert, key):
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    listener.settimeout(5)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(cert, key)

    def run():
        try:
            raw, _ = listener.accept()
            raw.settimeout(5)
            with raw, ctx.wrap_socket(raw, server_side=True) as stream:
                while block := stream.recv(65536):
                    stream.sendall(block)
        except (OSError, ssl.SSLError):
            pass

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    try:
        yield listener.getsockname()[1]
    finally:
        listener.close()
        worker.join(timeout=6)


def test_verified_tls_bridge_roundtrip(tmp_path):
    ca, cert, key = certificates(tmp_path)
    with echo_server(cert, key) as port:
        with publisher.TLSBridge("localhost", port, str(ca)) as bridge:
            with socket.create_connection(
                ("127.0.0.1", bridge.port_local), timeout=5
            ) as connection:
                connection.sendall(b"synthetic-video")
                assert connection.recv(100) == b"synthetic-video"
        assert bridge.process.poll() is not None


@pytest.mark.parametrize("failure", ["untrusted", "hostname", "expired"])
def test_certificate_failures_never_open_plaintext_listener(tmp_path, failure):
    ca, cert, key = certificates(
        tmp_path,
        hostname="wrong.example.invalid" if failure == "hostname" else "localhost",
        expired=failure == "expired",
    )
    cafile = None if failure == "untrusted" else str(ca)
    with echo_server(cert, key) as port:
        bridge = publisher.TLSBridge("localhost", port, cafile)
        with pytest.raises(ssl.SSLCertVerificationError):
            with bridge:
                pytest.fail("Unverified connection must never be usable")
        assert not hasattr(bridge, "port_local")


def test_tls_verification_is_required():
    context = publisher.tls_context()
    assert context.check_hostname is True
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.minimum_version >= ssl.TLSVersion.TLSv1_2


def valid_config():
    return dict(
        server_host="cameras.example.com",
        camera="garage",
        publish_token="A" * 43,
        source_url="rtsp://view:synthetic-password@127.0.0.1:8554/cam",
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"server_host": "http://example.com"},
        {"source_url": "rtsp://192.168.1.1/cam"},
        {"source_url": "http://127.0.0.1/cam"},
        {"publish_token": "short"},
        {"camera": "../other"},
        {"server_port": True},
        {"server_port": 0},
        {"insecure": True},
        {"ca_file": "/tmp/untrusted"},
    ],
)
def test_invalid_or_insecure_configuration_rejected(changes):
    with pytest.raises(ValueError):
        publisher.validate({**valid_config(), **changes})


def test_private_config_and_sanitized_error(tmp_path, monkeypatch, capsys):
    config = tmp_path / "publisher.json"
    config.write_text(json.dumps(valid_config()))
    config.chmod(0o644)
    with pytest.raises(ValueError):
        publisher.load(config)
    config.chmod(0o600)
    assert publisher.load(config)["server_port"] == 1936
    monkeypatch.setenv("CREDENTIALS_DIRECTORY", str(tmp_path))
    monkeypatch.setattr(publisher.sys, "argv", ["publish.py"])

    def fail(data):
        raise RuntimeError(data["source_url"] + data["publish_token"])

    monkeypatch.setattr(publisher, "publish", fail)
    assert publisher.main() == 1
    stderr = capsys.readouterr().err
    assert "synthetic-password" not in stderr and "A" * 43 not in stderr
    assert "RuntimeError" in stderr
