"""Shared folder browse session tests."""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from chatx5.web.server import ChatWebServer


class ShareBrowserTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.server = ChatWebServer(host="127.0.0.1", port=0)
        self.server.config_dir = self.tmp
        self.share_root = os.path.join(self.tmp, "shareme")
        os.makedirs(self.share_root)
        with open(os.path.join(self.share_root, "hello.txt"), "w", encoding="utf-8") as fh:
            fh.write("hello")
        os.makedirs(os.path.join(self.share_root, "nested"))

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_share_listing_returns_files_and_dirs(self):
        listing = self.server._share_listing(self.share_root)
        self.assertIsNotNone(listing)
        names = {e["name"] for e in listing["entries"]}
        self.assertIn("hello.txt", names)
        self.assertIn("nested", names)
        nested = next(e for e in listing["entries"] if e["name"] == "nested")
        self.assertTrue(nested["dir"])

    def test_share_session_requires_token(self):
        self.server._share_sessions["abc"] = {
            "root": self.share_root,
            "token": "secret",
            "expires": __import__("time").time() + 3600,
        }
        self.assertIsNone(self.server._share_session("abc", token="wrong"))
        self.assertIsNotNone(self.server._share_session("abc", token="secret"))

    def test_share_listing_rejects_missing_subdir(self):
        self.assertIsNone(self.server._share_listing(self.share_root, "missing-dir"))

    def test_traversal_stays_under_share_root(self):
        from chatx5.utils.helpers import safe_rel_path_under
        resolved = safe_rel_path_under(self.share_root, "../../outside.txt", "x")
        self.assertTrue(resolved.startswith(os.path.normpath(self.share_root)))
        self.assertNotEqual(resolved, os.path.join(self.tmp, "outside.txt"))


if __name__ == "__main__":
    unittest.main()