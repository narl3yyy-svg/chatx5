"""Frontend static asset integrity after Phase 11 modularization."""

import os
import re
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pathlib import Path


class StaticFrontendTests(unittest.TestCase):
    def setUp(self):
        self.static = Path(__file__).resolve().parent.parent / "chatx5" / "web" / "static"

    def test_index_html_is_modular_shell(self):
        html = (self.static / "index.html").read_text(encoding="utf-8")
        self.assertNotIn("<style>", html)
        self.assertNotIn('function init()', html)
        self.assertIn('/static/css/app.css', html)
        self.assertLess(len(html.splitlines()), 700)

    def test_referenced_static_assets_exist(self):
        html = (self.static / "index.html").read_text(encoding="utf-8")
        refs = re.findall(r'(?:href|src)="/static/([^"]+)"', html)
        self.assertGreater(len(refs), 10)
        for ref in refs:
            path = self.static / ref
            self.assertTrue(path.is_file(), f"missing static asset: {ref}")

    def test_app_css_nonempty(self):
        css = self.static / "css" / "app.css"
        self.assertTrue(css.is_file())
        self.assertGreater(len(css.read_text(encoding="utf-8")), 1000)


if __name__ == "__main__":
    unittest.main()