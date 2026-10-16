"""Exercise a running hosted deployment; one successful run uses real Claude usage."""

import argparse
from io import BytesIO
import ipaddress
import json
import sys
import time
from urllib.parse import urlsplit

from PIL import Image, ImageDraw, ImageFont
from playwright.sync_api import expect, sync_playwright


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="https://manu.wiphk.com/")
    parser.add_argument("--host-ip", help="Optional DNS override; certificate verification remains enabled")
    parser.add_argument("--timeout", type=float, default=120)
    options = parser.parse_args()
    target = urlsplit(options.url)
    if target.scheme != "https" or not target.hostname or target.fragment or target.query:
        parser.error("Use a plain HTTPS URL without a query or fragment")
    if options.timeout <= 0:
        parser.error("Timeout must be positive")
    arguments = []
    if options.host_ip:
        ipaddress.ip_address(options.host_ip)
        arguments.append(f"--host-resolver-rules=MAP {target.hostname} {options.host_ip}")
    started = time.monotonic()
    try:
        with sync_playwright() as playwright:
            browser = None
            for channel in ("msedge", "chrome", None):
                try:
                    launch_options = {"channel": channel} if channel else {}
                    browser = playwright.chromium.launch(
                        headless=True, timeout=12000, args=arguments, **launch_options,
                    )
                    print(f"Browser: {channel or 'cached Chromium'} {browser.version}", flush=True)
                    break
                except Exception:
                    continue
            if browser is None:
                raise RuntimeError("No supported headless browser could start")
            try:
                context = browser.new_context(
                    viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True,
                )
                page = context.new_page()
                page.set_default_timeout(15000)
                errors = []
                statuses = []
                page.on("pageerror", lambda error: errors.append(str(error)))

                def frame_received(payload):
                    try:
                        message = json.loads(payload)
                    except (ValueError, TypeError):
                        return
                    if isinstance(message, dict) and message.get("type") == "status":
                        statuses.append(message)

                page.on("websocket", lambda websocket: websocket.on("framereceived", frame_received))
                response = page.goto(options.url, timeout=25000)
                assert response is not None and response.status == 200, "HTTPS page must return 200"
                assert response.headers.get("cache-control") == "no-store", "Missing no-store header"
                assert response.headers.get("referrer-policy") == "no-referrer", "Missing referrer protection"
                expect(page.locator("#connection")).to_have_text("Connected")
                expect(page.locator("#send")).to_be_disabled()
                assert page.locator("#token").count() == 0, "Pairing controls are still present"
                expect(page.locator("#error")).to_be_hidden()
                print("PASS: HTTPS page and direct WSS connection", flush=True)
                image = Image.new("RGB", (640, 240), "white")
                font = ImageFont.load_default(size=72)
                ImageDraw.Draw(image).text((40, 70), "TEST_OK", fill="black", font=font)
                encoded = BytesIO()
                image.save(encoded, "PNG")
                page.locator("#camera").set_input_files(
                    {"name": "synthetic-hosted.png", "mimeType": "image/png", "buffer": encoded.getvalue()},
                )
                page.wait_for_function("document.querySelector('#preview').naturalWidth === 640")
                page.locator("#prompt").fill("Read the text in this image. Return only the exact text, no explanation or formatting.")
                statuses.clear()
                request_started = time.monotonic()
                page.locator("#send").click()
                expect(page.locator("#response")).to_have_text("TEST_OK", timeout=options.timeout * 1000)
                expect(page.locator("#send")).to_be_enabled()
                assert any(message.get("phase") == "ready" and message.get("response") == "TEST_OK"
                           for message in statuses), "No new ready response received"
                expect(page.locator("#count")).to_have_text("0 / 7 characters typed")
                print(f"PASS: synthetic camera input -> real Claude -> TEST_OK ({time.monotonic() - request_started:.1f}s)", flush=True)
                page.evaluate("socket.close()")
                expect(page.locator("#connection")).to_have_text("Disconnected")
                expect(page.locator("#send")).to_be_disabled()
                expect(page.locator("#connection")).to_have_text("Connected")
                expect(page.locator("#response")).to_have_text("TEST_OK")
                expect(page.locator("#send")).to_be_enabled()
                page.reload()
                expect(page.locator("#connection")).to_have_text("Connected")
                expect(page.locator("#response")).to_have_text("TEST_OK")
                page.evaluate("socket.send('[]')")
                expect(page.locator("#error")).to_contain_text("Expected an image")
                expect(page.locator("#connection")).to_have_text("Connected")
                assert not errors, "Uncaught browser JavaScript exception"
                print(f"PASS: reconnect, reload persistence, malformed-message recovery; total {time.monotonic() - started:.1f}s", flush=True)
            finally:
                browser.close()
    except Exception as error:
        print(f"FAIL: {type(error).__name__}: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
