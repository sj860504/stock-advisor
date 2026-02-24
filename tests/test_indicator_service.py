import unittest
import pandas as pd

from services.analysis.indicator_service import IndicatorService


class TestIndicatorService(unittest.TestCase):
    def test_compute_latest_indicators_snapshot_returns_model(self):
        series = pd.Series(range(1, 221))
        snapshot = IndicatorService.compute_latest_indicators_snapshot(series)
        self.assertIsNotNone(snapshot)
        self.assertIn(200, snapshot.ema)
        self.assertIsNotNone(snapshot.ema[200])
        self.assertGreaterEqual(snapshot.rsi, 0)
        self.assertLessEqual(snapshot.rsi, 100)

    def test_compute_latest_indicators_snapshot_short_series(self):
        series = pd.Series(range(1, 50))
        snapshot = IndicatorService.compute_latest_indicators_snapshot(series)
        self.assertIsNotNone(snapshot)
        self.assertIsNone(snapshot.ema.get(200))
        self.assertEqual(snapshot.rsi, 50.0)

    def test_get_latest_indicators_compat_returns_ema_dict_and_flat_keys(self):
        series = pd.Series(range(1, 221))
        indicators = IndicatorService.get_latest_indicators(series)
        self.assertIn("ema", indicators)
        self.assertIsInstance(indicators["ema"], dict)
        self.assertIn(200, indicators["ema"])
        self.assertIn("ema200", indicators)
        self.assertEqual(indicators["ema200"], indicators["ema"][200])

    def test_get_latest_indicators_short_series(self):
        series = pd.Series(range(1, 50))
        indicators = IndicatorService.get_latest_indicators(series)
        self.assertIn("ema", indicators)
        self.assertIsNone(indicators["ema"].get(200))
        self.assertIn("ema200", indicators)
        self.assertIsNone(indicators["ema200"])


if __name__ == "__main__":
    unittest.main()
