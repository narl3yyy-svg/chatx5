"""Tests for web static asset resolution (Android + desktop)."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from chatx5.web.server import ChatWebServer


class StaticDirResolutionTests(unittest.TestCase):
    def test_static_dir_finds_web_static_not_routes_static(self):
        server = ChatWebServer.__new__(ChatWebServer)
        static = server._static_dir()
        self.assertTrue((static / "index.html").exists(), static)
        self.assertEqual(static.name, "static")
        self.assertEqual(static.parent.name, "web")

    def test_static_dir_uses_package_root(self):
        server = ChatWebServer.__new__(ChatWebServer)
        got = server._static_dir()
        self.assertTrue(got.exists())
        self.assertTrue((got / "index.html").exists())


if __name__ == "__main__":
    unittest.main()