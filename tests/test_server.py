import asyncio
import base64
from io import BytesIO
import json
import unittest
from unittest.mock import AsyncMock

import httpx
from PIL import Image
from websockets.asyncio.client import connect
from websockets.asyncio.server import serve

from accessible_ai.server import Application, normalize_image
from accessible_ai.state import ResponseState


def image_bytes():
    output = BytesIO()
    Image.new("RGB", (8, 8), "white").save(output, "PNG")
    return output.getvalue()


class ServerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.claude = AsyncMock()
        self.claude.generate.return_value = "Image answer"
        self.state = ResponseState()
        self.app = Application(self.state, self.claude)
        self.server = await serve(self.app.connection, "127.0.0.1", 0,
                                  process_request=self.app.http_request)
        self.port = self.server.sockets[0].getsockname()[1]
        self.url = f"ws://127.0.0.1:{self.port}/ws"

    async def asyncTearDown(self):
        self.server.close()
        await self.server.wait_closed()
        if self.app.job:
            await self.app.job

    async def test_http_page_is_served_without_pairing_controls(self):
        async with httpx.AsyncClient() as client:
            response = await client.get(f"http://127.0.0.1:{self.port}/")
            self.assertEqual(response.status_code, 200)
            self.assertIn("Take a photo", response.text)
            self.assertNotIn("Pairing token", response.text)
            self.assertEqual(response.headers["Cache-Control"], "no-store")

    async def test_socket_connects_without_pairing(self):
        async with connect(self.url) as websocket:
            self.assertEqual(json.loads(await websocket.recv())["phase"], "idle")
        self.claude.generate.assert_not_called()

    async def test_upload_triggers_generation_and_status_broadcast(self):
        publisher = asyncio.create_task(self.app.publish())
        try:
            async with connect(self.url) as websocket:
                self.assertEqual(json.loads(await websocket.recv())["phase"], "idle")
                await websocket.send(json.dumps({"type": "image", "data": base64.b64encode(image_bytes()).decode(), "prompt": "Solve this"}))
                for attempt in range(10):
                    message = json.loads(await asyncio.wait_for(websocket.recv(), 2))
                    if message["phase"] == "ready":
                        break
                self.assertEqual(message["response"], "Image answer")
                self.claude.generate.assert_awaited_once()
                self.assertEqual(self.claude.generate.call_args.args[1], "Solve this")
        finally:
            publisher.cancel()
            await asyncio.gather(publisher, return_exceptions=True)

    async def test_malformed_upload_does_not_disconnect_or_call_claude(self):
        async with connect(self.url) as websocket:
            await websocket.recv()
            await websocket.send(json.dumps({"type": "image", "data": "%%%"}))
            self.assertEqual(json.loads(await websocket.recv())["type"], "error")
            await websocket.send("[]")
            self.assertEqual(json.loads(await websocket.recv())["type"], "error")
        self.claude.generate.assert_not_called()

    async def test_identical_consecutive_answers_always_publish_completion(self):
        publisher = asyncio.create_task(self.app.publish())
        try:
            async with connect(self.url) as websocket:
                await websocket.recv()
                for request_number in range(2):
                    await websocket.send(json.dumps({"type": "image", "data": base64.b64encode(image_bytes()).decode()}))
                    while True:
                        message = json.loads(await asyncio.wait_for(websocket.recv(), 1))
                        if message["phase"] == "ready":
                            break
                    self.assertEqual(message["response"], "Image answer")
                self.assertEqual(self.claude.generate.await_count, 2)
        finally:
            publisher.cancel()
            await asyncio.gather(publisher, return_exceptions=True)

    async def test_bad_image_reports_error_and_can_retry(self):
        self.assertTrue(self.app.submit(b"not an image"))
        await self.app.job
        self.assertEqual(self.state.snapshot()["phase"], "error")
        self.assertTrue(self.app.submit(image_bytes()))
        await self.app.job
        self.assertEqual(self.state.snapshot()["phase"], "ready")

    async def test_timeout_clears_busy_state(self):
        self.claude.generate.side_effect = TimeoutError
        self.app.submit(image_bytes())
        await self.app.job
        self.assertEqual(self.state.snapshot()["phase"], "error")
        self.assertIn("timed out", self.state.snapshot()["message"])


class ImageTests(unittest.TestCase):
    def test_image_normalizes_and_strips_metadata(self):
        result = normalize_image(image_bytes())
        with Image.open(BytesIO(result)) as image:
            self.assertEqual(image.format, "PNG")
            self.assertEqual(image.size, (8, 8))

    def test_oversized_data_rejected(self):
        with self.assertRaisesRegex(ValueError, "10 MB"):
            normalize_image(b"x" * (10 * 1024 * 1024 + 1))
