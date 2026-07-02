"""Direct serial path seeding for beacon-discovered USB peers."""

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from chatx5.core.lan_rns import seed_serial_path_for_peer

PEER = "6fc871f35bb064726fc871f35bb06472"[:32]


class SerialPathSeedTests(unittest.TestCase):
    def test_seed_installs_one_hop_serial_path(self):
        import RNS

        serial_iface = MagicMock()
        serial_iface.__class__.__name__ = "SerialInterface"
        dest_bytes = bytes.fromhex(PEER)
        with patch(
            "chatx5.core.lan_rns.serial_interface_online",
            return_value=serial_iface,
        ):
            with patch(
                "chatx5.core.lan_rns.interface_is_healthy",
                return_value=True,
            ):
                with patch(
                    "chatx5.core.lan_rns.peer_path_on_family",
                    return_value=None,
                ):
                    restored = seed_serial_path_for_peer(PEER)
        self.assertIs(restored, serial_iface)
        entry = RNS.Transport.path_table.get(dest_bytes)
        self.assertIsNotNone(entry)
        self.assertEqual(len(entry), 7)
        self.assertIsInstance(entry[1], bytes)
        self.assertEqual(entry[2], 1)
        self.assertEqual(entry[4], [])
        self.assertIs(entry[5], serial_iface)
        RNS.Transport.path_table.pop(dest_bytes, None)


if __name__ == "__main__":
    unittest.main()