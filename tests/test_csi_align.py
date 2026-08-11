import csv
import tempfile
import unittest
from pathlib import Path

import numpy as np

import csi_align


class ClockModelTests(unittest.TestCase):
    def test_recovers_offset_and_drift_with_outlier(self):
        start = 1_700_000_000_000_000_000
        node1 = start + np.arange(200, dtype=np.int64) * 50_000_000
        elapsed_s = (node1 - node1[0]) / 1e9
        expected_delta = 4_800_000 + 21_500 * elapsed_s
        node3 = node1 + np.rint(expected_delta).astype(np.int64)
        node3[80] += 7_000_000

        model = csi_align.fit_clock_model(node1, node3)

        self.assertAlmostEqual(model["offset_ns_at_reference"], 4_800_000, delta=2)
        self.assertAlmostEqual(model["drift_ppm"], 21.5, delta=0.01)
        self.assertEqual(model["inlier_count"], 199)
        self.assertLess(model["residual_p95_abs_ns"], 2)

    def test_rejects_too_few_valid_timestamps(self):
        with self.assertRaisesRegex(ValueError, "at least three"):
            csi_align.fit_clock_model([100, 200], [110, 210])

    def test_csv_contains_corrected_clock_columns(self):
        packet1 = csi_align.Packet(0, "aa:bb:cc:dd:ee:ff", 1, 0, 7, 1, 10, 1_000_000_000)
        packet3 = csi_align.Packet(1, "aa:bb:cc:dd:ee:ff", 1, 0, 7, 1, 11, 1_005_000_000)
        model = {
            "reference_node1_ns": 1_000_000_000,
            "offset_ns_at_reference": 5_000_000.0,
            "drift_ns_per_second": 0.0,
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "aligned.csv"
            csi_align.write_csv(path, [(packet1, packet3)], model)
            with path.open(newline="", encoding="utf-8") as stream:
                row = next(csv.DictReader(stream))
        self.assertEqual(int(row["node3_system_ns_corrected"]), 1_000_000_000)
        self.assertEqual(int(row["node3_clock_residual_ns"]), 0)


class SubcarrierTests(unittest.TestCase):
    def test_he20_removes_eight_format_specific_pilots(self):
        indices = np.concatenate((np.arange(-122, -1), np.arange(2, 123)))
        cube = np.ones((2, 242, 1, 1), dtype=np.complex64)
        data, kept = csi_align.select_model_subcarriers(cube, indices, "HESU", 20)
        self.assertEqual(data.shape[1], 234)
        for pilot in (-116, -90, -48, -22, 22, 48, 90, 116):
            self.assertNotIn(pilot, kept)

    def test_vht80_uses_vht_pilot_locations(self):
        indices = np.concatenate((np.arange(-122, -1), np.arange(2, 123)))
        cube = np.ones((2, 242, 1, 1), dtype=np.complex64)
        data, kept = csi_align.select_model_subcarriers(cube, indices, "VHT", 80)
        self.assertEqual(data.shape[1], 234)
        for pilot in (-103, -75, -39, -11, 11, 39, 75, 103):
            self.assertNotIn(pilot, kept)

    def test_ht20_keeps_all_56_occupied_tones(self):
        indices = np.concatenate((np.arange(-28, 0), np.arange(1, 29)))
        cube = np.ones((2, 56, 1, 1), dtype=np.complex64)
        data, kept = csi_align.select_model_subcarriers(cube, indices, "HT", 20)
        self.assertEqual(data.shape[1], 56)
        np.testing.assert_array_equal(kept, indices)


if __name__ == "__main__":
    unittest.main()
