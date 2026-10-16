import asyncio
import base64
import binascii
from contextlib import suppress
from http import HTTPStatus
from io import BytesIO
import json
import logging
from pathlib import Path
import warnings

from PIL import Image, UnidentifiedImageError
from websockets.asyncio.server import serve
from websockets.exceptions import ConnectionClosed
from websockets.http11 import Response
from websockets.datastructures import Headers

from .claude import DEFAULT_PROMPT


MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_PIXELS = 25_000_000
LOGGER = logging.getLogger(__name__)


def normalize_image(data):
    if len(data) > MAX_IMAGE_BYTES:
        raise ValueError("Image exceeds 10 MB. Choose a smaller photo.")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(data)) as source:
                if source.width * source.height > MAX_PIXELS:
                    raise ValueError("Image exceeds 25 megapixels. Choose a smaller photo.")
                from PIL import ImageOps
                image = ImageOps.exif_transpose(source).convert("RGB")
                image.thumbnail((2000, 2000))
                output = BytesIO()
                image.save(output, format="PNG")
                return output.getvalue()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError, Image.DecompressionBombWarning) as error:
        raise ValueError("Unsupported or damaged image. Use JPEG, PNG, or WebP.") from error


class Application:
    def __init__(self, state, claude, capture=None, prompt=DEFAULT_PROMPT):
        self.state, self.claude = state, claude
        self.capture, self.prompt = capture, prompt
        self.clients = set()
        self.job = None

    def submit(self, data=None, prompt=None):
        if not self.state.begin():
            return False
        self.job = asyncio.create_task(self.generate(data, prompt or self.prompt))
        return True

    async def generate(self, data, prompt):
        try:
            if data is None:
                if self.capture is None:
                    raise ValueError("Desktop capture is disabled.")
                data = await asyncio.to_thread(self.capture)
            image = await asyncio.to_thread(normalize_image, data)
            self.state.ready(await self.claude.generate(image, prompt))
        except TimeoutError:
            self.state.fail("AI request timed out. Try again or increase --timeout.")
        except asyncio.CancelledError:
            raise
        except Exception as error:
            LOGGER.warning("Image request failed: %s", error)
            self.state.fail(str(error))

    async def connection(self, websocket):
        try:
            self.clients.add(websocket)
            await websocket.send(json.dumps(self.state.snapshot()))
            async for raw in websocket:
                try:
                    message = json.loads(raw)
                    if not isinstance(message, dict) or message.get("type") != "image":
                        raise ValueError("Expected an image message.")
                    prompt = message.get("prompt", "")
                    if not isinstance(prompt, str) or len(prompt) > 8000:
                        raise ValueError("Instructions must be text under 8,000 characters.")
                    encoded = message.get("data")
                    if not isinstance(encoded, str):
                        raise ValueError("Missing image data.")
                    data = base64.b64decode(encoded, validate=True)
                    if len(data) > MAX_IMAGE_BYTES:
                        raise ValueError("Image exceeds 10 MB.")
                    if not self.submit(data, prompt):
                        raise ValueError("Finish or pause typing, or wait for the current request.")
                except (ValueError, TypeError, binascii.Error) as error:
                    await websocket.send(json.dumps({"type": "error", "message": str(error)}))
        except ConnectionClosed:
            pass
        finally:
            self.clients.discard(websocket)

    async def publish(self):
        previous = None
        while True:
            snapshot = json.dumps(self.state.snapshot())
            if snapshot != previous:
                previous = snapshot
                clients = list(self.clients)
                if clients:
                    await asyncio.gather(*(self.send_status(client, snapshot) for client in clients))
            await asyncio.sleep(0.1)

    async def send_status(self, client, snapshot):
        try:
            await asyncio.wait_for(client.send(snapshot), 2)
        except (ConnectionClosed, TimeoutError):
            self.clients.discard(client)
            await client.close()

    def http_request(self, connection, request):
        if request.path == "/ws" and request.headers.get("Upgrade", "").lower() == "websocket":
            return None
        if request.path not in {"/", "/index.html"}:
            return connection.respond(HTTPStatus.NOT_FOUND, "Not found\n")
        body = (Path(__file__).parent / "web/index.html").read_bytes()
        return Response(200, "OK", Headers({
            "Content-Type": "text/html; charset=utf-8", "Content-Length": str(len(body)),
            "Cache-Control": "no-store", "Referrer-Policy": "no-referrer",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src 'self' blob:; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'",
        }), body)

    async def run(self, host, port, ssl=None):
        publisher = asyncio.create_task(self.publish())
        try:
            async with serve(self.connection, host, port, process_request=self.http_request,
                             max_size=14 * 1024 * 1024, max_queue=2, compression=None,
                             ssl=ssl, open_timeout=10, close_timeout=3):
                await asyncio.Future()
        finally:
            publisher.cancel()
            with suppress(asyncio.CancelledError):
                await publisher
            if self.job and not self.job.done():
                self.job.cancel()
                with suppress(asyncio.CancelledError):
                    await self.job
