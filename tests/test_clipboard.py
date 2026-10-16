import ctypes
import unittest
from unittest.mock import Mock, patch

from accessible_ai.clipboard import set_text


class ClipboardTests(unittest.TestCase):
    def setUp(self):
        self.user, self.kernel = Mock(), Mock()
        self.window = 101
        self.memory = 202
        self.buffer = ctypes.create_string_buffer(128)
        self.user.CreateWindowExW.return_value = self.window
        self.user.OpenClipboard.return_value = True
        self.user.EmptyClipboard.return_value = True
        self.user.SetClipboardData.return_value = self.memory
        self.kernel.GlobalAlloc.return_value = self.memory
        self.kernel.GlobalLock.return_value = ctypes.addressof(self.buffer)

    def test_unicode_text_is_transferred_to_clipboard_and_window_is_closed(self):
        with patch("accessible_ai.clipboard.ctypes.WinDLL", side_effect=[self.user, self.kernel]):
            set_text("A\n日本語 😀")
        expected = "A\r\n日本語 😀".encode("utf-16-le") + b"\x00\x00"
        self.assertEqual(self.buffer.raw[:len(expected)], expected)
        self.user.OpenClipboard.assert_called_once_with(self.window)
        self.user.SetClipboardData.assert_called_once_with(13, self.memory)
        self.kernel.GlobalFree.assert_not_called()
        self.user.CloseClipboard.assert_called_once()
        self.user.DestroyWindow.assert_called_once_with(self.window)

    def test_busy_clipboard_frees_memory_and_closes_window(self):
        self.user.OpenClipboard.return_value = False
        with patch("accessible_ai.clipboard.ctypes.WinDLL", side_effect=[self.user, self.kernel]), \
             patch("accessible_ai.clipboard.time.sleep"):
            with self.assertRaisesRegex(OSError, "clipboard is busy"):
                set_text("answer")
        self.kernel.GlobalFree.assert_called_once_with(self.memory)
        self.user.CloseClipboard.assert_not_called()
        self.user.DestroyWindow.assert_called_once_with(self.window)

    def test_null_character_rejected_before_clipboard_changes(self):
        with patch("accessible_ai.clipboard.ctypes.WinDLL") as library:
            with self.assertRaisesRegex(OSError, "cannot be pasted"):
                set_text("before\x00after")
        library.assert_not_called()


if __name__ == "__main__":
    unittest.main()
