"""HTTP/HTTPS requests to peer chatx5 web servers (wake, connect, LAN file fetch)."""

import json
import ssl
from urllib import request as urlrequest


def insecure_ssl_context():
    """Trust self-signed certs for LAN peers running chatx5 --share --tls."""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def peer_url(peer_ip, port, path, scheme="http"):
    port = int(port or 8742)
    path = path if path.startswith("/") else f"/{path}"
    return f"{scheme}://{peer_ip}:{port}{path}"


def peer_request(peer_ip, port, path, *, scheme="http", method="GET", payload=None, timeout=5.0):
    """Return (ok: bool, status: int|None). Raises on unexpected errors when strict."""
    if not peer_ip:
        return False, None
    url = peer_url(peer_ip, port, path, scheme=scheme)
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urlrequest.Request(url, data=data, headers=headers, method=method)
    ctx = insecure_ssl_context() if scheme == "https" else None
    with urlrequest.urlopen(req, timeout=timeout, context=ctx) as resp:
        code = getattr(resp, "status", None) or resp.getcode()
        return 200 <= int(code) < 300, int(code)


def peer_request_with_fallback(
    peer_ip, port, path, *, primary_scheme="http", method="GET", payload=None, timeout=5.0,
):
    """Try primary scheme, then alternate (http↔https) for mixed LAN deployments."""
    schemes = [primary_scheme]
    alt = "https" if primary_scheme == "http" else "http"
    if alt not in schemes:
        schemes.append(alt)
    last_exc = None
    for scheme in schemes:
        try:
            ok, _code = peer_request(
                peer_ip, port, path, scheme=scheme, method=method,
                payload=payload, timeout=timeout,
            )
            if ok:
                return True, scheme
        except Exception as exc:
            last_exc = exc
            continue
    if last_exc:
        raise last_exc
    return False, primary_scheme


def peer_get_bytes(peer_ip, port, path, *, scheme="http", timeout=60.0):
    url = peer_url(peer_ip, port, path, scheme=scheme)
    req = urlrequest.Request(url, method="GET")
    ctx = insecure_ssl_context() if scheme == "https" else None
    with urlrequest.urlopen(req, timeout=timeout, context=ctx) as resp:
        return resp.read(), scheme


def peer_get_with_fallback(peer_ip, port, path, *, primary_scheme="http", timeout=60.0):
    schemes = [primary_scheme]
    alt = "https" if primary_scheme == "http" else "http"
    if alt not in schemes:
        schemes.append(alt)
    last_exc = None
    for scheme in schemes:
        try:
            data, used = peer_get_bytes(peer_ip, port, path, scheme=scheme, timeout=timeout)
            return data, used
        except Exception as exc:
            last_exc = exc
            continue
    if last_exc:
        raise last_exc
    return b"", primary_scheme