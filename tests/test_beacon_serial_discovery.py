"""LAN beacon must advertise USB serial endpoints for symmetric dual-transport discovery."""

import os
import sys
import time
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from chatx5.core.discovery import PeerDiscovery
from chatx5.core.lan_beacon import LanBeacon


class BeaconSerialDiscoveryTests(unittest.TestCase):
    def test_beacon_payload_includes_serial_endpoint(self):
        beacon = LanBeacon(
            None,
            "a" * 32,
            serial_hash="b" * 32,
            serial_identity_hash="c" * 32,
            serial_identity_pubkey=b"\x01" * 32,
        )
        import json

        payload = json.loads(beacon._payload().decode("utf-8"))
        self.assertEqual(payload["serial_hash"], "b" * 32)
        self.assertEqual(payload["serial_identity_hash"], "c" * 32)
        self.assertIn("serial_pubkey", payload)

    def test_on_beacon_registers_serial_row_alongside_lan(self):
        disc = PeerDiscovery()
        disc.running = True
        disc.accept_peers = True
        lan_hash = "6d0c4a39e4db8cbd6d0c4a39e4db8cbd"
        serial_hash = "cf3420dbcc9f256ccf3420dbcc9f256c"
        data = {
            "app": "chatx5",
            "hash": lan_hash,
            "identity_hash": "d" * 32,
            "serial_hash": serial_hash,
            "serial_identity_hash": "e" * 32,
            "serial_pubkey": "dGVzdA==",
            "name": "330s",
            "ip": "10.0.30.101",
            "port": 8742,
            "pubkey": "dGVzdA==",
        }
        with patch("chatx5.utils.platform.discovery_scope_ip", return_value="10.0.30.112"):
            with patch("chatx5.core.discovery.serial_discovery_active", return_value=True):
                with patch("chatx5.core.peer_identity.peer_record_from_beacon") as rec:
                    rec.return_value = {
                        "hash": lan_hash,
                        "name": "330s",
                        "via": "beacon",
                        "ip": "10.0.30.101",
                    }
                    with patch(
                        "chatx5.core.discovery.register_identity_from_beacon",
                        return_value=True,
                    ):
                        ok = disc._on_beacon(data, "f" * 32, source_ip="10.0.30.101")
                self.assertTrue(ok)
                serial_peer = disc.peer_row(serial_hash, via="serial")
                self.assertIsNotNone(serial_peer)
                self.assertEqual(serial_peer.get("via"), "serial")
                self.assertTrue(serial_peer.get("serial_rns"))
                peers = disc.get_peers(scope_ip="10.0.30.112")
        serial_rows = [p for p in peers if (p.get("via") or "") == "serial"]
        lan_rows = [p for p in peers if (p.get("via") or "") != "serial"]
        self.assertEqual(len(serial_rows), 1)
        self.assertEqual(serial_rows[0]["hash"], serial_hash)
        self.assertEqual(len(lan_rows), 1)
        self.assertEqual(lan_rows[0]["hash"], lan_hash)


    def test_ipless_announce_serial_by_matching_display_name(self):
        """Dual-identity: serial hash + name matches existing LAN row → USB row."""
        disc = PeerDiscovery()
        disc.running = True
        disc.accept_peers = True
        lan_hash = "6d0c4a39e4db8cbd6d0c4a39e4db8cbd"
        serial_hash = "cf3420dbcc9f256ccf3420dbcc9f256c"
        disc.peers[f"{lan_hash}:rns"] = {
            "hash": lan_hash,
            "name": "330s",
            "via": "rns",
            "ip": "10.0.30.101",
            "identity_hash": "a" * 32,
            "last_seen": time.time(),
        }
        peer_hash = bytes.fromhex(serial_hash)
        app_data = b'{"app":"chatx5","name":"330s"}'
        lan_iface = MagicMock()
        with patch("chatx5.core.discovery.announce_packet_receiving_interface", return_value=lan_iface):
            with patch("chatx5.core.discovery.interface_family", return_value="udp"):
                with patch("chatx5.core.discovery.serial_discovery_active", return_value=True):
                    with patch("chatx5.utils.platform.discovery_scope_ip", return_value="10.0.30.112"):
                        disc._on_announce(peer_hash, app_data, announced_identity=None)
        serial_peer = disc.peer_row(serial_hash, via="serial")
        self.assertIsNotNone(serial_peer)
        self.assertEqual(serial_peer.get("via"), "serial")


if __name__ == "__main__":
    unittest.main()