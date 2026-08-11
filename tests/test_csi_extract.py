import unittest

import numpy as np

import csi_extract


class CsiExtractionTests(unittest.TestCase):
    def test_reshapes_main_csi_in_picoscenes_fortran_order(self):
        frame = {
            "CSI": {
                "numTones": 2,
                "numTx": 1,
                "numRx": 2,
                "numCSI": 1,
                "CSI": [1 + 1j, 2 + 2j, 3 + 3j, 4 + 4j],
            },
            "LegacyCSI": {"numTones": 2, "CSI": [99, 99]},
        }
        matrix = csi_extract.reshape_main_csi(frame)
        self.assertEqual(matrix.shape, (2, 2, 1))
        np.testing.assert_array_equal(matrix[:, 0, 0], [1 + 1j, 2 + 2j])
        np.testing.assert_array_equal(matrix[:, 1, 0], [3 + 3j, 4 + 4j])

    def test_rejects_frame_without_main_csi(self):
        with self.assertRaisesRegex(ValueError, "main CSI"):
            csi_extract.reshape_main_csi({"LegacyCSI": {"CSI": [1]}})

    def test_normalizes_ht20_center_null_to_56_occupied_tones(self):
        indices = np.arange(-28, 29)
        matrix = np.arange(57).reshape(57, 1, 1)
        normalized, normalized_indices = csi_extract.normalize_occupied_csi(
            matrix, indices, "HT", 20
        )
        self.assertEqual(normalized.shape[0], 56)
        self.assertNotIn(0, normalized_indices)

    def test_normalizes_he20_three_center_nulls_to_242_tones(self):
        indices = np.arange(-122, 123)
        matrix = np.arange(245).reshape(245, 1, 1)
        normalized, normalized_indices = csi_extract.normalize_occupied_csi(
            matrix, indices, "HESU", 20
        )
        self.assertEqual(normalized.shape[0], 242)
        self.assertTrue(all(value not in normalized_indices for value in (-1, 0, 1)))

    def test_dominant_signature_does_not_mix_packet_formats(self):
        ht = ("aa:bb:cc:dd:ee:ff", (56, 2, 1), "HT", 20)
        vht = ("aa:bb:cc:dd:ee:ff", (56, 2, 1), "VHT", 20)
        self.assertEqual(csi_extract.dominant_signature([ht, vht, vht]), vht)


if __name__ == "__main__":
    unittest.main()
