import unittest

from skytrace_ml_tracker.eval import eval_capture
from skytrace_ml_tracker.metrics import Band


class TestEval(unittest.TestCase):
    def test_strict_non_merge_ok_when_one_to_one(self):
        gt = [Band(0.0, 10.0), Band(20.0, 30.0)]
        pred = [Band(0.0, 10.0), Band(20.0, 30.0)]
        ev = eval_capture(gt, pred)
        self.assertTrue(ev.strict_non_merge_ok)
        self.assertEqual(ev.matched_n, 2)

    def test_merge_detected_as_recall_drop(self):
        gt = [Band(0.0, 10.0), Band(20.0, 30.0)]
        pred = [Band(0.0, 30.0)]  # one wide band
        ev = eval_capture(gt, pred)
        self.assertFalse(ev.strict_non_merge_ok)
        self.assertEqual(ev.matched_n, 1)

    def test_edge_ok_gate(self):
        gt = [Band(100.0, 200.0)]
        # 0.4% of bw=100 => 0.4Hz tolerance. Use a slightly too-big error.
        pred = [Band(100.5, 199.5)]
        ev = eval_capture(gt, pred)
        self.assertEqual(ev.edge_ok_n, 0)


if __name__ == "__main__":
    unittest.main()
