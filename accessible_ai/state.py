from dataclasses import dataclass
from threading import RLock


@dataclass
class ResponseState:
    text: str = ""
    position: int = 0
    phase: str = "idle"
    message: str = "Upload a photo or dwell in the top-right corner."
    target: int = 0
    request_id: int = 0
    typing_session: int = 0

    def __post_init__(self):
        self.lock = RLock()
        self._refresh_keyboard_context()

    def _refresh_keyboard_context(self):
        self.keyboard_context = (self.phase, self.target, self.typing_session, len(self.text) - self.position)

    def snapshot(self):
        with self.lock:
            return {
                "type": "status", "phase": self.phase, "message": self.message,
                "response": self.text, "position": self.position, "total": len(self.text),
                "request_id": self.request_id,
            }

    def begin(self):
        with self.lock:
            if self.phase in {"generating", "typing"}:
                return False
            self.request_id += 1
            self.text, self.position, self.target = "", 0, 0
            self.phase, self.message = "generating", "Claude is reading your image…"
            self._refresh_keyboard_context()
            return True

    def ready(self, text):
        with self.lock:
            self.text = text.replace("\r\n", "\n").replace("\r", "\n")
            self.position = 0
            self.phase = "ready" if self.text else "error"
            self.message = "Select a textbox, then dwell in the bottom-left corner." if self.text else "Claude returned an empty response."
            self._refresh_keyboard_context()

    def fail(self, message):
        with self.lock:
            self.phase, self.message, self.target = "error", message, 0
            self._refresh_keyboard_context()

    def arm(self, target):
        with self.lock:
            if self.phase != "ready" or not target:
                return False
            self.target = target
            self.typing_session += 1
            self.phase, self.message = "typing", "Press keys to type. Escape pauses."
            self._refresh_keyboard_context()
            return True

    def pause(self, session=None):
        with self.lock:
            if session is not None and session != self.typing_session:
                return
            if self.phase == "typing":
                self.typing_session += 1
                self.phase, self.message, self.target = "ready", "Paused. Select a textbox and use the bottom-left corner to resume.", 0
                self._refresh_keyboard_context()

    def advance(self, target, emit, session=None):
        with self.lock:
            if session is not None and session != self.typing_session:
                return False
            if self.phase != "typing":
                return False
            if target != self.target:
                self.pause()
                return False
            try:
                emit(self.text[self.position])
            except OSError as error:
                self.pause()
                self.message = f"Typing paused: {error}"
                return False
            self.position += 1
            if self.position == len(self.text):
                self.phase, self.message, self.target = "done", "Response finished.", 0
                self._refresh_keyboard_context()
                return True
            self._refresh_keyboard_context()
            return False

    def paste_remaining(self, target, paste, session=None):
        with self.lock:
            if session is not None and session != self.typing_session:
                return False
            if self.phase != "typing":
                return False
            if target != self.target:
                self.pause()
                return False
            try:
                paste(self.text[self.position:])
            except OSError as error:
                self.pause()
                self.message = f"Typing paused: {error}"
                return False
            self.position = len(self.text)
            self.phase, self.message, self.target = "done", "Response finished.", 0
            self._refresh_keyboard_context()
            return True


class CornerDwell:
    def __init__(self, dwell=0.45, margin=6):
        self.dwell, self.margin = dwell, margin
        self.corner, self.entered, self.fired = None, 0, False

    def update(self, x, y, width, height, now):
        corner = None
        if width - self.margin <= x < width and 0 <= y < self.margin:
            corner = "capture"
        elif 0 <= x < self.margin and height - self.margin <= y < height:
            corner = "type"
        if corner != self.corner:
            self.corner, self.entered, self.fired = corner, now, False
        if corner and not self.fired and now - self.entered >= self.dwell:
            self.fired = True
            return corner
        return None
