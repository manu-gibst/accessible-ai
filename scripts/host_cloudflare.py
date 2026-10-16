"""Serve manu.wiphk.com with a Worker and a temporary desktop connector."""

import argparse
import hashlib
import http.client
import ipaddress
import json
import os
from pathlib import Path
import queue
import re
import shutil
import socket
import ssl
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from urllib.parse import urlencode, urlsplit

if __package__:
    from .processes import start_managed, stop
else:
    from processes import start_managed, stop


ROOT = Path(__file__).resolve().parents[1]
HOSTNAME = "manu.wiphk.com"
ZONE_ID = "73c503f21d675020e0dccfaf162712a5"


def request_json(url, token=None, method="GET", payload=None):
    headers = {"User-Agent": "accessible-ai-hosting", "Accept": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    data = None
    if payload is not None:
        data = json.dumps(payload).encode()
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        raise RuntimeError(f"Hosting API returned HTTP {error.code} for {method} {urllib.parse.urlsplit(url).path}. Check the token permissions.") from None


def cloudflare(path, token, method="GET", payload=None):
    data = request_json("https://api.cloudflare.com/client/v4" + path, token, method, payload)
    if not data.get("success"):
        codes = [item.get("code") for item in data.get("errors", [])]
        raise RuntimeError(f"Cloudflare rejected the request; error codes: {codes}")
    return data["result"]


def check_hostname():
    dns_token = os.environ.get("CLOUDFLARE_API_TOKEN")
    if not dns_token:
        raise RuntimeError("Set CLOUDFLARE_API_TOKEN with Workers and DNS Edit permissions.")
    zone = cloudflare(f"/zones/{ZONE_ID}", dns_token)
    if zone["name"] != "wiphk.com":
        raise RuntimeError("The configured zone is not wiphk.com.")
    account_id = zone["account"]["id"]
    records = cloudflare(f"/zones/{ZONE_ID}/dns_records?name={HOSTNAME}", dns_token)
    domains = cloudflare(f"/accounts/{account_id}/workers/domains", dns_token)
    matching = [item for item in domains if item["hostname"] == HOSTNAME]
    if any(item.get("service") != "accessible-ai-manu" for item in matching) or (records and not matching):
        raise RuntimeError("The hostname belongs to another service; refusing to overwrite it.")
    configured = json.loads((ROOT / "deploy/wrangler.jsonc").read_text())
    if configured["account_id"] != account_id:
        raise RuntimeError("Worker configuration and zone account do not match.")


def read_connector_output(process, lines):
    with process.stdout as output:
        for line in output:
            try:
                lines.put_nowait(line)
            except queue.Full:
                pass


def resolved_origin_response(hostname):
    query = urlencode({"name": hostname, "type": "A"})
    request = urllib.request.Request("https://cloudflare-dns.com/dns-query?" + query, headers={"Accept": "application/dns-json"})
    with urllib.request.urlopen(request, timeout=3) as response:
        answer = json.load(response)
    addresses = []
    for record in answer.get("Answer", []) if answer.get("Status") == 0 else []:
        if record.get("type") != 1:
            continue
        address = ipaddress.ip_address(record["data"])
        if address.version == 4 and address.is_global:
            addresses.append(str(address))
    if not addresses:
        raise OSError("Public DNS did not return a routable connector address.")
    last_error = None
    context = ssl.create_default_context()
    for address in addresses:
        try:
            with socket.create_connection((address, 443), timeout=3) as raw_socket:
                with context.wrap_socket(raw_socket, server_hostname=hostname) as tls_socket:
                    connection = http.client.HTTPSConnection(hostname, timeout=3, context=context)
                    connection.sock = tls_socket
                    try:
                        connection.request("GET", "/", headers={"Host": hostname})
                        response = connection.getresponse()
                        return response.status, response.read(64 * 1024)
                    finally:
                        connection.close()
        except OSError as error:
            last_error = error
    raise OSError(f"Resolved connector could not be reached: {last_error}")


def probe_origin(origin):
    try:
        with urllib.request.urlopen(origin + "/", timeout=3) as response:
            status, body = response.status, response.read(64 * 1024)
    except urllib.error.HTTPError as error:
        status = error.code
        error.close()
        raise OSError(f"Connector returned HTTP {status}.") from None
    except urllib.error.URLError as error:
        hostname = urlsplit(origin).hostname or ""
        if not isinstance(error.reason, socket.gaierror) or not re.fullmatch(r"https://[a-z0-9-]+\.trycloudflare\.com", origin):
            raise
        status, body = resolved_origin_response(hostname)
    if status != 200:
        raise OSError(f"Connector returned HTTP {status}.")
    if b"A picture in." not in body:
        raise OSError("Connector returned a page other than the desktop app.")


def origin_healthy(origin):
    try:
        probe_origin(origin)
        return True
    except (OSError, ValueError):
        return False


def wait_for_connector(process, timeout=60):
    lines = queue.Queue(maxsize=128)
    threading.Thread(target=read_connector_output, args=(process, lines), daemon=True).start()
    deadline = time.monotonic() + timeout
    origin, registered = None, False
    last_error = "No public app probe completed."
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("Cloudflared stopped before establishing a connector.")
        try:
            line = lines.get(timeout=min(0.2, max(0.001, deadline - time.monotonic())))
        except queue.Empty:
            line = ""
        match = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com", line)
        if match:
            origin = match.group(0)
        if "Registered tunnel connection" in line:
            registered = True
        if origin and registered:
            try:
                probe_origin(origin)
                return origin
            except (OSError, ValueError) as error:
                last_error = str(error)
    detail = last_error if registered else "No tunnel registration observed. Check outbound port 7844 connectivity."
    raise RuntimeError(f"Connector not ready within {timeout} seconds (URL received: {bool(origin)}, registered: {registered}). {detail}")


def require_free_port():
    try:
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 8765))
    except OSError:
        raise RuntimeError("Port 8765 is already in use. Stop the existing desktop app before starting hosting.") from None


def wait_for_app(process, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("The desktop app failed to start.")
        if origin_healthy("http://127.0.0.1:8765"):
            return
        time.sleep(0.1)
    raise RuntimeError("The desktop app did not become ready within ten seconds.")


def connector():
    installed = shutil.which("cloudflared")
    if installed:
        return installed
    if sys.platform != "win32":
        raise RuntimeError("Install cloudflared on this operating system first.")
    directory = ROOT / ".runtime"
    directory.mkdir(exist_ok=True)
    executable = directory / "cloudflared.exe"
    if executable.is_file():
        return str(executable)
    release = request_json("https://api.github.com/repos/cloudflare/cloudflared/releases/latest")
    asset = next(item for item in release["assets"] if item["name"] == "cloudflared-windows-amd64.exe")
    with urllib.request.urlopen(asset["browser_download_url"], timeout=90) as response:
        binary = response.read()
    digest = asset.get("digest")
    if not digest or digest != "sha256:" + hashlib.sha256(binary).hexdigest():
        raise RuntimeError("The cloudflared release checksum could not be verified.")
    executable.write_bytes(binary)
    print(f"Installed verified cloudflared {release['tag_name']} in .runtime.", flush=True)
    return str(executable)


def relay_output(process, output):
    with process.stdout as source:
        for line in source:
            try:
                output.write(line)
            except UnicodeEncodeError:
                encoding = getattr(output, "encoding", None) or "utf-8"
                output.write(line.encode(encoding, errors="replace").decode(encoding))
            output.flush()


def start_with_output(command, **options):
    process = start_managed(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, encoding="utf-8", errors="replace", **options)
    process._output_thread = threading.Thread(target=relay_output, args=(process, sys.stdout), daemon=True)
    process._output_thread.start()
    return process


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--demo", action="store_true")
    parser.add_argument("--no-desktop", action="store_true")
    parser.add_argument("--model", help="Claude generator model")
    parser.add_argument("--review-model", help="Codex reviewer model")
    parser.add_argument("--fallback-model", help="Codex fallback model")
    options = parser.parse_args()
    app = tunnel = deployment = None
    try:
        require_free_port()
        executable = connector()
        check_hostname()
        wrangler = ROOT / "deploy/node_modules/wrangler/bin/wrangler.js"
        node = shutil.which("node")
        if not node or not wrangler.is_file():
            raise RuntimeError("Install Node.js, then run 'npm ci --prefix deploy' first.")
        environment = os.environ.copy()
        for name in tuple(environment):
            if name.startswith("CLOUDFLARE_API_TOKEN"):
                environment.pop(name)
        environment.pop("TUNNEL_TOKEN", None)
        environment["PYTHONIOENCODING"] = "utf-8"
        command = [sys.executable, "-m", "accessible_ai", "--host", "127.0.0.1", "--public-url", "https://" + HOSTNAME]
        if options.demo:
            command.append("--demo")
        if options.no_desktop:
            command.append("--no-desktop")
        for name in ("model", "review_model", "fallback_model"):
            value = getattr(options, name)
            if value:
                command.extend(["--" + name.replace("_", "-"), value])
        app = start_with_output(command, cwd=ROOT, env=environment, creationflags=0x08000000 if os.name == "nt" else 0)
        wait_for_app(app)
        tunnel = start_managed([executable, "tunnel", "--no-autoupdate", "--protocol", "http2", "--url", "http://127.0.0.1:8765"], env=environment, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace", creationflags=0x08000000 if os.name == "nt" else 0)
        origin = wait_for_connector(tunnel)
        if app.poll() is not None:
            raise RuntimeError("The desktop app failed to start.")
        deploy_environment = os.environ.copy()
        deploy_environment["CI"] = "true"
        deploy_environment["WRANGLER_SEND_METRICS"] = "false"
        deployment = start_with_output([node, str(wrangler), "deploy", "--var", "DESKTOP_ORIGIN:" + origin], cwd=ROOT / "deploy", env=deploy_environment, creationflags=0x08000000 if os.name == "nt" else 0)
        if deployment.wait():
            raise subprocess.CalledProcessError(deployment.returncode, deployment.args)
        stop(deployment)
        deployment._output_thread.join(timeout=2)
        print(f"Starting https://{HOSTNAME}. Keep this process and the PC running; Ctrl+C stops hosting.", flush=True)
        while True:
            if app.poll() is not None or tunnel.poll() is not None:
                raise RuntimeError("The desktop app or tunnel stopped. Review the messages above.")
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    except Exception as error:
        print(f"Hosting failed: {error}", file=sys.stderr, flush=True)
        return 1
    finally:
        try:
            stop(deployment)
        finally:
            try:
                stop(tunnel)
            finally:
                stop(app)
                for process in (app, deployment):
                    if process is not None:
                        process._output_thread.join(timeout=2)
    return 0


if __name__ == "__main__":
    sys.exit(main())
