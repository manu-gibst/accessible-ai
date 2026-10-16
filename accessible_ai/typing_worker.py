import queue
import threading
import time


class PacedTyper:
    def __init__(self, state, foreground, emit, completed, interval=0.04, paste=None):
        self.state, self.foreground = state, foreground
        self.emit, self.completed, self.interval = emit, completed, interval
        self.paste_emit = paste
        self.queue = queue.Queue()
        self.stopping = threading.Event()
        self.pending_lock = threading.Lock()
        self.requested_session = None
        self.quota, self.accepted = 0, 0
        self.cancelled_session = None
        self.paste_session = None
        self.thread = threading.Thread(target=self.run, name="response-typing", daemon=True)

    def start(self):
        self.thread.start()

    def request(self):
        phase, target, session, remaining = self.state.keyboard_context
        if phase != "typing" or session in {self.cancelled_session, self.paste_session} or self.stopping.is_set():
            return
        with self.pending_lock:
            if self.requested_session != session:
                self.requested_session, self.quota, self.accepted = session, remaining, 0
            if self.accepted >= self.quota:
                return
            self.accepted += 1
        self.queue.put((session, target))

    def pause(self):
        self.cancelled_session = self.state.keyboard_context[2]

    def paste(self):
        phase, target, session, remaining = self.state.keyboard_context
        if phase != "typing" or not remaining or self.paste_emit is None or session == self.cancelled_session or self.stopping.is_set():
            return
        self.paste_session = session
        self.queue.put((session, target))

    def run(self):
        next_delivery = 0
        while not self.stopping.is_set():
            if self.cancelled_session is not None:
                self.state.pause(session=self.cancelled_session)
            try:
                session, target = self.queue.get(timeout=0.05)
            except queue.Empty:
                continue
            try:
                if session == self.cancelled_session or self.state.keyboard_context[:3] != ("typing", target, session):
                    continue
                if session == self.paste_session:
                    finished = self.state.paste_remaining(self.foreground(), self.paste_emit, session=session)
                else:
                    if self.stopping.wait(max(0, next_delivery - time.monotonic())):
                        break
                    if session == self.cancelled_session:
                        self.state.pause(session=session)
                        continue
                    if session == self.paste_session:
                        finished = self.state.paste_remaining(self.foreground(), self.paste_emit, session=session)
                    else:
                        finished = self.state.advance(self.foreground(), self.emit, session=session)
                        next_delivery = time.monotonic() + self.interval
                if finished:
                    self.completed()
            finally:
                self.queue.task_done()

    def stop(self):
        self.stopping.set()
        if self.thread.is_alive():
            self.thread.join(timeout=2)
