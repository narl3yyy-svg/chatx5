"""RNS interface summary for live network status."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from chatx5.core.rns_interfaces import summarize_rns_interfaces


class TCPClientInterface:
    pass


class TCPServerInterface:
    pass


def _iface(cls, name="", online=True, **attrs):
    obj = cls()
    obj.name = name
    obj.online = online
    for key, val in attrs.items():
        setattr(obj, key, val)
    return obj


class RnsInterfaceSummaryTests(unittest.TestCase):
    def test_collapses_inbound_hub_tcp_clients(self):
        raw = [
            _iface(TCPClientInterface, "Client on TCP Hub Server", online=True),
            _iface(TCPClientInterface, "Client on TCP Hub Server", online=True),
            _iface(TCPServerInterface, "TCP Hub Server", online=True, listen_port=4242),
        ]
        summary = summarize_rns_interfaces(raw, hub_role="server", hub_port=4242)
        inbound = [row for row in summary if row.get("role") == "inbound"]
        self.assertEqual(len(inbound), 1)
        self.assertEqual(inbound[0]["count"], 2)
        self.assertTrue(inbound[0]["online"])

    def test_groups_outbound_tcp_client_by_target(self):
        raw = [
            _iface(
                TCPClientInterface,
                "TCP Client 10.0.30.112:4242",
                online=True,
                target_host="10.0.30.112",
                target_port=4242,
            ),
            _iface(
                TCPClientInterface,
                "TCP Client 10.0.30.112:4242",
                online=False,
                target_host="10.0.30.112",
                target_port=4242,
            ),
        ]
        summary = summarize_rns_interfaces(raw, hub_role="client", hub_port=4242)
        outbound = [row for row in summary if row.get("role") == "outbound"]
        self.assertEqual(len(outbound), 1)
        self.assertEqual(outbound[0]["count"], 2)
        self.assertEqual(outbound[0]["online_count"], 1)


if __name__ == "__main__":
    unittest.main()