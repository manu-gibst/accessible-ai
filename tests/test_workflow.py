import ctypes
import asyncio
from io import BytesIO
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from PIL import Image

from accessible_ai.desktop import Desktop, HookData
from accessible_ai.server import Application
from accessible_ai.state import CornerDwell, ResponseState
from accessible_ai.typing_worker import PacedTyper


class ScreenshotWorkflowTests(unittest.IsolatedAsyncioTestCase):
    async def test_ctrl_v_pastes_complete_remaining_answer(self):
        state = ResponseState()
        state.ready("abcd")
        state.arm(42)
        desktop = Desktop.__new__(Desktop)
        desktop.state = state
        desktop.pressed, desktop.suppressed = set(), set()
        desktop.pause_requested = False
        desktop.in_typing_corner = False
        desktop.awaiting_corner_exit = False
        desktop.dimensions = lambda: (1000, 800)
        desktop.user = SimpleNamespace(GetForegroundWindow=lambda: 42,
                                       GetAsyncKeyState=lambda virtual: -32768,
                                       CallNextHookEx=Mock(return_value=0),
                                       SetCursorPos=Mock(),
                                       SendInput=Mock(side_effect=lambda count, events, size: count))
        desktop.typer = PacedTyper(state, desktop.user.GetForegroundWindow,
                                  Mock(), desktop.typing_completed, paste=desktop.paste_remaining)
        desktop.typer.start()
        self.addCleanup(desktop.typer.stop)
        control = HookData(0xA2, 0, 0, 0, 0)
        paste_key = HookData(0x56, 0, 0, 0, 0)
        with patch("accessible_ai.desktop.set_text") as clipboard:
            self.assertEqual(desktop.keyboard_event(0, 0x100, ctypes.addressof(control)), 0)
            self.assertEqual(desktop.keyboard_event(0, 0x100, ctypes.addressof(paste_key)), 1)
            for attempt in range(100):
                if state.snapshot()["phase"] == "done":
                    break
                await asyncio.sleep(0.005)
        clipboard.assert_called_once_with("abcd")
        self.assertEqual(state.snapshot()["position"], 4)
        desktop.user.SendInput.assert_called_once()
        desktop.user.SetCursorPos.assert_called_once_with(500, 400)

    async def test_capture_corner_to_response_to_keypress_output_and_completion(self):
        output = BytesIO()
        Image.new("RGB", (32, 32), "white").save(output, "PNG")
        capture = Mock(return_value=output.getvalue())
        provider = SimpleNamespace(generate=AsyncMock(return_value="Hi😀"))
        state = ResponseState()
        app = Application(state, provider, capture=capture)
        corner = CornerDwell(dwell=0.4)

        corner.update(999, 0, 1000, 800, 0)
        self.assertEqual(corner.update(999, 0, 1000, 800, 0.5), "capture")
        self.assertTrue(app.submit())
        self.assertFalse(app.submit())
        await app.job
        capture.assert_called_once()
        provider.generate.assert_awaited_once()
        self.assertEqual(state.snapshot()["phase"], "ready")

        desktop = Desktop.__new__(Desktop)
        desktop.state = state
        desktop.pressed, desktop.suppressed = set(), set()
        desktop.pause_requested = False
        desktop.in_typing_corner = False
        desktop.awaiting_corner_exit = False
        typed = []
        desktop.emit = typed.append
        desktop.dimensions = lambda: (1000, 800)
        desktop.user = SimpleNamespace(GetForegroundWindow=lambda: 42, CallNextHookEx=Mock(return_value=0), SetCursorPos=Mock())
        desktop.typer = PacedTyper(state, desktop.user.GetForegroundWindow, desktop.emit, desktop.typing_completed, interval=0.001)
        desktop.typer.start()
        self.addCleanup(desktop.typer.stop)
        corner.update(0, 799, 1000, 800, 1)
        self.assertEqual(corner.update(0, 799, 1000, 800, 1.5), "type")
        self.assertTrue(state.arm(42))

        physical_key = HookData(0x41, 0, 0, 0, 0)
        for expected in ["H", "i", "😀"]:
            self.assertEqual(desktop.keyboard_event(0, 0x100, ctypes.addressof(physical_key)), 1)
            for attempt in range(100):
                if typed and typed[-1] == expected:
                    break
                await asyncio.sleep(0.005)
            self.assertEqual(typed[-1], expected)
            self.assertEqual(desktop.keyboard_event(0, 0x101, ctypes.addressof(physical_key)), 1)
        self.assertEqual("".join(typed), "Hi😀")
        self.assertEqual(state.snapshot()["phase"], "done")
        desktop.user.SetCursorPos.assert_called_once_with(500, 400)
        self.assertEqual(desktop.keyboard_event(0, 0x100, ctypes.addressof(physical_key)), 0)
