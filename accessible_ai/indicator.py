import ctypes
from ctypes import wintypes
import logging
import os
import threading
import time


class StatusIndicator:
    def __init__(self, state):
        self.state = state
        self._lock = threading.RLock()
        self._stopping = threading.Event()
        self._hidden = threading.Event()
        self._hidden.set()
        self._suspended = False
        self._thread = None

    def start(self):
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stopping.clear()
            self._thread = threading.Thread(target=self._run, name="status-indicator", daemon=True)
            self._thread.start()

    def stop(self):
        with self._lock:
            self._stopping.set()
            thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=1)

    def suspend(self):
        deadline = time.monotonic() + 1
        self._suspended = True
        if not self._lock.acquire(timeout=1):
            return False
        try:
            if self._thread is None or not self._thread.is_alive():
                return True
            self._hidden.clear()
        finally:
            self._lock.release()
        return self._hidden.wait(timeout=max(0, deadline - time.monotonic()))

    def resume(self):
        with self._lock:
            self._suspended = False

    def _run(self):
        root = dot = None
        try:
            if os.name != "nt":
                raise RuntimeError("Windows is required")
            import tkinter as tk

            root = tk.Tk()
            root.withdraw()
            root.overrideredirect(True)
            root.attributes("-topmost", True)
            dot = tk.Canvas(root, width=7, height=7, background="#b70000",
                            highlightthickness=0, borderwidth=0, takefocus=False)
            dot.pack()
            root.update_idletasks()
            user = ctypes.WinDLL("user32", use_last_error=True)
            user.GetParent.argtypes = [wintypes.HWND]
            user.GetParent.restype = wintypes.HWND
            get_style = user.GetWindowLongPtrW if ctypes.sizeof(ctypes.c_void_p) == 8 else user.GetWindowLongW
            set_style = user.SetWindowLongPtrW if ctypes.sizeof(ctypes.c_void_p) == 8 else user.SetWindowLongW
            get_style.argtypes = [wintypes.HWND, ctypes.c_int]
            get_style.restype = ctypes.c_ssize_t
            set_style.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
            set_style.restype = ctypes.c_ssize_t
            user.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
            user.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int,
                                          ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT]
            user.SystemParametersInfoW.argtypes = [wintypes.UINT, wintypes.UINT,
                                                   ctypes.c_void_p, wintypes.UINT]
            handle = user.GetParent(root.winfo_id()) or root.winfo_id()
            set_style(handle, -20, get_style(handle, -20) | 0x08000000 | 0x80 | 0x20 | 0x80000)
            user.SetLayeredWindowAttributes.argtypes = [wintypes.HWND, wintypes.DWORD,
                                                         ctypes.c_ubyte, wintypes.DWORD]
            user.SetLayeredWindowAttributes(handle, 0, 255, 2)
            graphics = ctypes.WinDLL("gdi32", use_last_error=True)
            graphics.CreateEllipticRgn.argtypes = [ctypes.c_int] * 4
            graphics.CreateEllipticRgn.restype = wintypes.HANDLE
            graphics.DeleteObject.argtypes = [wintypes.HANDLE]
            user.SetWindowRgn.argtypes = [wintypes.HWND, wintypes.HANDLE, wintypes.BOOL]
            def tick():
                with self._lock:
                    if self._stopping.is_set():
                        user.ShowWindow(handle, 0)
                        self._hidden.set()
                        root.quit()
                        return
                    if self._suspended or self.state.snapshot().get("phase") != "generating":
                        user.ShowWindow(handle, 0)
                        self._hidden.set()
                    else:
                        work = wintypes.RECT(0, 0, root.winfo_screenwidth(), root.winfo_screenheight())
                        user.SystemParametersInfoW(0x30, 0, ctypes.byref(work), 0)
                        width, height = root.winfo_reqwidth(), root.winfo_reqheight()
                        region = graphics.CreateEllipticRgn(0, 0, width, height)
                        if region and not user.SetWindowRgn(handle, region, True):
                            graphics.DeleteObject(region)
                        if not user.SetWindowPos(handle, -1, work.right - width - 22,
                                                 work.top + 22, width, height, 0x10 | 0x40):
                            raise ctypes.WinError(ctypes.get_last_error())
                        user.ShowWindow(handle, 4)
                        self._hidden.clear()
                root.after(50, safe_tick)

            def safe_tick():
                try:
                    tick()
                except Exception:
                    logging.getLogger(__name__).warning("Desktop status indicator unavailable", exc_info=True)
                    root.quit()

            root.after(0, safe_tick)
            root.mainloop()
        except Exception:
            logging.getLogger(__name__).warning("Desktop status indicator unavailable", exc_info=True)
        finally:
            if root is not None:
                try:
                    root.destroy()
                except Exception:
                    pass
            root = dot = None
            self._hidden.set()
