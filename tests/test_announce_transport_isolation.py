"""LAN-only announce must not fall back to USB serial."""

import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from chatx5.core.messaging import MessagingBackend


class AnnounceTransportIsolationTests(unittest.TestCase):
    def _backend(self):
        ident = MagicMock()
        ident.hash = bytes.fromhex("a" * 32)
        backend = MessagingBackend(identity=ident, config_dir=tempfile.mkdtemp())
        backend.destination = MagicMock()
        return backend

    def test_silent_announce_lan_only_skips_serial_when_lan_down(self):
        backend = self._backend()
        with patch(
            "chatx5.core.messaging.announce.physical_lan_reachable",
            return_value=False,
        ):
            with patch.object(backend, "_burst_serial_announce") as burst:
                backend._silent_announce(also_serial=False)
                burst.assert_not_called()

    def test_silent_announce_can_still_use_serial_when_also_serial_true(self):
        backend = self._backend()
        with patch(
            "chatx5.core.messaging.announce.physical_lan_reachable",
            return_value=False,
        ):
            with patch.object(backend, "_serial_transport_ready", return_value=True):
                with patch.object(backend, "_burst_serial_announce", return_value=1) as burst:
                    backend._silent_announce(also_serial=True)
                    burst.assert_called_once()

    def test_burst_serial_without_callback_skips_lan_beacon(self):
        backend = self._backend()
        backend.on_after_serial_announce = None
        with patch.object(backend, "_serial_transport_ready", return_value=True):
            with patch.object(backend, "ensure_serial_runtime", return_value=True):
                with patch("chatx5.core.messaging.announce._serial_iface_online", return_value=MagicMock(port="/dev/ttyUSB0")):
                    with patch("chatx5.core.messaging.announce.suppress_offline_lan_transports"):
                        with patch("chatx5.core.messaging.announce.dedupe_serial_interfaces"):
                            with patch("chatx5.core.messaging.announce.prune_dead_serial_interfaces"):
                                with patch.object(backend, "_announce_on_interface", return_value=True) as announce:
                                    sent = backend._burst_serial_announce(count=1, force=True)
        self.assertEqual(sent, 1)
        announce.assert_called_once()

    def test_silent_announce_lan_only_skips_serial_when_lan_up(self):
        backend = self._backend()
        udp_iface = MagicMock()
        with patch(
            "chatx5.core.messaging.announce.physical_lan_reachable",
            return_value=True,
        ):
            with patch(
                "chatx5.core.messaging.announce.configured_udp_lan_enabled",
                return_value=True,
            ):
                with patch(
                    "chatx5.core.messaging.announce.configured_tcp_lan_enabled",
                    return_value=False,
                ):
                    with patch(
                        "chatx5.core.messaging.announce.lan_discovery_configured",
                        return_value=True,
                    ):
                        with patch(
                            "chatx5.core.messaging.announce.udp_interface_online",
                            return_value=udp_iface,
                        ):
                            with patch.object(backend, "_burst_serial_announce") as burst:
                                with patch.object(backend, "_announce_on_interface", return_value=True):
                                    backend._silent_announce(also_serial=False)
                                burst.assert_not_called()


if __name__ == "__main__":
    unittest.main()