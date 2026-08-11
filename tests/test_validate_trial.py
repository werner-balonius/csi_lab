import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

from validate_trial import ValidationError, validate_npz


def valid_payload(subcarriers=56):
    rows = 4
    shape = (rows, subcarriers, 1, 1)
    return {
        "node1_csi": np.ones(shape, dtype=np.complex64),
        "node3_csi": np.ones(shape, dtype=np.complex64),
        "node1_csi_data": np.ones(shape, dtype=np.complex64),
        "node3_csi_data": np.ones(shape, dtype=np.complex64),
        "node1_frame_index": np.arange(rows, dtype=np.int64),
        "node3_frame_index": np.arange(rows, dtype=np.int64),
        "node1_system_ns": np.arange(rows, dtype=np.int64) + 100,
        "node3_system_ns": np.arange(rows, dtype=np.int64) + 110,
        "node3_system_ns_corrected": np.arange(rows, dtype=np.int64) + 100,
        "node3_clock_residual_ns": np.zeros(rows, dtype=np.int64),
        "sequence": np.arange(rows, dtype=np.uint16),
        "task_id": np.ones(rows, dtype=np.uint16),
        "csi_segment": np.asarray("CSI"),
        "packet_format": np.asarray("HT"),
        "bandwidth_mhz": np.asarray(20, dtype=np.int16),
        "data_subcarrier_index": np.arange(subcarriers, dtype=np.int16),
        "pilot_subcarrier_index": np.asarray([], dtype=np.int16),
    }


class TrialValidationTests(unittest.TestCase):
    def save(self, directory, payload):
        path = Path(directory) / "aligned.npz"
        np.savez_compressed(path, **payload)
        return path

    def test_accepts_complete_non_camera_trial(self):
        with tempfile.TemporaryDirectory() as tmp:
            summary = validate_npz(self.save(tmp, valid_payload()), 56, False)
        self.assertEqual(summary["rows"], 4)
        self.assertEqual(summary["data_subcarriers"], 56)

    def test_rejects_missing_model_input_arrays(self):
        payload = valid_payload()
        del payload["node1_csi_data"]
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValidationError, "node1_csi_data"):
                validate_npz(self.save(tmp, payload), 56, False)

    def test_rejects_wrong_subcarrier_count(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValidationError, "expected 234"):
                validate_npz(self.save(tmp, valid_payload(56)), 234, False)

    def test_rejects_wrong_packet_format_for_experiment(self):
        payload = valid_payload()
        payload["packet_format"] = np.asarray("HESU")
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValidationError, "expected HT"):
                validate_npz(
                    self.save(tmp, payload), 56, False,
                    expected_packet_format="HT",
                )

    def test_rejects_wrong_bandwidth_for_experiment(self):
        payload = valid_payload()
        payload["bandwidth_mhz"] = np.asarray(80, dtype=np.int16)
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValidationError, "expected 20"):
                validate_npz(
                    self.save(tmp, payload), 56, False,
                    expected_bandwidth_mhz=20,
                )

    def test_rejects_nonfinite_model_input(self):
        payload = valid_payload()
        payload["node3_csi_data"][1, 2, 0, 0] = np.nan + 0j
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValidationError, "non-finite"):
                validate_npz(self.save(tmp, payload), 56, False)

    def test_rejects_legacy_csi_as_model_input(self):
        payload = valid_payload()
        payload["csi_segment"] = np.asarray("LegacyCSI")
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValidationError, "main CSI segment"):
                validate_npz(self.save(tmp, payload), 56, False)

    def test_rejects_nonmonotonic_frame_indices(self):
        payload = valid_payload()
        payload["node3_frame_index"] = np.array([0, 2, 1, 3])
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValidationError, "strictly increasing"):
                validate_npz(self.save(tmp, payload), 56, False)

    def test_camera_trial_requires_pairing_and_good_metadata(self):
        payload = valid_payload()
        payload["depth_frame_index"] = np.arange(4, dtype=np.int64)
        payload["depth_dt_ns"] = np.zeros(4, dtype=np.int64)
        payload["depth_timestamp_epoch_ns"] = np.arange(4, dtype=np.int64) + 90
        with tempfile.TemporaryDirectory() as tmp:
            path = self.save(tmp, payload)
            meta = Path(tmp) / "cam.json"
            meta.write_text(json.dumps({
                "depth": {"frames": 4, "sequence_gaps": 1},
                "color": {"decodable": True},
            }), encoding="utf-8")
            with self.assertRaisesRegex(ValidationError, "sequence gaps"):
                validate_npz(path, 56, True, meta)

    def test_camera_trial_accepts_zero_gaps_and_decodable_video(self):
        payload = valid_payload()
        payload["depth_frame_index"] = np.arange(4, dtype=np.int64)
        payload["depth_dt_ns"] = np.zeros(4, dtype=np.int64)
        payload["depth_timestamp_epoch_ns"] = np.arange(4, dtype=np.int64) + 90
        with tempfile.TemporaryDirectory() as tmp:
            path = self.save(tmp, payload)
            meta = Path(tmp) / "cam.json"
            meta.write_text(json.dumps({
                "depth": {"frames": 4, "sequence_gaps": 0},
                "color": {"decodable": True},
            }), encoding="utf-8")
            summary = validate_npz(path, 56, True, meta)
        self.assertEqual(summary["camera_depth_frames"], 4)


if __name__ == "__main__":
    unittest.main()
