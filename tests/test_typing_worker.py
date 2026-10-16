import threading
import time
import unittest

from accessible_ai.state import ResponseState
from accessible_ai.typing_worker import PacedTyper


class PacedTypingTests(unittest.TestCase):
    def worker(self, text, emit, interval=0, paste=None):
        state = ResponseState()
        state.ready(text)
        state.arm(42)
        completed = threading.Event()
        worker = PacedTyper(state, lambda: 42, emit, completed.set,
                            interval=interval, paste=paste)
        self.addCleanup(worker.stop)
        return state, worker, completed

    def test_fast_burst_preserves_order_and_runs_off_the_hook_thread(self):
        expected = "Fast typing: abcABC0123 日本語 😀\n" * 10
        typed, threads = [], []
        def emit(character):
            typed.append(character)
            threads.append(threading.get_ident())
        state, worker, completed = self.worker(expected, emit)
        for character in expected:
            worker.request()
        self.assertEqual(typed, [])
        worker.start()
        self.assertTrue(completed.wait(5), f"phase={state.phase}, position={state.position}, queued={worker.queue.qsize()}, accepted={worker.accepted}, worker_alive={worker.thread.is_alive()}")
        self.assertEqual("".join(typed), expected)
        self.assertEqual(state.snapshot()["phase"], "done")
        self.assertNotIn(threading.get_ident(), threads)

    def test_output_is_paced_even_when_all_keypresses_arrive_at_once(self):
        delivered = []
        state, worker, completed = self.worker("abcdef", lambda character: delivered.append(time.monotonic()), interval=0.02)
        for character in state.text:
            worker.request()
        worker.start()
        self.assertTrue(completed.wait(2))
        self.assertTrue(all(later - earlier >= 0.019 for earlier, later in zip(delivered, delivered[1:])))

    def test_pause_discards_old_credits_and_resume_requires_new_keypresses(self):
        typed = []
        state, worker, completed = self.worker("abcd", typed.append)
        for character in state.text:
            worker.request()
        worker.pause()
        worker.start()
        for attempt in range(100):
            if state.keyboard_context[0] == "ready":
                break
            time.sleep(0.005)
        self.assertEqual(typed, [])
        self.assertTrue(state.arm(42))
        time.sleep(0.03)
        self.assertEqual(typed, [])
        for character in state.text:
            worker.request()
        self.assertTrue(completed.wait(2))
        self.assertEqual("".join(typed), "abcd")

    def test_new_keypress_is_not_lost_while_previous_character_is_in_flight(self):
        entered, release = threading.Event(), threading.Event()
        typed = []
        def emit(character):
            typed.append(character)
            if character == "a":
                entered.set()
                release.wait(1)
        state, worker, completed = self.worker("ab", emit)
        worker.start()
        worker.request()
        self.assertTrue(entered.wait(1))
        worker.request()
        release.set()
        self.assertTrue(completed.wait(2))
        self.assertEqual(typed, ["a", "b"])

    def test_focus_change_does_not_consume_queued_response(self):
        typed = []
        state, worker, completed = self.worker("ab", typed.append)
        worker.foreground = lambda: 99
        worker.request()
        worker.start()
        for attempt in range(100):
            if state.keyboard_context[0] == "ready":
                break
            time.sleep(0.005)
        self.assertEqual(typed, [])
        self.assertEqual(state.snapshot()["position"], 0)

    def test_paste_replaces_queued_credits_with_remaining_response(self):
        entered, release = threading.Event(), threading.Event()
        typed, pasted = [], []

        def emit(character):
            typed.append(character)
            if character == "a":
                entered.set()
                release.wait(1)

        def paste(remaining):
            pasted.append(remaining)
            typed.append(remaining)

        state, worker, completed = self.worker("abcd", emit, paste=paste)
        worker.start()
        for character in state.text:
            worker.request()
        self.assertTrue(entered.wait(1))
        worker.paste()
        worker.request()
        release.set()
        self.assertTrue(completed.wait(2))
        self.assertEqual(pasted, ["bcd"])
        self.assertEqual("".join(typed), "abcd")
        self.assertEqual(state.snapshot()["phase"], "done")

    def test_failed_paste_preserves_remaining_response(self):
        def paste(remaining):
            raise OSError("clipboard busy")

        state, worker, completed = self.worker("abcd", lambda character: None, paste=paste)
        worker.paste()
        worker.start()
        for attempt in range(100):
            if state.keyboard_context[0] == "ready":
                break
            time.sleep(0.005)
        self.assertEqual(state.snapshot()["position"], 0)
        self.assertEqual(state.snapshot()["phase"], "ready")
        self.assertFalse(completed.is_set())
