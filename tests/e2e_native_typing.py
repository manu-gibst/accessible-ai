"""Opt-in Windows typing test using a disposable foreground textbox."""

import ctypes
from ctypes import wintypes
from pathlib import Path
import sys
import time
import tkinter

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from accessible_ai.desktop import Desktop, HookData, Input, KeyboardInput
from accessible_ai.state import ResponseState


def main():
    state = ResponseState()
    desktop = Desktop(state, lambda: None)
    desktop.typer.start()
    user = desktop.user
    previous = user.GetForegroundWindow()
    user.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
    user.GetAncestor.restype = wintypes.HWND
    user.SetForegroundWindow.argtypes = [wintypes.HWND]
    root = tkinter.Tk()
    root.title("Accessible AI: disposable typing test")
    root.geometry("640x240")
    textbox = tkinter.Text(root)
    textbox.pack(fill="both", expand=True)
    root.update()
    target = user.GetAncestor(root.winfo_id(), 2)
    user.SetForegroundWindow(target)
    textbox.focus_force()
    root.update()
    if user.GetForegroundWindow() != target:
        root.destroy()
        raise RuntimeError("The disposable textbox did not receive focus; no input was injected.")
    expected = "I couldn't find a question in the image.\nWrangler 4.151.0; https://example.com/#token=ABC123\nUnicode: café 日本語 😀\n" * 3
    original_center = user.SetCursorPos
    user.SetCursorPos = lambda *arguments: True
    try:
        for interval in (() if "--hook-only" in sys.argv else (1, 20)):
            textbox.delete("1.0", "end")
            state.ready(expected)
            state.arm(target)
            for position in range(len(expected)):
                if user.GetForegroundWindow() != target:
                    raise RuntimeError("Test textbox lost focus; injection stopped.")
                event = HookData(0x41, 0, 0, 0, 0)
                desktop.keyboard_event(0, 0x100, ctypes.addressof(event))
                desktop.keyboard_event(0, 0x101, ctypes.addressof(event))
                if (position + 1) % interval == 0:
                    root.update()
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                root.update()
                if textbox.get("1.0", "end-1c") == expected:
                    break
                time.sleep(0.01)
            actual = textbox.get("1.0", "end-1c")
            if actual != expected:
                mismatch = next((index for index, pair in enumerate(zip(actual, expected)) if pair[0] != pair[1]), min(len(actual), len(expected)))
                raise AssertionError(f"Native typing mismatch at {mismatch}, update interval {interval}: expected {expected[mismatch:mismatch + 20]!r}, actual {actual[mismatch:mismatch + 20]!r}; lengths {len(expected)}/{len(actual)}, phase {state.phase}, position {state.position}, foreground {user.GetForegroundWindow()}, target {target}, worker alive {desktop.typer.thread.is_alive()}")
            print(f"Native textbox: {len(expected)} characters correct, message-pump interval {interval}.", flush=True)
        textbox.delete("1.0", "end")
        root.update()
        state.ready(expected)
        state.arm(target)
        original_event = desktop.keyboard_event
        marker = 0x41495445

        def marked_event(code, message, pointer):
            if code >= 0:
                data = ctypes.cast(pointer, ctypes.POINTER(HookData)).contents
                if data.extra == marker:
                    if user.GetForegroundWindow() != target:
                        state.pause()
                        return 1
                    copied = HookData(data.virtual, data.scan, data.flags & ~0x10, data.time, data.extra)
                    return original_event(code, message, ctypes.addressof(copied))
            return user.CallNextHookEx(None, code, message, pointer)

        desktop.keyboard_event = marked_event
        desktop.hook_thread.start()
        if not desktop.started.wait(5) or desktop.error:
            raise RuntimeError(f"Test hook did not start: {desktop.error}")
        for position in range(len(expected)):
            if user.GetForegroundWindow() != target:
                raise RuntimeError("Test textbox lost focus; injection stopped.")
            events = (Input * 2)(*[Input(type=1, keyboard=KeyboardInput(0x41, 0, flags, 0, marker)) for flags in (0, 2)])
            if user.SendInput(2, events, ctypes.sizeof(Input)) != 2:
                raise RuntimeError("Windows rejected the tagged test keypress.")
            root.update()
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            root.update()
            if textbox.get("1.0", "end-1c") == expected:
                break
            time.sleep(0.01)
        actual = textbox.get("1.0", "end-1c")
        if actual != expected:
            mismatch = next((index for index, pair in enumerate(zip(actual, expected)) if pair[0] != pair[1]), min(len(actual), len(expected)))
            raise AssertionError(f"Real-hook typing mismatch at {mismatch}: expected {expected[mismatch:mismatch + 25]!r}, actual {actual[mismatch:mismatch + 25]!r}; lengths {len(expected)}/{len(actual)}, phase {state.phase}, message {state.message!r}, foreground {user.GetForegroundWindow()}, target {target}, pause requested {desktop.pause_requested}, cancelled session {desktop.typer.cancelled_session}")
        print(f"Real low-level hook: {len(expected)} characters correct.", flush=True)
    finally:
        desktop.stop()
        user.SetCursorPos = original_center
        root.destroy()
        if previous:
            user.SetForegroundWindow(previous)


if __name__ == "__main__":
    main()
