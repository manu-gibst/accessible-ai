import argparse
import asyncio
import logging
from pathlib import Path
import socket
import ssl
from urllib.parse import urlsplit

from .claude import Claude, DEFAULT_PROMPT, find_claude
from .pipeline import (ReviewedAnswer, find_codex, DEFAULT_CLAUDE_MODEL,
                       DEFAULT_REVIEW_MODEL, DEFAULT_FALLBACK_MODEL)
from .server import Application
from .state import ResponseState


def validate_public_url(value):
    message = "--public-url must be an HTTPS origin with a valid hostname and port, without credentials, a path, a query, or a fragment"
    try:
        parsed = urlsplit(value)
        hostname, port = parsed.hostname, parsed.port
        invalid = parsed.scheme != "https" or not hostname or any(character.isspace() for character in hostname) or "\\" in hostname or parsed.netloc.endswith(":") or port == 0 or parsed.username is not None or parsed.password is not None or parsed.query or parsed.fragment or parsed.path not in {"", "/"}
    except ValueError:
        raise ValueError(message) from None
    if invalid:
        raise ValueError(message)
    return value.rstrip("/")


async def run(options):
    state = ResponseState()
    provider = (Claude(demo=True) if options.demo else
                ReviewedAnswer(options.model, options.review_model,
                               options.fallback_model, options.timeout))
    app = Application(state, provider, prompt=options.prompt)
    desktop = None
    context = None
    if options.cert:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(options.cert, options.key)
    if not options.demo:
        find_claude()
        find_codex()
    if not options.no_desktop:
        from .desktop import Desktop
        loop = asyncio.get_running_loop()
        desktop = Desktop(state, lambda: loop.call_soon_threadsafe(app.submit), options.dwell)
        app.capture = desktop.capture
        desktop.start()
    protocol = "https" if context else "http"
    addresses = {"127.0.0.1"} if options.host in {"0.0.0.0", "::"} else {options.host}
    if options.host == "0.0.0.0":
        try:
            addresses.update(socket.gethostbyname_ex(socket.gethostname())[2])
        except OSError:
            pass
    print("Accessible AI is starting. Open this URL on your phone (same Wi-Fi):", flush=True)
    for address in sorted(addresses):
        print(f"  {protocol}://{address}:{options.port}/", flush=True)
    if options.public_url:
        print(f"Public URL: {options.public_url.rstrip('/')}/", flush=True)
    print("Top-right: capture | Bottom-left: start/resume | Ctrl+V: paste remainder | Escape: pause | Ctrl+C here: quit", flush=True)
    if options.demo:
        print("DEMO: uses a fixed response without calling Claude.", flush=True)
    try:
        await app.run(options.host, options.port, context)
    finally:
        if desktop:
            desktop.stop()


def main():
    parser = argparse.ArgumentParser(description="Reviewed image responses with user-paced Windows typing")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--public-url", help="Public HTTPS address of a reverse proxy or Cloudflare Tunnel")
    parser.add_argument("--model", default=DEFAULT_CLAUDE_MODEL, help="Claude generator model")
    parser.add_argument("--review-model", default=DEFAULT_REVIEW_MODEL, help="Codex reviewer model")
    parser.add_argument("--fallback-model", default=DEFAULT_FALLBACK_MODEL, help="Codex fallback model")
    parser.add_argument("--timeout", type=float, default=180)
    parser.add_argument("--dwell", type=float, default=0.45, help="Seconds to dwell in a corner")
    parser.add_argument("--prompt", default=DEFAULT_PROMPT)
    parser.add_argument("--prompt-file", type=Path, help="UTF-8 instructions for both input sources")
    parser.add_argument("--no-desktop", action="store_true", help="Serve phone uploads without keyboard hooks or screenshots")
    parser.add_argument("--demo", action="store_true", help="Use a fixed answer without calling Claude")
    parser.add_argument("--cert", help="TLS certificate PEM file")
    parser.add_argument("--key", help="TLS private key PEM file")
    options = parser.parse_args()
    if options.public_url:
        try:
            options.public_url = validate_public_url(options.public_url)
        except ValueError as error:
            parser.error(str(error))
    if bool(options.cert) != bool(options.key):
        parser.error("--cert and --key must be supplied together")
    if options.dwell <= 0 or options.timeout <= 0 or not 1 <= options.port <= 65535:
        parser.error("Dwell and timeout must be positive; port must be 1–65535")
    if options.prompt_file:
        options.prompt = options.prompt_file.read_text(encoding="utf-8")
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")
    try:
        asyncio.run(run(options))
    except KeyboardInterrupt:
        pass
    except (RuntimeError, OSError) as error:
        parser.exit(1, f"Error: {error}\n")


if __name__ == "__main__":
    main()
