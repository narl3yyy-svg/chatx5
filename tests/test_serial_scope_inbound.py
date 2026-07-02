"""Serial inbound scope when RNS has not attached an interface yet."""

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from chatx5.core.messaging import MessagingBackend

ARCH_SERIAL = "6a7b8318cc9eaae9a6a7b8318cc9eaae9"[:32]
ARCH_LAN = "4f599af06822d2b14f599af06822d2b1"[:32]


class _FakeIdentity:
    def __init__(self, ident_hex):
        self.hash = bytes.fromhex(ident_hex)


class _FakeLink:
    ACTIVE = 2

    def __init__(self, link_id_hex, remote_ident_hex):
        self.link_id = bytes.fromhex(link_id_hex.ljust(32, "0")[:32])
        self._remote = _FakeIdentity(remote_ident_hex)
        self.status = self.ACTIVE
        self.attached_interface = None
        self.interface = None
        self.parent_interface = None

    def get_remote_identity(self):
        return self._remote


def _backend(resolver):
    ident = _FakeIdentity("a" * 32)
    backend = MessagingBackend(
        identity=ident,
        config_dir="/tmp/chatx5-serial-scope",
        peer_scope_checker=lambda peer_hash, link=None: False,
        peer_transport_resolver=resolver,
    )
    backend.running = True
    backend.my_dest_hash = "b" * 32
    return backend


class SerialInboundScopeTests(unittest.TestCase):
    def test_serial_endpoint_allowed_without_attached_interface(self):
        def resolver(peer, via=None):
            if via == "serial" or peer == ARCH_SERIAL:
                return {"hash": ARCH_SERIAL, "via": "serial", "name": "Arch"}
            return None

        backend = _backend(resolver)
        link = _FakeLink("aa" * 16, ARCH_SERIAL)
        with patch.object(backend, "_serial_transport_ready", return_value=True):
            allowed = backend._peer_allowed_by_scope(ARCH_SERIAL, link=link)
        self.assertTrue(allowed)

    def test_lan_hash_not_treated_as_serial_endpoint(self):
        def resolver(peer, via=None):
            if via in ("lan", "rns"):
                return {"hash": ARCH_LAN, "via": "rns", "ip": "10.0.30.112"}
            if via == "serial":
                return {"hash": ARCH_SERIAL, "via": "serial"}
            if peer == ARCH_LAN:
                return {"hash": ARCH_LAN, "via": "rns", "ip": "10.0.30.112"}
            return None

        backend = _backend(resolver)
        self.assertFalse(backend._peer_hash_is_serial_endpoint(ARCH_LAN))
        self.assertTrue(backend._peer_hash_is_serial_endpoint(ARCH_SERIAL))


if __name__ == "__main__":
    unittest.main()