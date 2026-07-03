"""TLS certificate helpers for local HTTPS (--tls / --share)."""

import os
import ssl
import subprocess


def tls_cert_paths(config_dir):
    return (
        os.path.join(config_dir, "tls-cert.pem"),
        os.path.join(config_dir, "tls-key.pem"),
    )


def ensure_self_signed_cert(config_dir, hostname="chatx5"):
    """Create or reuse a 10-year self-signed cert in the config directory."""
    cert_path, key_path = tls_cert_paths(config_dir)
    if os.path.isfile(cert_path) and os.path.isfile(key_path):
        return cert_path, key_path
    os.makedirs(config_dir, exist_ok=True)
    subject = f"/CN={hostname}"
    cmd = [
        "openssl", "req", "-x509", "-newkey", "rsa:4096",
        "-keyout", key_path,
        "-out", cert_path,
        "-days", "3650",
        "-nodes",
        "-subj", subject,
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=60)
    except FileNotFoundError as exc:
        raise RuntimeError(
            "openssl not found — install OpenSSL to use --tls, "
            "or pass --cert and --key"
        ) from exc
    except subprocess.CalledProcessError as exc:
        err = (exc.stderr or b"").decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"openssl cert generation failed: {err or exc}") from exc
    return cert_path, key_path


def build_ssl_context(cert_path, key_path):
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(cert_path, key_path)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    return ctx