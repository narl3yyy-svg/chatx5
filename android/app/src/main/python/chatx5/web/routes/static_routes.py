"""Auto-extracted from web/server.py — StaticRoutes layer."""

import mimetypes
import sys
from pathlib import Path

from aiohttp import web

from chatx5.utils.platform import (
    is_android,
)


class StaticRoutesMixin:
    def _static_dir(self):
        routes_dir = Path(__file__).resolve().parent
        web_dir = routes_dir.parent
        candidates = []
        if getattr(sys, "frozen", False):
            meipass = getattr(sys, "_MEIPASS", None)
            if meipass:
                candidates.append(Path(meipass) / "chatx5" / "web" / "static")
        try:
            import chatx5 as chatx5_pkg

            pkg_root = Path(chatx5_pkg.__file__).resolve().parent
            candidates.append(pkg_root / "web" / "static")
        except Exception:
            pass
        candidates.extend([
            web_dir / "static",
            routes_dir / "static",
            Path.cwd() / "chatx5" / "web" / "static",
            Path.cwd() / "static",
        ])
        if is_android():
            candidates.append(web_dir / "static")
        seen = set()
        for p in candidates:
            key = str(p)
            if key in seen:
                continue
            seen.add(key)
            if p.exists() and (p / "index.html").exists():
                return p
        return web_dir / "static"

    async def handle_index(self, request):
        static_dir = self._static_dir()
        index_path = static_dir / "index.html"
        if not index_path.exists():
            tried = ", ".join(str(static_dir))
            print(f"[web] Frontend not found — looked in {tried}")
            return web.Response(text="Frontend not found", status=500)
        resp = web.FileResponse(index_path)
        resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        resp.headers["Pragma"] = "no-cache"
        return resp

    async def handle_static(self, request):
        static_dir = self._static_dir()
        filepath = static_dir / request.match_info["filename"]
        if not filepath.exists() or not filepath.is_file():
            return web.Response(text="Not found", status=404)
        ct, _ = mimetypes.guess_type(str(filepath))
        resp = web.FileResponse(filepath)
        if ct:
            resp.headers['Content-Type'] = ct
        return resp