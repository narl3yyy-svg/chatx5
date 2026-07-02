"""Auto-extracted from web/server.py — SystemRoutes layer."""

import asyncio
import os
import subprocess
import sys

from aiohttp import web

from chatx5.utils.platform import (
    is_android,
)
from chatx5.utils.system import get_cpu_percent, get_cpu_temperature_detail
from chatx5.web.rns_utils import (
    stop_stale_chatx5_servers,
)


class SystemRoutesMixin:
    def _spawn_unix_server_restart(self):
        """Re-exec via restart-server.sh so dialout/uucp (sg) is preserved on Linux."""
        sys.stdout.flush()
        root = os.environ.get("CHATX5_ROOT") or os.getcwd()
        extra = list(sys.argv[1:]) or ["--share"]
        env = os.environ.copy()
        env["CHATX5_ROOT"] = root
        env["PYTHONPATH"] = root
        env["PYTHON"] = sys.executable
        wrapper = os.path.join(root, "scripts", "restart-server.sh")
        if os.path.isfile(wrapper):
            cmd = ["bash", wrapper, str(os.getpid()), root, *extra]
        else:
            cmd = ["bash", os.path.join(root, "run.sh"), "web", *extra]
        subprocess.Popen(
            cmd,
            cwd=root,
            env=env,
            start_new_session=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        os._exit(0)

    async def handle_restart(self, request):
        if is_android():
            settings = self.load_settings()
            self._write_rns_config(settings)
            await asyncio.to_thread(self._apply_hub_runtime, settings)
            return web.json_response({
                "status": "restarting",
                "android": True,
                "rns_reloaded": True,
            })
        if not getattr(sys, "frozen", False):
            try:
                await self._reload_server_runtime()
                print("[restart] Reloaded network stack in-process")
                return web.json_response({
                    "status": "ok",
                    "restarting": True,
                    "reloaded": True,
                    "message": "Network stack reloaded — refresh the page",
                })
            except Exception as e:
                if sys.platform != "win32":
                    print(f"[restart] In-process reload failed ({e}) — spawning new process")
                    asyncio.get_event_loop().call_later(0.8, self._spawn_unix_server_restart)
                    return web.json_response({"status": "restarting"})
                return web.json_response({"error": str(e)}, status=400)
        if getattr(sys, "frozen", False) and sys.platform == "win32":
            exe = sys.executable
            cwd = os.path.dirname(os.path.abspath(exe))

            def _win_restart():
                sys.stdout.flush()
                stop_stale_chatx5_servers(exclude_pid=os.getpid())
                flags = (
                    getattr(subprocess, "DETACHED_PROCESS", 0)
                    | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                )
                subprocess.Popen(
                    [exe],
                    cwd=cwd,
                    close_fds=True,
                    creationflags=flags,
                )
                os._exit(0)

            print(f"[restart] Spawning new process: {exe}")
            asyncio.get_event_loop().call_later(0.5, _win_restart)
            return web.json_response({"status": "restarting"})
        def _source_restart():
            sys.stdout.flush()
            stop_stale_chatx5_servers(exclude_pid=os.getpid())
            root = os.environ.get("CHATX5_ROOT") or os.getcwd()
            extra = [a for a in sys.argv[1:] if a.startswith("-")]
            if sys.platform == "win32":
                run_bat = os.path.join(root, "run.bat")
                cmd = ["cmd.exe", "/c", run_bat, "web"] + (extra or ["--share"])
                flags = (
                    getattr(subprocess, "DETACHED_PROCESS", 0)
                    | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                )
                subprocess.Popen(cmd, cwd=root, creationflags=flags)
            else:
                args = [sys.executable, "-m", "chatx5.web.server", *sys.argv[1:]]
                env = os.environ.copy()
                env["CHATX5_ROOT"] = root
                env["PYTHONPATH"] = root
                subprocess.Popen(args, cwd=root, env=env, start_new_session=True)
            os._exit(0)

        print("[restart] Spawning new server process")
        asyncio.get_event_loop().call_later(0.8, _source_restart)
        return web.json_response({"status": "restarting"})

    async def handle_temperature(self, request):
        try:
            detail = await asyncio.to_thread(get_cpu_temperature_detail)
        except Exception:
            detail = {"avg_celsius": None, "approx": False}
        return web.json_response(detail)

    async def handle_cpu(self, request):
        pct = await asyncio.to_thread(get_cpu_percent)
        if pct is not None:
            return web.json_response({"cpu_percent": pct})
        return web.json_response({"cpu_percent": None})

    async def handle_brand_logo_get(self, request):
        path = self._brand_logo_path()
        if not os.path.isfile(path):
            raise web.HTTPNotFound()
        return web.FileResponse(path)

    async def handle_brand_logo_upload(self, request):
        try:
            reader = await request.multipart()
            field = await reader.next()
            if not field or field.name != "logo":
                return web.json_response({"error": "missing logo field"}, status=400)
            data = await field.read()
            if not data or len(data) > 2 * 1024 * 1024:
                return web.json_response({"error": "invalid image"}, status=400)
            os.makedirs(self.config_dir, exist_ok=True)
            with open(self._brand_logo_path(), "wb") as f:
                f.write(data)
            return web.json_response({"status": "ok"})
        except Exception as exc:
            return web.json_response({"error": str(exc)}, status=400)

    async def handle_brand_logo_delete(self, request):
        try:
            os.remove(self._brand_logo_path())
        except FileNotFoundError:
            pass
        except OSError as exc:
            return web.json_response({"error": str(exc)}, status=400)
        return web.json_response({"status": "ok"})

    async def handle_release_notes(self, request):
        from chatx5.release_notes import CURRENT_VERSION, all_release_notes
        return web.json_response({
            "current_version": CURRENT_VERSION,
            "releases": all_release_notes(),
        })

    async def handle_health(self, request):
        status = "ok" if not self.rns_init_error else "rns_error"
        return web.json_response({
            "status": status,
            "rns_ready": self.messaging is not None,
            "rns_error": self.rns_init_error,
        })

