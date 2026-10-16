import ctypes
from ctypes import wintypes
import os
import threading
import time
import unittest

from accessible_ai.indicator import StatusIndicator


class SnapshotState:
    def __init__(self):
        self.lock = threading.Lock()
        self.phase = "generating"

    def snapshot(self):
        with self.lock:
            return dict(phase=self.phase, position=12, total=50,
                        message="PRIVATE", response="PRIVATE")

    def set_phase(self, phase):
        with self.lock:
            self.phase = phase


@unittest.skipUnless(os.name == "nt", "Windows native indicator smoke test")
class NativeIndicatorTests(unittest.TestCase):
    def test_visibility_and_foreground_focus_are_preserved(self):
        user = ctypes.WinDLL("user32", use_last_error=True)
        callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        user.EnumThreadWindows.argtypes = [wintypes.DWORD, callback_type, wintypes.LPARAM]
        user.GetForegroundWindow.restype = wintypes.HWND
        user.IsWindowVisible.argtypes = [wintypes.HWND]
        user.IsWindow.argtypes = [wintypes.HWND]
        user.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        user.SystemParametersInfoW.argtypes = [wintypes.UINT, wintypes.UINT,
                                               ctypes.c_void_p, wintypes.UINT]

        class GuiInfo(ctypes.Structure):
            _fields_ = [("size", wintypes.DWORD), ("flags", wintypes.DWORD),
                        ("active", wintypes.HWND), ("focus", wintypes.HWND),
                        ("capture", wintypes.HWND), ("menu", wintypes.HWND),
                        ("move", wintypes.HWND), ("caret", wintypes.HWND),
                        ("caret_rect", wintypes.RECT)]

        user.GetGUIThreadInfo.argtypes = [wintypes.DWORD, ctypes.POINTER(GuiInfo)]

        def focus():
            info = GuiInfo(size=ctypes.sizeof(GuiInfo))
            self.assertTrue(user.GetGUIThreadInfo(0, ctypes.byref(info)))
            return user.GetForegroundWindow(), info.focus

        def wait_for(predicate):
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                result = predicate()
                if result:
                    return result
                time.sleep(0.02)
            self.fail("Indicator did not reach the expected native visibility state")

        state = SnapshotState()
        indicator = StatusIndicator(state)
        handles = []

        @callback_type
        def collect(handle, parameter):
            if user.IsWindowVisible(handle):
                handles.append(handle)
            return True

        def visible_handle():
            handles.clear()
            user.EnumThreadWindows(indicator._thread.native_id, collect, 0)
            return handles[0] if handles else None

        try:
            indicator.start()
            handle = wait_for(visible_handle)
            self.assertNotEqual(focus()[0], handle)
            rectangle, work = wintypes.RECT(), wintypes.RECT()
            self.assertTrue(user.GetWindowRect(handle, ctypes.byref(rectangle)))
            self.assertTrue(user.SystemParametersInfoW(0x30, 0, ctypes.byref(work), 0))
            self.assertEqual(rectangle.right - rectangle.left, 7)
            self.assertEqual(rectangle.bottom - rectangle.top, 7)
            self.assertGreater(work.right - rectangle.right, 6)
            self.assertGreater(rectangle.top - work.top, 6)
            self.assertTrue(indicator.suspend())
            self.assertFalse(user.IsWindowVisible(handle))
            indicator.resume()
            wait_for(lambda: user.IsWindowVisible(handle))
            for phase in ("ready", "typing", "done", "error", "idle"):
                state.set_phase(phase)
                wait_for(lambda: not user.IsWindowVisible(handle))
                time.sleep(0.1)
                self.assertFalse(user.IsWindowVisible(handle))
                self.assertNotEqual(focus()[0], handle)
            state.set_phase("generating")
            wait_for(lambda: user.IsWindowVisible(handle))
        finally:
            indicator.stop()
        self.assertFalse(indicator._thread.is_alive())
        self.assertFalse(user.IsWindow(handle))
        self.assertNotEqual(focus()[0], handle)


if __name__ == "__main__":
    unittest.main()
