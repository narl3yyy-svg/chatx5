"""Connect wake and stale-link usability checks."""

import unittest
from unittest.mock import MagicMock, patch

from chatx5.core.messaging import MessagingBackend


class _LinkStub:
    ACTIVE = "active"

    def __init__(self, status=ACTIVE):
        self.status = status


class ConnectWakeTests(unittest.TestCase):
    def _messaging(self):
        m = MessagingBackend.__new__(MessagingBackend)
        m.peer_links = {}
        m.links = {}
        m._link_peer_hashes = {}
        m.config_dir = None
        m.my_dest_hash = "b" * 32
        m.shutdown_requested = False
        m.running = True
        m.active_link = None
        m.active_peer_hash = None
        return m

    def test_peer_link_usable_requires_healthy_interface_and_path(self):
        m = self._messaging()
        peer = "a" * 32
        link = _LinkStub()
        m._peer_link_active = MagicMock(return_value=True)
        m._link_for_peer = MagicMock(return_value=link)
        m._find_active_link_for_peer = MagicMock(return_value=None)
        m.dest_hash_for = MagicMock(return_value=peer)
        m._link_interface_healthy = MagicMock(return_value=False)
        m._peer_has_path = MagicMock(return_value=True)

        usable, adopt = m._peer_link_usable(peer)
        self.assertFalse(usable)
        self.assertIs(adopt, link)

        m._link_interface_healthy.return_value = True
        m._peer_has_path.return_value = False
        usable, adopt = m._peer_link_usable(peer)
        self.assertFalse(usable)

        m._peer_has_path.return_value = True
        usable, adopt = m._peer_link_usable(peer)
        self.assertTrue(usable)
        self.assertIs(adopt, link)

    @patch("chatx5.core.messaging.backend.physical_lan_reachable", return_value=True)
    def test_user_initiated_connect_wakes_lan_peer(self, _lan):
        m = self._messaging()
        peer = "a" * 32
        m.hashes_equivalent = MagicMock(return_value=False)
        m._peer_lan_ip_usable = MagicMock(return_value=True)
        m._peer_lan_recently_unreachable = MagicMock(return_value=False)
        m.clear_user_disconnected = MagicMock()
        m.dest_hash_for = MagicMock(side_effect=lambda h: (h or "").replace(":", ""))
        m._teardown_other_peer_links = MagicMock()
        m._wake_peer = MagicMock(return_value=True)
        m._teardown_stale_peer_links = MagicMock(return_value=1)
        m._peer_link_active = MagicMock(return_value=False)
        m._identity_for_hash = MagicMock(return_value=None)
        m._wait_for_identity = MagicMock(return_value=(None, peer))

        m._connect_to_locked(peer, peer_ip="192.168.1.10", peer_port=8742, user_initiated=True)

        m._wake_peer.assert_called_once()
        m._teardown_stale_peer_links.assert_called_once()

    @patch("chatx5.core.messaging.backend.physical_lan_reachable", return_value=True)
    def test_wake_peer_http_failure_does_not_mark_unreachable(self, _lan):
        m = self._messaging()
        m._peer_lan_unreachable = {}
        m._interrupted = MagicMock(return_value=False)
        m._request_peer_connect = MagicMock(return_value=False)
        m._request_peer_announce = MagicMock(return_value=False)

        ok = m._wake_peer("10.0.5.10", 8742, m.my_dest_hash)

        self.assertFalse(ok)
        self.assertNotIn("10.0.5.10", m._peer_lan_unreachable)

    @patch("chatx5.core.messaging.backend.physical_lan_reachable", return_value=True)
    def test_lan_connect_waits_inbound_after_udp_quick_fail(self, _lan):
        """Initiator must not skip inbound wait when UDP quick connect fails."""
        m = self._messaging()
        peer = "f4b541432b50d2fb8b60a9dcdbec8ae8"
        ident = MagicMock()
        ident.hash = bytes.fromhex(peer)
        m._peer_lan_unreachable = {}
        m.hashes_equivalent = MagicMock(return_value=False)
        m._peer_lan_ip_usable = MagicMock(return_value=True)
        m._peer_lan_recently_unreachable = MagicMock(return_value=False)
        m.clear_user_disconnected = MagicMock()
        m.dest_hash_for = MagicMock(side_effect=lambda h: (h or "").replace(":", ""))
        m._teardown_other_peer_links = MagicMock()
        m._teardown_stale_peer_links = MagicMock(return_value=0)
        m._teardown_mismatched_links = MagicMock(return_value=0)
        m._identity_for_hash = MagicMock(return_value=ident)
        m.register_peer_mapping = MagicMock()
        m._find_active_link_for_peer = MagicMock(return_value=None)
        m._peer_link_usable = MagicMock(return_value=(False, None))
        m._peer_link_active = MagicMock(return_value=False)
        m._ensure_runtime_serial_transport = MagicMock()
        m._lan_transport_ready = MagicMock(return_value=True)
        m._serial_transport_ready = MagicMock(return_value=True)
        m._should_prefer_serial_connect = MagicMock(return_value=False)
        m._tcp_connect_ready = MagicMock(return_value=False)
        m._udp_connect_ready = MagicMock(return_value=True)
        m._prime_udp_path = MagicMock(return_value=True)
        m._establish_outbound_link = MagicMock(return_value=False)
        m._prime_lan_path = MagicMock()
        m._peer_has_path = MagicMock(return_value=False)
        m._wake_peer = MagicMock(return_value=False)
        m._wait_for_peer_link = MagicMock(return_value=True)
        m._link_for_peer = MagicMock(return_value=_LinkStub())
        m._finish_connect = MagicMock(return_value=True)
        m.is_user_disconnected = MagicMock(return_value=False)

        with patch("chatx5.core.messaging.backend.RNS.Destination") as dest_cls:
            dest_cls.return_value.hash = bytes.fromhex(peer)
            m._connect_to_locked(
                peer,
                peer_ip="10.0.5.10",
                peer_port=8742,
                user_initiated=True,
                prefer_transport="lan",
            )

        m._wait_for_peer_link.assert_called()
        m._finish_connect.assert_called()


if __name__ == "__main__":
    unittest.main()