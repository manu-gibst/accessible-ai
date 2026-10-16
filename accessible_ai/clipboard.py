import ctypes
from ctypes import wintypes
import time


def set_text(text):
    if "\x00" in text:
        raise OSError("The response contains a character that cannot be pasted.")
    user = ctypes.WinDLL("user32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    user.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR,
                                     wintypes.DWORD, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                     ctypes.c_int, wintypes.HWND, wintypes.HMENU,
                                     wintypes.HINSTANCE, ctypes.c_void_p]
    user.CreateWindowExW.restype = wintypes.HWND
    user.DestroyWindow.argtypes = [wintypes.HWND]
    user.OpenClipboard.argtypes = [wintypes.HWND]
    user.EmptyClipboard.restype = wintypes.BOOL
    user.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
    user.SetClipboardData.restype = wintypes.HANDLE
    kernel.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    kernel.GlobalAlloc.restype = wintypes.HANDLE
    kernel.GlobalLock.argtypes = [wintypes.HANDLE]
    kernel.GlobalLock.restype = ctypes.c_void_p
    kernel.GlobalUnlock.argtypes = [wintypes.HANDLE]
    kernel.GlobalFree.argtypes = [wintypes.HANDLE]

    window = user.CreateWindowExW(0, "STATIC", "", 0, 0, 0, 0, 0,
                                  ctypes.c_void_p(-3), None, None, None)
    if not window:
        raise ctypes.WinError(ctypes.get_last_error())
    memory = None
    opened = False
    try:
        encoded = text.replace("\n", "\r\n").encode("utf-16-le") + b"\x00\x00"
        memory = kernel.GlobalAlloc(2, len(encoded))
        if not memory:
            raise ctypes.WinError(ctypes.get_last_error())
        pointer = kernel.GlobalLock(memory)
        if not pointer:
            raise ctypes.WinError(ctypes.get_last_error())
        ctypes.memmove(pointer, encoded, len(encoded))
        kernel.GlobalUnlock(memory)
        for attempt in range(10):
            if user.OpenClipboard(window):
                opened = True
                break
            time.sleep(0.02)
        if not opened:
            raise OSError("The clipboard is busy. Try Ctrl+V again.")
        if not user.EmptyClipboard():
            raise ctypes.WinError(ctypes.get_last_error())
        if not user.SetClipboardData(13, memory):
            raise ctypes.WinError(ctypes.get_last_error())
        memory = None
    finally:
        if opened:
            user.CloseClipboard()
        if memory:
            kernel.GlobalFree(memory)
        user.DestroyWindow(window)
