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


if __name__ == "__main__":
    unittest.main()