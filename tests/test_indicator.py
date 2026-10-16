import threading
import unittest
from unittest.mock import Mock, patch

from accessible_ai.desktop import Desktop
from accessible_ai.indicator import StatusIndicator


class IndicatorTests(unittest.TestCase):
    def test_construct_stop_and_suspend_do_not_start_gui(self):
        with patch("accessible_ai.indicator.threading.Thread") as thread:
            indicator = StatusIndicator(object())
            indicator.stop()
            self.assertTrue(indicator.suspend())
            indicator.resume()
            thread.assert_not_called()

    def test_start_is_idempotent_and_stop_is_safe(self):
        entered, release = threading.Event(), threading.Event()

        def run():
            entered.set()
            release.wait(1)

        indicator = StatusIndicator(object())
        with patch.object(indicator, "_run", side_effect=run) as worker:
            indicator.start()
            self.assertTrue(entered.wait(1))
            indicator.start()
            release.set()
            indicator.stop()
            indicator.stop()
            self.assertEqual(worker.call_count, 1)

    def test_suspend_waits_for_ui_acknowledgment(self):
        indicator = StatusIndicator(object())
        with patch.object(indicator, "_thread") as thread:
            thread.is_alive.return_value = True
            with patch.object(indicator._hidden, "wait", return_value=False) as wait:
                self.assertFalse(indicator.suspend())
                wait.assert_called_once()
                self.assertGreaterEqual(wait.call_args.kwargs["timeout"], 0)
                self.assertLessEqual(wait.call_args.kwargs["timeout"], 1)
                self.assertTrue(indicator._suspended)
            indicator.resume()
            self.assertFalse(indicator._suspended)

    def test_suspend_requests_hide_even_if_ui_lock_is_busy(self):
        indicator = StatusIndicator(object())
        with patch.object(indicator, "_lock") as lock:
            lock.acquire.return_value = False
            self.assertFalse(indicator.suspend())
            lock.acquire.assert_called_once_with(timeout=1)
            self.assertTrue(indicator._suspended)

    def test_gui_unavailable_logs_and_releases_suspend(self):
        indicator = StatusIndicator(object())
        with patch.dict("sys.modules", {"tkinter": None}):
            with self.assertLogs("accessible_ai.indicator", level="WARNING"):
                indicator.start()
                indicator.stop()
        self.assertTrue(indicator._hidden.is_set())
        self.assertTrue(indicator.suspend())

    def test_capture_waits_for_indicator_to_hide_and_resumes_it(self):
        desktop = Desktop.__new__(Desktop)
        desktop.indicator = Mock()
        desktop.dimensions = Mock(return_value=(100, 100))
        order = []
        desktop.indicator.suspend.side_effect = lambda: order.append("hide") or True
        desktop.indicator.resume.side_effect = lambda: order.append("resume")
        screenshot = Mock()
        screenshot.save.side_effect = lambda output, format: order.append("capture")
        with patch("PIL.ImageGrab.grab", return_value=screenshot):
            desktop.capture()
        self.assertEqual(order, ["hide", "capture", "resume"])

    def test_capture_is_rejected_if_indicator_does_not_hide(self):
        desktop = Desktop.__new__(Desktop)
        desktop.indicator = Mock()
        desktop.indicator.suspend.return_value = False
        with patch("PIL.ImageGrab.grab") as grab:
            with self.assertRaisesRegex(OSError, "Could not hide"):
                desktop.capture()
        grab.assert_not_called()
        desktop.indicator.resume.assert_called_once()


if __name__ == "__main__":
    unittest.main()
