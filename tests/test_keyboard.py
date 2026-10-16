import ctypes
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from accessible_ai.desktop import Desktop, HookData
from accessible_ai.state import CornerDwell, ResponseState


class KeyboardTests(unittest.TestCase):
    def setUp(self):
        self.desktop = Desktop.__new__(Desktop)
        self.desktop.state = ResponseState()
        self.desktop.state.ready("AB")
        self.desktop.state.arm(42)
        self.desktop.pressed, self.desktop.suppressed = set(), set()
        self.desktop.pause_requested = False
        self.desktop.in_typing_corner = False
        self.desktop.awaiting_corner_exit = False
        self.desktop.dwell = CornerDwell()
        self.desktop.capture_requested = Mock()
        self.desktop.typer = Mock()
        self.desktop.typer.pause.side_effect = self.desktop.state.pause
        self.desktop.emit = Mock()
        self.desktop.dimensions = lambda: (1000, 800)
        self.desktop.user = SimpleNamespace(GetForegroundWindow=lambda: 42, CallNextHookEx=Mock(return_value=0), SetCursorPos=Mock())

    def event(self, virtual=0x41, message=0x100, flags=0):
        data = HookData(virtual, 0, flags, 0, 0)
        return self.desktop.keyboard_event(0, message, ctypes.addressof(data))

    def test_repeat_does_not_advance_and_final_keyup_is_suppressed(self):
        self.assertEqual(self.event(), 1)
        self.assertEqual(self.event(), 1)
        self.desktop.typer.request.assert_called_once()
        self.desktop.emit.assert_not_called()
        self.assertEqual(self.event(message=0x101), 1)
        self.assertEqual(self.event(), 1)
        self.assertEqual(self.desktop.typer.request.call_count, 2)
        self.desktop.state.advance(42, self.desktop.emit)
        self.assertTrue(self.desktop.state.advance(42, self.desktop.emit))
        self.desktop.typing_completed()
        self.desktop.user.SetCursorPos.assert_called_once_with(500, 400)
        self.assertEqual(self.event(message=0x101), 1)
        self.assertEqual(self.event(), 0)

    def test_injected_keys_pass_without_recursing(self):
        self.assertEqual(self.event(flags=0x10), 0)
        self.desktop.emit.assert_not_called()

    def test_extra_typing_is_blocked_until_pointer_leaves_bottom_left(self):
        self.desktop.state.advance(42, self.desktop.emit)
        self.desktop.state.advance(42, self.desktop.emit)
        self.desktop.in_typing_corner = True
        self.assertEqual(self.event(), 1)
        self.assertEqual(self.event(message=0x101), 1)
        self.desktop.typing_completed()
        self.desktop.user.SetCursorPos.assert_not_called()
        self.assertTrue(self.desktop.awaiting_corner_exit)
        self.assertEqual(self.event(virtual=0x42), 1)
        self.assertEqual(self.event(virtual=0x42, message=0x101), 1)
        self.desktop.observe_pointer(7, 799, 1000, 800, 1)
        self.assertFalse(self.desktop.awaiting_corner_exit)
        self.assertEqual(self.event(virtual=0x42), 0)

    def test_escape_pauses_without_being_typed(self):
        self.assertEqual(self.event(virtual=0x1B), 1)
        self.assertEqual(self.desktop.state.snapshot()["phase"], "ready")
        self.desktop.emit.assert_not_called()

    def test_ctrl_v_pastes_without_pausing(self):
        self.assertEqual(self.event(virtual=0xA2), 0)
        self.assertEqual(self.desktop.state.snapshot()["phase"], "typing")
        self.assertEqual(self.event(virtual=0x56), 1)
        self.desktop.typer.paste.assert_called_once()
        self.assertEqual(self.event(virtual=0x56, message=0x101), 1)
        self.assertEqual(self.event(virtual=0xA2, message=0x101), 0)

    def test_other_ctrl_shortcut_pauses_and_passes_through(self):
        self.assertEqual(self.event(virtual=0xA2), 0)
        self.assertEqual(self.event(virtual=0x43), 0)
        self.assertEqual(self.desktop.state.snapshot()["phase"], "ready")
        self.desktop.emit.assert_not_called()

    def test_paste_uses_clipboard_and_shortcut_events(self):
        self.desktop.user.GetAsyncKeyState = Mock(return_value=0)
        self.desktop.user.SendInput = Mock(side_effect=lambda count, events, size: count)
        with patch("accessible_ai.desktop.set_text") as clipboard:
            self.desktop.paste_remaining("Remaining text")
        clipboard.assert_called_once_with("Remaining text")
        count, events, size = self.desktop.user.SendInput.call_args.args
        self.assertEqual(count, 4)
        self.assertEqual([events[index].keyboard.virtual for index in range(count)],
                         [0x11, 0x56, 0x56, 0x11])
        self.assertEqual([events[index].keyboard.flags for index in range(count)], [0, 0, 2, 2])

    def test_paste_reuses_held_control_key(self):
        self.desktop.user.GetAsyncKeyState = Mock(return_value=-32768)
        self.desktop.user.SendInput = Mock(side_effect=lambda count, events, size: count)
        with patch("accessible_ai.desktop.set_text"):
            self.desktop.paste_remaining("Remaining text")
        count, events, size = self.desktop.user.SendInput.call_args.args
        self.assertEqual(count, 2)
        self.assertEqual([events[index].keyboard.virtual for index in range(count)], [0x56, 0x56])

    def test_paste_does_not_replace_clipboard_after_focus_change(self):
        self.desktop.user.GetForegroundWindow = lambda: 99
        with patch("accessible_ai.desktop.set_text") as clipboard:
            with self.assertRaisesRegex(OSError, "selected window changed"):
                self.desktop.paste_remaining("Remaining text")
        clipboard.assert_not_called()

    def test_modifier_held_before_arming_still_allows_shortcut(self):
        self.desktop.pressed.add(0xA2)
        self.assertEqual(self.event(), 0)
        self.assertEqual(self.desktop.state.snapshot()["phase"], "ready")
        self.desktop.emit.assert_not_called()

    def test_hook_queues_work_instead_of_injecting_inside_callback(self):
        self.desktop.typer = Mock()
        self.assertEqual(self.event(), 1)
        self.desktop.typer.request.assert_called_once()
        self.desktop.emit.assert_not_called()

    def test_hook_does_not_wait_for_state_lock_held_during_injection(self):
        self.desktop.typer = Mock()
        returned = threading.Event()
        callback = threading.Thread(target=lambda: (self.event(), returned.set()))
        self.desktop.state.lock.acquire()
        try:
            callback.start()
            self.assertTrue(returned.wait(0.1), "Hook waited for the output worker's state lock")
        finally:
            self.desktop.state.lock.release()
            callback.join(timeout=1)
