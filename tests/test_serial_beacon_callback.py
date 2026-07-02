"""Serial RNS announce must also send LAN beacon carrying serial_hash."""

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from chatx5.core.messaging.announce import AnnounceMixin


class _Backend(AnnounceMixin):
    def __init__(self):
        self.config_dir = "/tmp"
        self.on_after_serial_announce = None
        self._connect_in_progress = False
        self._failover_in_progress = False
        self.destination_serial = MagicMock()
        self.identity_serial = MagicMock()
        self.display_name = "test"

    def _has_active_transfer(self):
        return False

    def _serial_transport_ready(self):
        return True

    def ensure_serial_runtime(self):
        return True

    def _announce_payload(self, include_lan_ip=True):
        return b'{"app":"chatx5","name":"test"}'

    def _announce_on_interface(self, iface, app_data=None):
        return True


class SerialBeaconCallbackTests(unittest.TestCase):
    def test_burst_serial_invokes_after_callback(self):
        backend = _Backend()
        calls = []
        backend.on_after_serial_announce = lambda: calls.append(1)
        with patch("chatx5.core.messaging.announce._serial_iface_online", return_value=MagicMock(port="/dev/ttyUSB0")):
            with patch("chatx5.core.messaging.announce.suppress_offline_lan_transports"):
                with patch("chatx5.core.messaging.announce.dedupe_serial_interfaces"):
                    with patch("chatx5.core.messaging.announce.prune_dead_serial_interfaces"):
                        sent = backend._burst_serial_announce(count=1, force=True)
        self.assertEqual(sent, 1)
        self.assertEqual(calls, [1])


if __name__ == "__main__":
    unittest.main()