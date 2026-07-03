"""Saved contacts must connect without stale-hash rejection when offline."""

import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from chatx5.core.contacts import save_contact


class _ServerStub:
    config_dir = ""
    discovery = None
    messaging = None
    active_peer = None
    websockets = None
    _loop = None
    _ui_state = {}

    def _peer_dest_hash(self, h):
        return (h or "").replace(":", "").strip().lower()

    def _discovery_scope_ip(self):
        return None

    def _scoped_peers(self):
        return []

    def _peers_equivalent(self, a, b):
        return (a or "").replace(":", "") == (b or "").replace(":", "")

    def _schedule_contacts_broadcast(self):
        pass


class SavedContactConnectTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.server = _ServerStub()
        self.server.config_dir = self.tmp
        self.server.discovery = MagicMock()
        self.server.discovery.peer_is_current.return_value = False
        from chatx5.web.messaging_bridge import MessagingBridgeMixin

        for name in (
            "_find_saved_contact",
            "_refresh_saved_contact_from_discovery",
            "_peer_is_current",
            "_peer_matches_transport",
            "_saved_contact_connect_hash",
            "_resolve_current_peer_hash",
            "_contact_hash_for_transport",
        ):
            setattr(self.server, name, MessagingBridgeMixin.__dict__[name].__get__(
                self.server, _ServerStub,
            ))

    def test_saved_contact_treated_as_current_when_not_in_discovery(self):
        stale = "b9033de66c42b63e98d7a18f74db63aa"
        save_contact(self.tmp, stale, name="330s", via="lan", ip="10.0.30.101")
        self.assertTrue(self.server._peer_is_current(stale))

    def test_saved_contact_resolves_to_lan_hash_offline(self):
        lan = "3428352734b6dcc09472039c449e65b1"
        save_contact(self.tmp, lan, name="Arch", via="lan", ip="10.0.30.112")
        resolved = self.server._resolve_current_peer_hash(lan, peer_ip="10.0.30.112")
        self.assertEqual(resolved, lan)


if __name__ == "__main__":
    unittest.main()