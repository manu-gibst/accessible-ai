import unittest

from accessible_ai.state import CornerDwell, ResponseState


class StateTests(unittest.TestCase):
    def test_response_advances_only_when_armed_and_moves_to_done(self):
        state = ResponseState()
        emitted = []
        self.assertFalse(state.advance(10, emitted.append))
        self.assertTrue(state.begin())
        self.assertFalse(state.begin())
        state.ready("A\r\n😀")
        self.assertFalse(state.arm(0))
        self.assertTrue(state.arm(10))
        self.assertFalse(state.begin())
        self.assertFalse(state.advance(10, emitted.append))
        self.assertFalse(state.advance(10, emitted.append))
        self.assertTrue(state.advance(10, emitted.append))
        self.assertEqual(emitted, ["A", "\n", "😀"])
        self.assertEqual(state.snapshot()["phase"], "done")
        self.assertFalse(state.advance(10, emitted.append))

    def test_focus_change_pauses_and_resume_preserves_position(self):
        state = ResponseState()
        emitted = []
        state.ready("abc")
        state.arm(10)
        state.advance(10, emitted.append)
        state.advance(20, emitted.append)
        self.assertEqual(emitted, ["a"])
        self.assertEqual(state.snapshot()["phase"], "ready")
        state.arm(20)
        state.advance(20, emitted.append)
        self.assertEqual(emitted, ["a", "b"])

    def test_input_failure_preserves_character(self):
        state = ResponseState()
        state.ready("abc")
        state.arm(10)
        def fail(character):
            raise OSError("blocked")
        state.advance(10, fail)
        self.assertEqual(state.snapshot()["position"], 0)
        self.assertEqual(state.snapshot()["phase"], "ready")

    def test_corner_requires_dwell_and_leaving_before_retrigger(self):
        corner = CornerDwell(dwell=0.4)
        self.assertIsNone(corner.update(999, 0, 1000, 800, 0))
        self.assertIsNone(corner.update(999, 0, 1000, 800, 0.3))
        self.assertEqual(corner.update(999, 0, 1000, 800, 0.5), "capture")
        self.assertIsNone(corner.update(999, 0, 1000, 800, 2))
        corner.update(500, 400, 1000, 800, 3)
        corner.update(0, 799, 1000, 800, 4)
        self.assertEqual(corner.update(0, 799, 1000, 800, 4.5), "type")
        self.assertIsNone(corner.update(-1, 799, 1000, 800, 5))


if __name__ == "__main__":
    unittest.main()
