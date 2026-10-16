"""Demo-only browser checks through a local Worker and a temporary connector."""

import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request

import e2e_browser


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.host_cloudflare import connector, stop, wait_for_connector
from scripts.processes import start_managed


class ProxyBrowserTests(e2e_browser.BrowserTests):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        environment = os.environ.copy()
        for name in tuple(environment):
            if name.startswith("CLOUDFLARE_API_TOKEN") or name == "TUNNEL_TOKEN":
                environment.pop(name)
        environment["CI"] = "true"
        environment["WRANGLER_SEND_METRICS"] = "false"
        flags = 0x08000000 if os.name == "nt" else 0
        tunnel = start_managed(
            [connector(), "tunnel", "--no-autoupdate", "--protocol", "http2", "--url", cls.base_url],
            env=environment, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", creationflags=flags,
        )
        cls.resources.append(lambda: stop(tunnel))
        origin = wait_for_connector(tunnel)
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        worker_log = tempfile.TemporaryFile(mode="w+b")
        cls.resources.append(worker_log.close)
        worker = start_managed(
            ["node", str(ROOT / "deploy/node_modules/wrangler/bin/wrangler.js"),
             "dev", "--local", "--ip", "127.0.0.1", "--port", str(port),
             "--inspector-port", "0", "--var", "DESKTOP_ORIGIN:" + origin],
            cwd=ROOT / "deploy", env=environment, stdout=worker_log,
            stderr=subprocess.STDOUT, creationflags=flags,
        )
        cls.resources.append(lambda: stop(worker))
        cls.base_url = f"http://127.0.0.1:{port}/"
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if worker.poll() is not None:
                worker_log.seek(0)
                raise RuntimeError("Local Worker failed: " + worker_log.read().decode(errors="replace")[-1500:])
            try:
                with urllib.request.urlopen(cls.base_url, timeout=1) as response:
                    if response.status == 200:
                        break
            except (OSError, urllib.error.URLError):
                pass
            time.sleep(0.1)
        else:
            raise RuntimeError("Local Worker did not become ready.")
        print("Proxy path: browser -> local Worker -> temporary Cloudflare connector -> demo app; no provider calls or production writes.", flush=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
