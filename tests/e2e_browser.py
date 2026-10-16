"""Run with python tests/e2e_browser.py; requires Playwright and a browser."""

from io import BytesIO
from pathlib import Path
import re
import socket
import subprocess
import sys
import tempfile
import time
import unittest

from PIL import Image
from playwright.sync_api import sync_playwright, expect


ROOT = Path(__file__).resolve().parents[1]
DEMO_RESPONSE = "This is a demo response. Each keypress types the next character."


class BrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.resources = []
        cls.addClassCleanup(cls.cleanup_resources)
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        cls.log = tempfile.TemporaryFile(mode="w+b")
        cls.resources.append(cls.log.close)
        cls.process = subprocess.Popen(
            [sys.executable, "-m", "accessible_ai", "--demo", "--no-desktop",
             "--host", "127.0.0.1", "--port", str(port)],
            cwd=ROOT, stdout=cls.log, stderr=subprocess.STDOUT,
            creationflags=0x08000000 if sys.platform == "win32" else 0,
        )
        cls.resources.append(cls.stop_app)
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            cls.log.seek(0)
            output = cls.log.read().decode("utf-8", errors="replace")
            ready_url = f"http://127.0.0.1:{port}/"
            if cls.process.poll() is not None:
                raise RuntimeError("Demo application exited during startup")
            if ready_url in output:
                try:
                    with socket.create_connection(("127.0.0.1", port), timeout=0.3):
                        break
                except OSError:
                    pass
            time.sleep(0.05)
        else:
            raise RuntimeError("Demo application did not start within 15 seconds")
        cls.base_url = f"http://127.0.0.1:{port}/"
        cls.playwright = sync_playwright().start()
        cls.resources.append(cls.playwright.stop)
        failures = []
        for channel in ("msedge", "chrome", None):
            try:
                options = {"channel": channel} if channel else {}
                cls.browser = cls.playwright.chromium.launch(headless=True, timeout=12000, **options)
                cls.resources.append(cls.browser.close)
                print(f"Browser: {channel or 'cached Chromium'} {cls.browser.version}", flush=True)
                break
            except Exception as error:
                failures.append(str(error).splitlines()[0])
        else:
            raise RuntimeError("No headless browser available: " + "; ".join(failures))
        output_image = BytesIO()
        Image.new("RGB", (96, 64), "#268977").save(output_image, "PNG")
        cls.image = {"name": "synthetic.png", "mimeType": "image/png", "buffer": output_image.getvalue()}

    @classmethod
    def stop_app(cls):
        if cls.process.poll() is None:
            cls.process.terminate()
            try:
                cls.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                cls.process.kill()
                cls.process.wait(timeout=5)

    @classmethod
    def cleanup_resources(cls):
        for cleanup in reversed(cls.resources):
            cleanup()

    def setUp(self):
        self.context = self.browser.new_context(
            viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True,
        )
        self.addCleanup(self.context.close)
        self.page = self.context.new_page()
        self.page.set_default_timeout(6000)
        self.errors = []
        self.page.on("pageerror", lambda error: self.errors.append(str(error)))

    def tearDown(self):
        self.assertEqual(self.errors, [], "Uncaught browser JavaScript errors")

    def open_app(self):
        self.page.goto(self.base_url)
        expect(self.page.locator("#connection")).to_have_text("Connected")
        self.assertEqual(self.page.url, self.base_url)

    def upload(self, selector):
        self.page.locator(selector).set_input_files(self.image)
        expect(self.page.locator("#preview")).to_be_visible()
        self.page.wait_for_function("document.querySelector('#preview').naturalWidth === 96")
        expect(self.page.locator("#filename")).to_contain_text("synthetic.png")
        expect(self.page.locator("#send")).to_be_enabled()
        self.page.locator("#prompt").fill("Read the synthetic image; demo must not call a provider.")
        self.page.locator("#send").click()
        expect(self.page.locator("#response")).to_have_text(DEMO_RESPONSE)
        expect(self.page.locator("#status")).to_contain_text("Select a textbox")
        expect(self.page.locator("#count")).to_have_text(f"0 / {len(DEMO_RESPONSE)} characters typed")
        expect(self.page.locator("#progress")).to_have_attribute("value", "0")
        expect(self.page.locator("#send")).to_be_enabled()

    def test_camera_and_file_upload_preview_and_response(self):
        self.open_app()
        expect(self.page.locator("#camera")).to_have_attribute("capture", "environment")
        self.upload("#camera")
        self.upload("#file")

    def test_connects_without_pairing(self):
        self.open_app()
        self.assertEqual(self.page.locator("#token").count(), 0)
        expect(self.page.locator("#send")).to_be_disabled()
        expect(self.page.locator("#error")).to_be_hidden()
        self.page.goto(self.base_url + "#token=obsolete")
        expect(self.page.locator("#connection")).to_have_text("Connected")
        self.assertEqual(self.page.url, self.base_url)

    def test_reload_and_socket_reconnect_preserve_response(self):
        self.open_app()
        self.upload("#file")
        self.page.reload()
        expect(self.page.locator("#connection")).to_have_text("Connected")
        expect(self.page.locator("#response")).to_have_text(DEMO_RESPONSE)
        self.page.evaluate("socket.close()")
        expect(self.page.locator("#connection")).to_have_text("Disconnected")
        expect(self.page.locator("#send")).to_be_disabled()
        expect(self.page.locator("#connection")).to_have_text("Connected")
        expect(self.page.locator("#response")).to_have_text(DEMO_RESPONSE)
        self.upload("#camera")

    def test_malformed_messages_and_damaged_image_allow_retry(self):
        self.open_app()
        for message, expected in (("[]", "Expected an image"),
                                  ('{"type":"image","data":"%%%"}', "base64")):
            self.page.evaluate("message => socket.send(message)", message)
            expect(self.page.locator("#error")).to_contain_text(re.compile(expected, re.IGNORECASE))
            expect(self.page.locator("#connection")).to_have_text("Connected")
        self.page.locator("#file").set_input_files(
            {"name": "damaged.png", "mimeType": "image/png", "buffer": b"not an image"},
        )
        self.page.locator("#send").click()
        expect(self.page.locator("#status")).to_contain_text("Unsupported or damaged image")
        expect(self.page.locator("#send")).to_be_enabled()
        self.upload("#file")
        expect(self.page.locator("#error")).to_be_hidden()

    def test_oversized_file_rejected_and_mobile_layout(self):
        self.open_app()
        self.page.locator("#file").set_input_files(
            {"name": "oversized.png", "mimeType": "image/png", "buffer": b"x" * (10 * 1024 * 1024 + 1)},
        )
        expect(self.page.locator("#error")).to_contain_text("exceeds 10 MB")
        expect(self.page.locator("#send")).to_be_disabled()
        for width in (320, 390, 768):
            self.page.set_viewport_size({"width": width, "height": 844})
            self.assertTrue(self.page.evaluate(
                "document.documentElement.scrollWidth <= window.innerWidth"), f"Overflow at {width}px")
        self.upload("#file")


if __name__ == "__main__":
    unittest.main(verbosity=2)
