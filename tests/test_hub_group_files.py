"""Hub group file transfer over hub TCP links."""

import json
import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from chatx5.core.messaging.backend import MessagingBackend
from chatx5.core.messaging.constants import HUB_GROUP_PEER, MESSAGE_TYPE_FILE


class HubGroupFileTests(unittest.TestCase):
    def _backend(self, hub_role="client", hub_server_hash=""):
        ident = MagicMock()
        ident.hash = bytes.fromhex("a" * 32)
        tmp = tempfile.mkdtemp()
        settings_path = os.path.join(tmp, "settings.json")
        with open(settings_path, "w", encoding="utf-8") as fh:
            json.dump({
                "hub_role": hub_role,
                "hub_host": "10.0.30.112",
                "hub_port": 4242,
                "hub_server_hash": hub_server_hash,
            }, fh)
        backend = MessagingBackend(identity=ident, config_dir=tmp)
        hub_link = MagicMock()
        hub_link.link_id = "hub1"
        hub_link.status = 1
        peer = "b" * 32
        backend.peer_links = {f"{peer}:tcp": hub_link}
        backend.links = {"hub1": hub_link}
        backend._hub_tcp_transport_online = MagicMock(return_value=True)
        backend._hub_tcp_linked_peers = MagicMock(return_value=[peer])
        backend._hub_send_targets = MagicMock(return_value=[peer])
        backend._hub_link_for_peer = MagicMock(return_value=hub_link)
        return backend, hub_link, peer, tmp

    def test_send_hub_file_uses_hub_link(self):
        backend, hub_link, peer, tmp = self._backend()
        fpath = os.path.join(tmp, "big.bin")
        with open(fpath, "wb") as fh:
            fh.write(b"x" * 600_000)
        sent_msg = MagicMock()
        with patch.object(backend, "send_file", return_value=sent_msg) as send_file:
            result = backend.send_hub_file(fpath, MESSAGE_TYPE_FILE, transfer_id="tid1")
        self.assertIs(result, sent_msg)
        send_file.assert_called_once()
        kwargs = send_file.call_args.kwargs
        self.assertTrue(kwargs.get("hub_group"))
        self.assertIs(kwargs.get("link"), hub_link)
        self.assertEqual(kwargs.get("target_peer"), peer)

    def test_drain_hub_group_queue_sends_files(self):
        backend, hub_link, peer, tmp = self._backend(hub_server_hash="c" * 32)
        fpath = os.path.join(tmp, "photo.jpg")
        with open(fpath, "wb") as fh:
            fh.write(b"jpeg")
        backend.message_queue = [{
            "type": "image",
            "content": fpath,
            "file_path": fpath,
            "target_hash": HUB_GROUP_PEER,
            "msg_id": "img01",
            "file_name": "photo.jpg",
            "file_size": 4,
        }]
        with patch.object(backend, "send_hub_file", return_value=MagicMock()) as send_hub:
            sent = backend.drain_hub_group_queue(hub_server_hash="c" * 32, hub_server_mode=False)
        self.assertEqual(sent, 1)
        send_hub.assert_called_once()
        self.assertEqual(backend.message_queue, [])

    def test_relay_hub_file_skips_sender(self):
        backend, hub_link, peer, tmp = self._backend(hub_role="server")
        other = "d" * 32
        backend._hub_tcp_linked_peers = MagicMock(return_value=[peer, other])
        backend._hub_link_for_peer = MagicMock(side_effect=lambda p: hub_link if p == other else None)
        backend.hashes_equivalent = MagicMock(side_effect=lambda a, b: a == b)
        fpath = os.path.join(tmp, "doc.pdf")
        with open(fpath, "wb") as fh:
            fh.write(b"%PDF")
        msg = MagicMock()
        msg.msg_type = MESSAGE_TYPE_FILE
        msg.msg_id = "f1"
        msg.file_name = "doc.pdf"
        with patch.object(backend, "send_file", return_value=MagicMock()) as send_file:
            relayed = backend.relay_hub_file(msg, peer, fpath)
        self.assertEqual(relayed, 1)
        send_file.assert_called_once()
        self.assertTrue(send_file.call_args.kwargs.get("hub_group"))


class SerialLinkQualityTests(unittest.TestCase):
    def test_serial_link_quality_percent(self):
        from chatx5.core.peer_probe import serial_link_quality_percent

        self.assertEqual(serial_link_quality_percent(30), 100)
        self.assertEqual(serial_link_quality_percent(2000), 0)
        mid = serial_link_quality_percent(1025)
        self.assertIsNotNone(mid)
        self.assertTrue(40 <= mid <= 60)