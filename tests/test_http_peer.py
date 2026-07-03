"""HTTP/HTTPS peer client and settings persistence."""

import tempfile
import unittest

from chatx5.core.http_peer import peer_url
from chatx5.core.lan_transfer import register_offer


class HttpPeerUrlTests(unittest.TestCase):
    def test_peer_url_https(self):
        self.assertEqual(
            peer_url("10.0.30.112", 8742, "/api/lan-transfer/x?token=t"),
            "http://10.0.30.112:8742/api/lan-transfer/x?token=t",
        )
        self.assertEqual(
            peer_url("10.0.30.112", 8742, "/api/path_wake", scheme="https"),
            "https://10.0.30.112:8742/api/path_wake",
        )


class LanOfferSchemeTests(unittest.TestCase):
    def test_register_offer_stores_scheme(self):
        from chatx5.core.lan_transfer import peek_offer

        tok = register_offer("tid2", "/tmp/f2", "b" * 32, "10.0.0.2", 8742, scheme="https")
        offer = peek_offer("tid2", tok)
        self.assertEqual(offer.get("scheme"), "https")


class WanSecureSettingsTests(unittest.TestCase):
    def test_settings_roundtrip_wan_secure_mode(self):
        from chatx5.web.settings_store import SettingsStoreMixin

        class _Srv(SettingsStoreMixin):
            def __init__(self, config_dir):
                self.config_dir = config_dir
                self.host = "0.0.0.0"
                self.messaging = None

        tmp = tempfile.mkdtemp()
        srv = _Srv(tmp)
        settings = srv.load_settings()
        settings["wan_secure_mode"] = True
        settings["serial_quality_interval_s"] = 8
        srv.save_settings(settings)
        loaded = srv.load_settings()
        self.assertTrue(loaded.get("wan_secure_mode"))
        self.assertEqual(loaded.get("serial_quality_interval_s"), 8)