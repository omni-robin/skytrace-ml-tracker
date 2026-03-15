import unittest

import numpy as np

from skytrace_ml_tracker.features import normalize_feature


class TestFeatures(unittest.TestCase):
    def test_normalize_feature_finite(self):
        x = np.array([0.0, 1.0, 2.0, 3.0], dtype=np.float32)
        y = normalize_feature(x)
        self.assertTrue(np.isfinite(y).all())


if __name__ == "__main__":
    unittest.main()
