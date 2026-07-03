"""Tests for TCP hub relay: remote group chat over port 4242, isolated from LAN/UDP."""

import json
import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from chatx5.core import rns_interfaces as ri
from chatx5.core.lan_rns import interface_family
from chatx5.core.messaging import (
    HUB_GROUP_PEER,
    MESSAGE_TYPE_TEXT,
    ChatMessage,
    MessagingBackend,
    is_hub_peer_hash,
)


class HubServerBindingTests(unittest.TestCase):
    def test_hub_server_listens_on_all_interfaces(self):
        from chatx5.web.server import ChatWebServer

        server = ChatWebServer.__new__(ChatWebServer)
        settings = {
            "hub_role": "server",
            "hub_port": 4242,
            "rns_interfaces": ri.default_interface_list(),
        }
        out = server._apply_hub_settings(settings)
        tcp_srv = next(
            i for i in out["rns_interfaces"]
            if i.get("type") == "TCPServerInterface"
        )
        self.assertEqual(tcp_srv.get("listen_ip"), "0.0.0.0")
        self.assertEqual(tcp_srv.get("listen_port"), 4242)
        self.assertTrue(tcp_srv.get("enabled"))

    def test_hub_server_custom_port(self):
        from chatx5.web.server import ChatWebServer

        server = ChatWebServer.__new__(ChatWebServer)
        settings = {
            "hub_role": "server",
            "hub_port": 54242,
            "rns_interfaces": ri.default_interface_list(),
        }
        out = server._apply_hub_settings(settings)
        tcp_srv = next(
            i for i in out["rns_interfaces"]
            if i.get("type") == "TCPServerInterface"
        )
        self.assertEqual(tcp_srv.get("listen_port"), 54242)


class HubClientRemoteTests(unittest.TestCase):
    def test_hub_client_targets_public_hostname(self):
        from chatx5.web.server import ChatWebServer

        server = ChatWebServer.__new__(ChatWebServer)
        settings = {
            "hub_role": "client",
            "hub_host": "hub.example.com",
            "hub_port": 4242,
            "rns_interfaces": ri.default_interface_list(),
        }
        out = server._apply_hub_settings(settings)
        client = next(
            i for i in out["rns_interfaces"]
            if i.get("type") == "TCPClientInterface"
        )
        self.assertEqual(client.get("target_host"), "hub.example.com")
        self.assertEqual(client.get("target_port"), 4242)
        self.assertTrue(client.get("enabled"))

    def test_hub_client_without_host_leaves_interfaces_unchanged(self):
        from chatx5.web.server import ChatWebServer

        server = ChatWebServer.__new__(ChatWebServer)
        before = ri.default_interface_list()
        settings = {
            "hub_role": "client",
            "hub_host": "",
            "hub_port": 4242,
            "rns_interfaces": before,
        }
        out = server._apply_hub_settings(settings)
        self.assertEqual(
            [i.get("preset") for i in out["rns_interfaces"]],
            [i.get("preset") for i in before],
        )

    def test_hub_client_keeps_tcp_lan_listener(self):
        from chatx5.web.server import ChatWebServer

        server = ChatWebServer.__new__(ChatWebServer)
        settings = {
            "hub_role": "client",
            "hub_host": "203.0.113.50",
            "hub_port": 4242,
            "rns_interfaces": [{
                "id": "tcp-lan",
                "preset": "tcp_lan",
                "type": "TCPServerInterface",
                "enabled": True,
                "listen_ip": "0.0.0.0",
                "listen_port": 4242,
            }],
        }
        out = server._apply_hub_settings(settings)
        tcp_lan = next(
            i for i in out["rns_interfaces"]
            if i.get("preset") == "tcp_lan"
        )
        self.assertTrue(tcp_lan.get("enabled"))


class HubMessageFormatTests(unittest.TestCase):
    def test_chat_message_hub_flag_serializes(self):
        msg = ChatMessage(MESSAGE_TYPE_TEXT, "hello remote hub")
        msg.hub_group = True
        payload = json.loads(msg.to_json())
        self.assertTrue(payload.get("hub"))
        restored = ChatMessage.from_json(msg.to_json())
        self.assertTrue(restored.hub_group)

    def test_regular_message_has_no_hub_flag(self):
        msg = ChatMessage(MESSAGE_TYPE_TEXT, "p2p only")
        payload = json.loads(msg.to_json())
        self.assertNotIn("hub", payload)


class HubRelayIsolationTests(unittest.TestCase):
    def _backend(self, hub_role="server", hub_host="10.0.30.109"):
        ident = MagicMock()
        ident.hash = bytes.fromhex("a" * 32)
        tmp = tempfile.mkdtemp()
        settings_path = os.path.join(tmp, "settings.json")
        with open(settings_path, "w", encoding="utf-8") as fh:
            json.dump({
                "hub_role": hub_role,
                "hub_host": hub_host,
                "hub_port": 4242,
            }, fh)
        backend = MessagingBackend(identity=ident, config_dir=tmp)

        class UDPInterface:
            pass

        class TCPClientInterface:
            target_host = hub_host
            target_port = 4242

        class TCPServerInterface:
            pass

        udp_link = MagicMock()
        udp_link.link_id = "udp1"
        udp_link.mtu = 500
        udp_link.attached_interface = UDPInterface()
        tcp_a = MagicMock()
        tcp_a.link_id = "tcp1"
        tcp_a.mtu = 500
        if hub_role == "server":
            tcp_a.attached_interface = TCPServerInterface()
            tcp_b_iface = TCPServerInterface()
        else:
            tcp_a.attached_interface = TCPClientInterface()
            tcp_b_iface = TCPClientInterface()
        tcp_b = MagicMock()
        tcp_b.link_id = "tcp2"
        tcp_b.mtu = 500
        tcp_b.attached_interface = tcp_b_iface

        backend.peer_links = {
            "b" * 32: udp_link,
            "c" * 32: tcp_a,
            "d" * 32: tcp_b,
        }
        backend.links = {
            "udp1": udp_link,
            "tcp1": tcp_a,
            "tcp2": tcp_b,
        }
        return backend, udp_link, tcp_a, tcp_b

    def test_hub_tcp_peers_excludes_udp_p2p(self):
        backend, _, _, _ = self._backend()
        peers = backend._hub_tcp_linked_peers()
        self.assertEqual(set(peers), {"c" * 32, "d" * 32})

    def test_relay_reaches_all_tcp_clients_not_udp(self):
        backend, _, tcp_a, tcp_b = self._backend()
        msg = MagicMock()
        msg.hub_group = True
        msg.to_json.return_value = '{"hub":true,"type":"text"}'
        with patch("chatx5.core.messaging.backend.RNS.Packet") as pkt:
            backend.relay_hub_message(msg, sender_hash="c" * 32)
            targets = {call.args[0] for call in pkt.call_args_list}
            self.assertEqual(targets, {tcp_b})

    def test_send_hub_message_never_targets_udp_or_tcp_lan_peers(self):
        backend, udp_link, tcp_a, tcp_b = self._backend(hub_role="server")

        class TCPClientInterface:
            target_host = "10.0.30.101"
            target_port = 4242

        lan_link = MagicMock()
        lan_link.link_id = "lan1"
        lan_link.mtu = 500
        lan_link.attached_interface = TCPClientInterface()
        backend.peer_links["e" * 32] = lan_link
        backend.links["lan1"] = lan_link
        with patch("chatx5.core.messaging.backend.RNS.Packet") as pkt:
            backend.send_hub_message(
                "remote group",
                hub_server_mode=True,
            )
            self.assertEqual(pkt.call_count, 2)
            sent_links = {call.args[0] for call in pkt.call_args_list}
            self.assertEqual(sent_links, {tcp_a, tcp_b})
            self.assertNotIn(udp_link, sent_links)

    def test_relay_ignores_non_hub_messages(self):
        backend, _, tcp_a, _ = self._backend()
        msg = MagicMock()
        msg.hub_group = False
        with patch("chatx5.core.messaging.backend.RNS.Packet") as pkt:
            backend.relay_hub_message(msg, sender_hash="c" * 32)
            pkt.assert_not_called()


class HubDefaultsAndSettingsTests(unittest.TestCase):
    def test_default_hub_role_is_off(self):
        from chatx5.web.server import ChatWebServer

        server = ChatWebServer.__new__(ChatWebServer)
        server.config_dir = tempfile.mkdtemp()
        with patch.object(
            ChatWebServer,
            "load_settings",
            wraps=server.load_settings,
        ):
            with patch("builtins.open", side_effect=FileNotFoundError):
                defaults = server.load_settings()
        self.assertEqual(defaults.get("hub_role"), "off")
        self.assertEqual(defaults.get("hub_port"), 4242)
        self.assertEqual(defaults.get("hub_host"), "")

    def test_hub_group_peer_is_not_a_real_dest_hash(self):
        self.assertTrue(is_hub_peer_hash(HUB_GROUP_PEER))
        self.assertFalse(is_hub_peer_hash("a" * 32))


class HubTransportFamilyTests(unittest.TestCase):
    def test_tcp_interface_family_for_hub_links(self):
        class TCPClientInterface:
            pass

        class TCPServerInterface:
            pass

        class UDPInterface:
            pass

        self.assertEqual(interface_family(TCPClientInterface()), "tcp")
        self.assertEqual(interface_family(TCPServerInterface()), "tcp")
        self.assertEqual(interface_family(UDPInterface()), "udp")

    def test_hub_transport_active_when_role_set(self):
        ident = MagicMock()
        ident.hash = bytes.fromhex("a" * 32)
        tmp = tempfile.mkdtemp()
        settings_path = os.path.join(tmp, "settings.json")
        with open(settings_path, "w", encoding="utf-8") as fh:
            json.dump({"hub_role": "client", "hub_host": "1.2.3.4"}, fh)
        backend = MessagingBackend(identity=ident, config_dir=tmp)
        self.assertTrue(backend._hub_transport_active())
        with open(settings_path, "w", encoding="utf-8") as fh:
            json.dump({"hub_role": "off"}, fh)
        self.assertFalse(backend._hub_transport_active())


class HubClientLinkTests(unittest.TestCase):
    def test_fetch_hub_server_hash_from_status_api(self):
        ident = MagicMock()
        ident.hash = bytes.fromhex("a" * 32)
        backend = MessagingBackend(identity=ident, config_dir=tempfile.mkdtemp())
        payload = json.dumps({
            "hub_role": "server",
            "hub_server_hash": "b" * 32,
        }).encode()

        class FakeResp:
            def read(self):
                return payload

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        with patch("chatx5.core.messaging.hub.urlrequest.urlopen", return_value=FakeResp()):
            got = backend._fetch_hub_server_hash_from_peer("10.0.30.112", 8742)
        self.assertEqual(got, "b" * 32)

    def test_fetch_hub_server_registers_identity_from_status(self):
        ident = MagicMock()
        ident.hash = bytes.fromhex("a" * 32)
        backend = MessagingBackend(identity=ident, config_dir=tempfile.mkdtemp())
        dest_hash = "d" * 32
        identity_hash = "e" * 32
        payload = json.dumps({
            "hub_role": "server",
            "hub_server_hash": dest_hash,
            "identity_hash": identity_hash,
            "identity_pubkey": "cHVibGljS2V5",
        }).encode()

        class FakeResp:
            def read(self):
                return payload

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        with patch(
            "chatx5.core.messaging.hub.register_beacon_identity",
            return_value=dest_hash,
            create=True,
        ):
            with patch(
                "chatx5.core.peer_identity.register_beacon_identity",
                return_value=dest_hash,
            ):
                with patch(
                    "chatx5.core.messaging.hub.urlrequest.urlopen",
                    return_value=FakeResp(),
                ):
                    got = backend._fetch_hub_server_hash_from_peer("10.0.30.112", 8742)
        self.assertEqual(got, dest_hash)
        self.assertTrue(backend._identity_for_hash(dest_hash) or backend.dest_hash_for(dest_hash))

    def test_ensure_hub_link_fetches_hash_when_missing(self):
        ident = MagicMock()
        ident.hash = bytes.fromhex("a" * 32)
        tmp = tempfile.mkdtemp()
        settings_path = os.path.join(tmp, "settings.json")
        with open(settings_path, "w", encoding="utf-8") as fh:
            json.dump({
                "hub_role": "client",
                "hub_host": "10.0.30.112",
                "hub_port": 4242,
                "hub_server_hash": "",
            }, fh)
        backend = MessagingBackend(identity=ident, config_dir=tmp)
        backend.running = True
        hub_hash = "c" * 32
        with patch.object(backend, "_hub_tcp_transport_online", return_value=True):
            with patch.object(backend, "_fetch_hub_server_hash_from_peer", return_value=hub_hash):
                with patch.object(backend, "_hub_link_for_peer", return_value=MagicMock()):
                    self.assertTrue(backend.ensure_hub_link())
        with open(settings_path, encoding="utf-8") as fh:
            saved = json.load(fh)
        self.assertEqual(saved.get("hub_server_hash"), hub_hash)


class HubTcpLinkSelectionTests(unittest.TestCase):
    def _backend(self, hub_role="client", hub_host="10.0.30.112"):
        ident = MagicMock()
        ident.hash = bytes.fromhex("a" * 32)
        tmp = tempfile.mkdtemp()
        settings_path = os.path.join(tmp, "settings.json")
        with open(settings_path, "w", encoding="utf-8") as fh:
            json.dump({
                "hub_role": hub_role,
                "hub_host": hub_host,
                "hub_port": 4242,
                "hub_server_hash": "b" * 32,
            }, fh)
        backend = MessagingBackend(identity=ident, config_dir=tmp)

        class UDPInterface:
            pass

        class TCPClientInterface:
            target_host = hub_host
            target_port = 4242

        udp_link = MagicMock()
        udp_link.link_id = "udp1"
        udp_link.mtu = 500
        udp_link.attached_interface = UDPInterface()
        hub_link = MagicMock()
        hub_link.link_id = "hub1"
        hub_link.mtu = 500
        hub_link.attached_interface = TCPClientInterface()
        backend.peer_links = {
            "b" * 32: udp_link,
            "b" * 32 + ":tcp": hub_link,
        }
        backend.links = {"udp1": udp_link, "hub1": hub_link}
        backend._link_peer_hashes = {"udp1": "b" * 32, "hub1": "b" * 32}
        return backend, udp_link, hub_link

    def test_hub_link_for_peer_prefers_tcp_over_udp(self):
        backend, udp_link, hub_link = self._backend()
        got = backend._hub_link_for_peer("b" * 32)
        self.assertIs(got, hub_link)
        self.assertIsNot(got, udp_link)

    def test_send_hub_message_uses_hub_tcp_link_not_udp(self):
        backend, udp_link, hub_link = self._backend(hub_role="client")
        with patch("chatx5.core.messaging.hub.RNS.Packet") as pkt:
            backend.send_hub_message("hello hub")
            self.assertEqual(pkt.call_count, 1)
            self.assertIs(pkt.call_args.args[0], hub_link)
            self.assertIsNot(pkt.call_args.args[0], udp_link)

    def test_send_hub_message_preserves_share_browse_type(self):
        from chatx5.core.messaging.constants import MESSAGE_TYPE_SHARE_BROWSE

        backend, _, hub_link = self._backend(hub_role="client")
        offer = (
            '{"session_id":"abc","token":"tok","root_name":"dl",'
            '"host":"10.0.30.112","port":8742,"hub_group":true}'
        )
        with patch("chatx5.core.messaging.hub.RNS.Packet") as pkt:
            result = backend.send_hub_message(
                offer,
                msg_type=MESSAGE_TYPE_SHARE_BROWSE,
            )
            self.assertTrue(result)
            self.assertEqual(result.msg_type, MESSAGE_TYPE_SHARE_BROWSE)
            payload = pkt.call_args.args[1]
            parsed = __import__("json").loads(payload.decode("utf-8"))
            self.assertEqual(parsed.get("type"), MESSAGE_TYPE_SHARE_BROWSE)
            self.assertTrue(parsed.get("hub"))

    def test_hub_path_connect_ready_for_configured_server(self):
        backend, _, _ = self._backend()
        with patch.object(backend, "_hub_tcp_transport_online", return_value=True):
            self.assertTrue(backend._hub_path_connect_ready("b" * 32))
            self.assertFalse(backend._hub_path_connect_ready("c" * 32))


class HubInboundTcpInterfaceTests(unittest.TestCase):
    def test_server_inbound_tcpclient_counts_as_hub_tcp(self):
        ident = MagicMock()
        ident.hash = bytes.fromhex("a" * 32)
        tmp = tempfile.mkdtemp()
        settings_path = os.path.join(tmp, "settings.json")
        with open(settings_path, "w", encoding="utf-8") as fh:
            json.dump({"hub_role": "server", "hub_port": 4242}, fh)
        backend = MessagingBackend(identity=ident, config_dir=tmp)

        class TCPClientInterface:
            pass

        link = MagicMock()
        link.attached_interface = TCPClientInterface()
        self.assertTrue(backend._link_is_hub_tcp(link))

    def test_server_outbound_tcpclient_not_hub_tcp(self):
        ident = MagicMock()
        ident.hash = bytes.fromhex("a" * 32)
        tmp = tempfile.mkdtemp()
        settings_path = os.path.join(tmp, "settings.json")
        with open(settings_path, "w", encoding="utf-8") as fh:
            json.dump({"hub_role": "server", "hub_port": 4242}, fh)
        backend = MessagingBackend(identity=ident, config_dir=tmp)

        class TCPClientInterface:
            target_host = "10.0.30.101"
            target_port = 4242

        link = MagicMock()
        link.attached_interface = TCPClientInterface()
        self.assertFalse(backend._link_is_hub_tcp(link))

    def test_inbound_tcpserver_on_hub_port_counts_as_hub(self):
        ident = MagicMock()
        ident.hash = bytes.fromhex("a" * 32)
        tmp = tempfile.mkdtemp()
        settings_path = os.path.join(tmp, "settings.json")
        with open(settings_path, "w", encoding="utf-8") as fh:
            json.dump({"hub_role": "server", "hub_port": 4242}, fh)
        backend = MessagingBackend(identity=ident, config_dir=tmp)

        class TCPServerInterface:
            listen_port = 4242

        link = MagicMock()
        link.attached_interface = TCPServerInterface()
        self.assertTrue(backend._inbound_link_is_hub_tcp(link))

    def test_registered_tcp_peer_counts_despite_udp_iface(self):
        ident = MagicMock()
        ident.hash = bytes.fromhex("a" * 32)
        tmp = tempfile.mkdtemp()
        settings_path = os.path.join(tmp, "settings.json")
        with open(settings_path, "w", encoding="utf-8") as fh:
            json.dump({"hub_role": "server", "hub_port": 4242}, fh)
        backend = MessagingBackend(identity=ident, config_dir=tmp)

        class UDPInterface:
            pass

        link = MagicMock()
        link.link_id = "hub1"
        link.attached_interface = UDPInterface()
        peer = "c" * 32
        backend.peer_links = {f"{peer}:tcp": link}
        backend.links = {"hub1": link}
        self.assertTrue(backend._link_is_hub_tcp(link))
        self.assertEqual(backend._hub_tcp_linked_peers(), [peer])

    def test_server_inbound_udp_not_hub_tcp(self):
        ident = MagicMock()
        ident.hash = bytes.fromhex("a" * 32)
        tmp = tempfile.mkdtemp()
        settings_path = os.path.join(tmp, "settings.json")
        with open(settings_path, "w", encoding="utf-8") as fh:
            json.dump({"hub_role": "server", "hub_port": 4242}, fh)
        backend = MessagingBackend(identity=ident, config_dir=tmp)

        class UDPInterface:
            pass

        link = MagicMock()
        link.attached_interface = UDPInterface()
        self.assertFalse(backend._link_is_hub_tcp(link))


class HubInboundScopeTests(unittest.TestCase):
    def _backend(self, hub_role="server"):
        ident = MagicMock()
        ident.hash = bytes.fromhex("a" * 32)
        tmp = tempfile.mkdtemp()
        settings_path = os.path.join(tmp, "settings.json")
        with open(settings_path, "w", encoding="utf-8") as fh:
            json.dump({"hub_role": hub_role, "hub_port": 4242}, fh)
        backend = MessagingBackend(identity=ident, config_dir=tmp)
        backend.peer_scope_checker = lambda *_a, **_k: False
        return backend

    def test_inbound_hub_tcp_unknown_peer_allowed_when_server_online(self):
        backend = self._backend(hub_role="server")

        class _HubLink:
            link_id = "hub1"
            attached_interface = None

        link = _HubLink()
        self.assertTrue(backend._inbound_link_is_hub_tcp(link))
        self.assertTrue(backend._peer_allowed_by_scope("unknown", link=link))

    def test_inbound_hub_tcp_not_allowed_when_hub_off(self):
        backend = self._backend(hub_role="off")
        link = MagicMock()
        link.attached_interface = None
        self.assertFalse(backend._inbound_link_is_hub_tcp(link))
        self.assertFalse(backend._peer_allowed_by_scope("unknown", link=link))


class HubMessageReceivableTests(unittest.TestCase):
    def test_hub_group_receivable_on_lan_link_when_hub_active(self):
        ident = MagicMock()
        ident.hash = bytes.fromhex("a" * 32)
        tmp = tempfile.mkdtemp()
        settings_path = os.path.join(tmp, "settings.json")
        with open(settings_path, "w", encoding="utf-8") as fh:
            json.dump({"hub_role": "client", "hub_port": 4242}, fh)
        backend = MessagingBackend(identity=ident, config_dir=tmp)

        class UDPInterface:
            pass

        link = MagicMock()
        link.attached_interface = UDPInterface()
        msg = MagicMock()
        msg.hub_group = True
        self.assertTrue(backend._hub_message_receivable(msg, link))
        self.assertFalse(backend._hub_message_acceptable(msg, link))

    def test_hub_group_not_receivable_when_hub_off(self):
        ident = MagicMock()
        ident.hash = bytes.fromhex("a" * 32)
        backend = MessagingBackend(identity=ident, config_dir=tempfile.mkdtemp())
        msg = MagicMock()
        msg.hub_group = True
        self.assertFalse(backend._hub_message_receivable(msg, None))


class HubFinalizeInboundTests(unittest.TestCase):
    def test_finalize_hub_tcp_inbound_registers_unknown_peer(self):
        ident = MagicMock()
        ident.hash = bytes.fromhex("a" * 32)
        tmp = tempfile.mkdtemp()
        settings_path = os.path.join(tmp, "settings.json")
        with open(settings_path, "w", encoding="utf-8") as fh:
            json.dump({"hub_role": "server", "hub_port": 4242}, fh)
        backend = MessagingBackend(identity=ident, config_dir=tmp)
        backend.running = True
        backend.peer_scope_checker = lambda *_a, **_k: True
        peer = "f" * 32

        link = MagicMock()
        link.link_id = b"\x03" * 16
        link.attached_interface = None
        link.get_remote_identity.return_value = None
        backend._peer_hash_from_link_identity = MagicMock(return_value=peer)
        backend._notify_link_established = MagicMock()
        backend._schedule_hub_queue_drain = MagicMock()

        got = backend._finalize_hub_tcp_inbound(link, initial_peer="unknown")
        self.assertEqual(got, peer)
        tcp_key = f"{peer}:tcp"
        self.assertIn(tcp_key, backend.peer_links)
        backend._notify_link_established.assert_called_once()


class HubInboundParallelTests(unittest.TestCase):
    def test_hub_tcp_inbound_registers_parallel_tcp_link(self):
        ident = MagicMock()
        ident.hash = bytes.fromhex("a" * 32)
        tmp = tempfile.mkdtemp()
        settings_path = os.path.join(tmp, "settings.json")
        with open(settings_path, "w", encoding="utf-8") as fh:
            json.dump({"hub_role": "server", "hub_port": 4242}, fh)
        backend = MessagingBackend(identity=ident, config_dir=tmp)
        backend.peer_scope_checker = lambda *_a, **_k: True
        backend.running = True
        peer = "f" * 32

        lan_link = MagicMock()
        lan_link.link_id = b"\x01" * 16
        lan_link.status = 1
        backend.active_link = lan_link
        backend.active_peer_hash = peer
        backend.links = {lan_link.link_id: lan_link}
        backend._link_peer_hashes = {lan_link.link_id: peer}
        backend.peer_links = {peer: lan_link}
        backend._link_path_score = lambda _l: 100
        backend._link_interface_healthy = lambda _l: True
        backend._has_active_transfer = lambda: False
        backend._incoming_matches_active_session = lambda _l: False
        backend._handoff_to_link = MagicMock()
        backend._setup_link = MagicMock()
        backend._schedule_hub_queue_drain = MagicMock()
        backend._resolve_remote_peer = MagicMock(return_value=peer)
        backend._peer_destination_hash = MagicMock(return_value=peer)
        backend._resolve_incoming_link_peer = MagicMock(return_value=peer)
        backend._cache_link_peer = MagicMock()
        backend._peer_expected_transport_families = MagicMock(return_value={"lan", "udp"})
        backend.is_user_disconnected = lambda _p: False

        hub_link = MagicMock()
        hub_link.link_id = b"\x02" * 16
        hub_link.attached_interface = None
        hub_link.teardown = MagicMock()

        backend._link_callback(hub_link)

        hub_link.teardown.assert_not_called()
        tcp_key = f"{peer}:tcp"
        self.assertIn(tcp_key, backend.peer_links)
        self.assertIs(backend.peer_links[tcp_key], hub_link)


class HubServerHashUpdateTests(unittest.TestCase):
    def test_maybe_update_hub_hash_only_on_hub_tcp_link(self):
        from chatx5.web.server import ChatWebServer

        server = ChatWebServer.__new__(ChatWebServer)
        server.config_dir = tempfile.mkdtemp()
        settings = {
            "hub_role": "client",
            "hub_server_hash": "",
            "hub_host": "10.0.30.112",
            "hub_port": 4242,
        }
        server.save_settings = MagicMock()
        server.load_settings = MagicMock(return_value=settings)
        server._peer_dest_hash = lambda h: (h or "").replace(":", "")
        server._is_self_hash = lambda h: False

        lan_link = MagicMock()
        lan_link.attached_interface = MagicMock()
        server.messaging = MagicMock()
        server.messaging._link_is_hub_tcp.return_value = False

        server._maybe_update_hub_server_hash("b" * 32, link=lan_link)
        server.save_settings.assert_not_called()

        hub_link = MagicMock()
        server.messaging._link_is_hub_tcp.return_value = True
        server._maybe_update_hub_server_hash("b" * 32, link=hub_link)
        server.save_settings.assert_called_once()


class HubHostPersistTests(unittest.TestCase):
    def test_ensure_hub_host_persists_in_scope_resolution(self):
        from chatx5.web.server import ChatWebServer

        server = ChatWebServer.__new__(ChatWebServer)
        server.config_dir = tempfile.mkdtemp()
        server.discovery = MagicMock()
        server._resolve_hub_host_in_scope = MagicMock(return_value="10.0.30.112")
        saved = {}

        def _save(s):
            saved.update(s)

        server.save_settings = _save
        out = server._ensure_hub_host_in_scope(
            {"hub_role": "client", "hub_host": "10.0.5.37", "hub_port": 4242},
            persist=True,
        )
        self.assertEqual(out["hub_host"], "10.0.30.112")
        self.assertEqual(saved.get("hub_host"), "10.0.30.112")


class HubHeadlessSpecTests(unittest.TestCase):
    """Specification tests for planned dedicated headless hub mode (not yet implemented)."""

    def test_headless_hub_setting_not_in_defaults_yet(self):
        from chatx5.web.server import ChatWebServer

        server = ChatWebServer.__new__(ChatWebServer)
        server.config_dir = tempfile.mkdtemp()
        with patch("builtins.open", side_effect=FileNotFoundError):
            defaults = server.load_settings()
        self.assertNotIn("headless_hub", defaults)

    def test_server_mode_supports_tcp_only_relay_path(self):
        """Headless hub will reuse hub_role=server + TCP listener on 4242."""
        from chatx5.web.server import ChatWebServer

        server = ChatWebServer.__new__(ChatWebServer)
        settings = {
            "hub_role": "server",
            "hub_port": 4242,
            "rns_interfaces": [],
        }
        out = server._apply_hub_settings(settings)
        tcp_srv = next(
            i for i in out["rns_interfaces"]
            if i.get("type") == "TCPServerInterface"
        )
        self.assertTrue(tcp_srv.get("enabled"))
        self.assertEqual(tcp_srv.get("listen_ip"), "0.0.0.0")


if __name__ == "__main__":
    unittest.main()