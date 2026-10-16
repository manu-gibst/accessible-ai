import ctypes
from ctypes import wintypes
from io import BytesIO
import os
import threading
import time

from .clipboard import set_text
from .state import CornerDwell
from .indicator import StatusIndicator
from .typing_worker import PacedTyper


class KeyboardInput(ctypes.Structure):
    _fields_ = [("virtual", wintypes.WORD), ("scan", wintypes.WORD),
                ("flags", wintypes.DWORD), ("time", wintypes.DWORD), ("extra", ctypes.c_size_t)]


class MouseInput(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG), ("data", wintypes.DWORD),
                ("flags", wintypes.DWORD), ("time", wintypes.DWORD), ("extra", ctypes.c_size_t)]


class InputUnion(ctypes.Union):
    _fields_ = [("keyboard", KeyboardInput), ("mouse", MouseInput)]


class Input(ctypes.Structure):
    _anonymous_ = ("value",)
    _fields_ = [("type", wintypes.DWORD), ("value", InputUnion)]


class HookData(ctypes.Structure):
    _fields_ = [("virtual", wintypes.DWORD), ("scan", wintypes.DWORD),
                ("flags", wintypes.DWORD), ("time", wintypes.DWORD), ("extra", ctypes.c_size_t)]


def is_typing_key(virtual):
    return virtual in {0x08, 0x09, 0x0D, 0x20, 0xE2} or 0x30 <= virtual <= 0x5A or 0x60 <= virtual <= 0x6F or 0xBA <= virtual <= 0xC0 or 0xDB <= virtual <= 0xDF


class Desktop:
    def __init__(self, state, capture_requested, dwell=0.45):
        if os.name != "nt":
            raise RuntimeError("Desktop control requires Windows. Use --no-desktop for the phone interface only.")
        self.state, self.capture_requested = state, capture_requested
        self.dwell = CornerDwell(dwell)
        self.user = ctypes.WinDLL("user32", use_last_error=True)
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.callback_type = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
        self.user.SetWindowsHookExW.argtypes = [ctypes.c_int, self.callback_type, wintypes.HINSTANCE, wintypes.DWORD]
        self.user.SetWindowsHookExW.restype = wintypes.HANDLE
        self.user.CallNextHookEx.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
        self.user.CallNextHookEx.restype = ctypes.c_ssize_t
        self.user.UnhookWindowsHookEx.argtypes = [wintypes.HANDLE]
        self.user.GetForegroundWindow.restype = wintypes.HWND
        self.user.GetAsyncKeyState.argtypes = [ctypes.c_int]
        self.user.GetAsyncKeyState.restype = ctypes.c_short
        self.user.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(Input), ctypes.c_int]
        self.user.SendInput.restype = wintypes.UINT
        self.kernel.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
        self.kernel.GetModuleHandleW.restype = wintypes.HMODULE
        self.kernel.GetCurrentThreadId.restype = wintypes.DWORD
        self.user.SetProcessDpiAwarenessContext.argtypes = [wintypes.HANDLE]
        self.user.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        self.stopping, self.started = threading.Event(), threading.Event()
        self.pressed, self.suppressed = set(), set()
        self.pause_requested = False
        self.in_typing_corner = False
        self.awaiting_corner_exit = False
        self.typer = PacedTyper(state, self.user.GetForegroundWindow, self.emit,
                               self.typing_completed, paste=self.paste_remaining)
        self.indicator = StatusIndicator(state)
        self.error, self.thread_id = None, None
        self.hook_thread = threading.Thread(target=self.keyboard_loop, name="keyboard-hook", daemon=True)
        self.corner_thread = threading.Thread(target=self.corner_loop, name="corner-monitor", daemon=True)

    def dimensions(self):
        return self.user.GetSystemMetrics(0), self.user.GetSystemMetrics(1)

    def capture(self):
        from PIL import ImageGrab
        try:
            if not self.indicator.suspend():
                raise OSError("Could not hide the status indicator before taking a screenshot.")
            width, height = self.dimensions()
            output = BytesIO()
            ImageGrab.grab(bbox=(0, 0, width, height)).save(output, "PNG")
            return output.getvalue()
        finally:
            self.indicator.resume()

    def emit(self, character):
        events = []
        if character in {"\n", "\t"}:
            virtual = 0x0D if character == "\n" else 0x09
            events = [Input(type=1, keyboard=KeyboardInput(virtual, 0, flags, 0, 0)) for flags in (0, 2)]
        else:
            encoded = character.encode("utf-16-le")
            for offset in range(0, len(encoded), 2):
                unit = int.from_bytes(encoded[offset:offset + 2], "little")
                events.extend(Input(type=1, keyboard=KeyboardInput(0, unit, flags, 0, 0)) for flags in (4, 6))
        batch = (Input * len(events))(*events)
        if self.user.SendInput(len(events), batch, ctypes.sizeof(Input)) != len(events):
            raise OSError("Windows rejected text input. Check that the target app is not running as administrator.")

    def paste_remaining(self, text):
        if self.user.GetForegroundWindow() != self.state.target:
            raise OSError("The selected window changed before pasting.")
        set_text(text)
        if self.user.GetForegroundWindow() != self.state.target:
            raise OSError("The selected window changed before pasting.")
        events = []
        if self.user.GetAsyncKeyState(0x11) >= 0:
            events.append(Input(type=1, keyboard=KeyboardInput(0x11, 0, 0, 0, 0)))
        events.extend(Input(type=1, keyboard=KeyboardInput(0x56, 0, flags, 0, 0))
                      for flags in (0, 2))
        if len(events) == 3:
            events.append(Input(type=1, keyboard=KeyboardInput(0x11, 0, 2, 0, 0)))
        batch = (Input * len(events))(*events)
        if self.user.SendInput(len(events), batch, ctypes.sizeof(Input)) != len(events):
            raise OSError("Windows rejected paste input. Check that the target app is not running as administrator.")

    def keyboard_event(self, code, message, pointer):
        if code >= 0:
            event = ctypes.cast(pointer, ctypes.POINTER(HookData)).contents
            if not event.flags & 0x10:
                virtual = event.virtual
                down, up = message in {0x100, 0x104}, message in {0x101, 0x105}
                repeated = virtual in self.pressed
                if down:
                    self.pressed.add(virtual)
                elif up:
                    self.pressed.discard(virtual)
                if virtual in self.suppressed:
                    if up:
                        self.suppressed.discard(virtual)
                    return 1
                modifiers = {0x11, 0x12, 0x5B, 0x5C, 0xA2, 0xA3, 0xA4, 0xA5}
                if down and (self.awaiting_corner_exit or
                             (self.state.keyboard_context[0] == "done" and self.in_typing_corner)):
                    if is_typing_key(virtual) and not self.pressed & modifiers:
                        self.suppressed.add(virtual)
                        return 1
                if down and self.state.keyboard_context[0] == "typing" and not self.pause_requested:
                    if virtual == 0x1B:
                        self.pause_requested = True
                        self.typer.pause()
                        self.suppressed.add(virtual)
                        return 1
                    if virtual in {0x10, 0xA0, 0xA1}:
                        return self.user.CallNextHookEx(None, code, message, pointer)
                    if virtual in {0x11, 0xA2, 0xA3}:
                        return self.user.CallNextHookEx(None, code, message, pointer)
                    if virtual == 0x56 and self.pressed & {0x11, 0xA2, 0xA3} and not self.pressed & {0x10, 0xA0, 0xA1, 0x12, 0xA4, 0xA5, 0x5B, 0x5C}:
                        self.suppressed.add(virtual)
                        if not repeated:
                            self.typer.paste()
                        return 1
                    if not is_typing_key(virtual) or self.pressed & modifiers:
                        self.pause_requested = True
                        self.typer.pause()
                    else:
                        self.suppressed.add(virtual)
                        if not repeated:
                            self.typer.request()
                        return 1
        return self.user.CallNextHookEx(None, code, message, pointer)

    def typing_completed(self):
        if self.in_typing_corner:
            self.awaiting_corner_exit = True
            return
        width, height = self.dimensions()
        self.user.SetCursorPos(width // 2, height // 2)

    def observe_pointer(self, x, y, width, height, now):
        self.in_typing_corner = 0 <= x < self.dwell.margin and height - self.dwell.margin <= y < height
        if self.awaiting_corner_exit and not self.in_typing_corner:
            self.awaiting_corner_exit = False
        action = self.dwell.update(x, y, width, height, now)
        if action == "capture":
            self.capture_requested()
        elif action == "type":
            if self.state.arm(self.user.GetForegroundWindow()):
                self.pause_requested = False

    def keyboard_loop(self):
        self.thread_id = self.kernel.GetCurrentThreadId()
        self.callback = self.callback_type(self.keyboard_event)
        hook = self.user.SetWindowsHookExW(13, self.callback, self.kernel.GetModuleHandleW(None), 0)
        if not hook:
            self.error = ctypes.WinError(ctypes.get_last_error())
            self.started.set()
            return
        message = wintypes.MSG()
        self.user.PeekMessageW(ctypes.byref(message), None, 0, 0, 0)
        self.started.set()
        try:
            while self.user.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
                self.user.TranslateMessage(ctypes.byref(message))
                self.user.DispatchMessageW(ctypes.byref(message))
        finally:
            self.user.UnhookWindowsHookEx(hook)

    def corner_loop(self):
        while not self.stopping.wait(0.04):
            point = wintypes.POINT()
            if not self.user.GetCursorPos(ctypes.byref(point)):
                continue
            width, height = self.dimensions()
            self.observe_pointer(point.x, point.y, width, height, time.monotonic())

    def start(self):
        self.typer.start()
        self.hook_thread.start()
        if not self.started.wait(5):
            raise RuntimeError("Windows keyboard hook did not start.")
        if self.error:
            raise self.error
        self.corner_thread.start()
        self.indicator.start()

    def stop(self):
        self.indicator.stop()
        self.typer.stop()
        self.state.pause()
        self.stopping.set()
        if self.thread_id:
            self.user.PostThreadMessageW(self.thread_id, 0x12, 0, 0)
        if self.hook_thread.is_alive():
            self.hook_thread.join(timeout=2)
        if self.corner_thread.is_alive():
            self.corner_thread.join(timeout=2)
