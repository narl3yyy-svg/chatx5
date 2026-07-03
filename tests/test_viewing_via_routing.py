"""UI viewing_via must route sends/transfers to the correct transport hash."""

import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from chatx5.core.contacts import save_contact
from chatx5.core.messaging import MessagingBackend
from chatx5.web.server import ChatWebServer

LAN = "cfd5323badc198b6904eaa21195f2b50"
SERIAL = "10ac3618bf9abc2f096701f02969729c"
CONTACT_KEY = LAN


class _FakeIdentity:
    def __init__(self, ident_hex):
        self.hash = bytes.fromhex(ident_hex)


class _FakeLink:
    ACTIVE = 2

    def __init__(self, link_id_hex, iface=None, remote_dest=None):
        self.link_id = bytes.fromhex(link_id_hex.ljust(32, "0")[:32])
        self.status = self.ACTIVE
        self.rtt = 0.01
        self.attached_interface = iface
        self._remote_dest = remote_dest

    def get_remote_identity(self):
        if not self._remote_dest:
            return None
        return _FakeIdentity(self._remote_dest)


def _iface(family):
    m = MagicMock()
    m.family = family
    return m


class ViewingViaRoutingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="chatx5-via-")
        save_contact(
            self.tmp,
            LAN,
            name="Arch",
            via="lan",
            lan_hash=LAN,
            serial_hash=SERIAL,
            ip="10.0.30.112",
        )

    def _server(self):
        server = ChatWebServer.__new__(ChatWebServer)
        server.config_dir = self.tmp
        server._ui_state = {"viewing_peer": CONTACT_KEY, "viewing_via": None, "hidden": False}
        server.discovery = MagicMock()
        server.messaging = None
        server._peer_dest_hash = lambda h: (h or "").strip().replace(":", "")
        server._peer_is_current = lambda h: True
        server._resolve_current_peer_hash = lambda h, ip=None, prefer_via=None: h
        server._scoped_peers = lambda: []
        return server

    def test_resolve_send_target_serial(self):
        server = self._server()
        got = server._resolve_send_target(CONTACT_KEY, prefer_via="serial")
        self.assertEqual(got, SERIAL)

    def test_resolve_send_target_lan(self):
        server = self._server()
        got = server._resolve_send_target(CONTACT_KEY, prefer_via="lan")
        self.assertEqual(got, LAN)

    def test_queue_target_hash_uses_viewing_via(self):
        server = self._server()
        server._ui_state["viewing_via"] = "serial"
        self.assertEqual(server._queue_target_hash(), SERIAL)
        server._ui_state["viewing_via"] = "lan"
        self.assertEqual(server._queue_target_hash(), LAN)

    def test_peers_share_contact(self):
        ident = _FakeIdentity("a" * 32)
        backend = MessagingBackend(identity=ident, config_dir=self.tmp)
        self.assertTrue(backend._peers_share_contact(LAN, SERIAL))
        self.assertFalse(backend._peers_share_contact(LAN, "b" * 32))

    def test_queue_matches_contact_alias_hashes(self):
        ident = _FakeIdentity("a" * 32)
        backend = MessagingBackend(identity=ident, config_dir=self.tmp)
        entry = {"target_hash": LAN}
        self.assertTrue(backend._queue_matches_target(entry, SERIAL))
        self.assertTrue(backend._queue_matches_target(entry, LAN))

    def test_best_transfer_link_prefers_explicit_transport(self):
        ident = _FakeIdentity("a" * 32)
        backend = MessagingBackend(identity=ident, config_dir=self.tmp)
        backend.running = True
        serial_iface = _iface("serial")
        udp_iface = _iface("udp")
        serial_link = _FakeLink("11" * 16, serial_iface, SERIAL)
        lan_link = _FakeLink("22" * 16, udp_iface, LAN)
        backend.links[serial_link.link_id] = serial_link
        backend.links[lan_link.link_id] = lan_link
        backend._link_peer_hashes[serial_link.link_id] = SERIAL
        backend._link_peer_hashes[lan_link.link_id] = LAN
        backend.peer_links[backend._link_map_key(SERIAL, "serial")] = serial_link
        backend.peer_links[backend._link_map_key(LAN, "lan")] = lan_link
        backend._session_peer_hash = SERIAL
        backend._session_transport = "serial"

        resolver = lambda h, via=None: {
            "hash": h,
            "via": "serial" if h == SERIAL else "rns",
            "ip": "10.0.30.112" if h == LAN else "",
        }
        backend.peer_transport_resolver = resolver

        with patch("chatx5.core.messaging.backend.interface_family", side_effect=lambda i: (
            "serial" if i is serial_iface else "udp"
        )):
            with patch.object(backend, "_link_interface_healthy", return_value=True):
                with patch.object(backend, "_dest_hash_from_identity", side_effect=lambda l: (
                    SERIAL if l is serial_link else LAN
                )):
                    lan_pick = backend._best_transfer_link(LAN, prefer_transport="lan")
                    serial_pick = backend._best_transfer_link(SERIAL, prefer_transport="serial")
        self.assertIs(lan_pick, lan_link)
        self.assertIs(serial_pick, serial_link)


if __name__ == "__main__":
    unittest.main()