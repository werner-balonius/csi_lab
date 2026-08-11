import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ShellContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lab = (ROOT / "csi_lab.sh").read_text(encoding="utf-8")
        cls.agent = (ROOT / "csi_agent.sh").read_text(encoding="utf-8")

    def test_trial_failures_return_to_batch(self):
        self.assertIn("trial_fail()", self.lab)
        self.assertIn("_summary.tsv", self.lab)
        self.assertIn("return 1", self.lab)
        self.assertIn("TRIAL_OK=0  # batch remains active", self.lab)

    def test_remote_arguments_are_shell_quoted(self):
        self.assertIn("printf -v quoted ' %q'", self.lab)
        self.assertNotIn('"bash \\"\\$HOME/$CSI_AGENT\\" $*"', self.lab)

    def test_fetch_verifies_remote_size(self):
        self.assertIn("remote_size", self.lab)
        self.assertIn("本地大小", self.lab)

    def test_camera_files_use_the_same_verified_fetch_path(self):
        for key in ("CAM_DEPTH_FILE", "CAM_COLOR_FILE", "CAM_META_FILE"):
            self.assertIn(key, self.agent)
            self.assertIn(key, self.lab)
        self.assertNotIn('$CAM_HOST:$camdir/*', self.lab)

    def test_experiment_parameters_are_configurable(self):
        for name in (
            "CSI_PHY", "CSI_CHANNEL", "CSI_BANDWIDTH", "CSI_PRESET",
            "CSI_TARGET_MAC", "CSI_TRAFFIC_MODE", "CSI_DELAY_US",
            "CSI_EXPECTED_SUBCARRIERS",
        ):
            self.assertIn(name, self.agent + self.lab)

    def test_validator_checks_captured_format_and_bandwidth(self):
        self.assertIn("CSI_EXPECTED_PACKET_FORMAT", self.lab)
        self.assertIn('--expected-packet-format "$CSI_EXPECTED_PACKET_FORMAT"', self.lab)
        self.assertIn('--expected-bandwidth-mhz "$CSI_BANDWIDTH"', self.lab)

    def test_agent_rejects_unimplemented_phy_bandwidth_combinations(self):
        for preset in (
            "TX_CBW_20_HT", "TX_CBW_20_VHT",
            "TX_CBW_20_HESU", "TX_CBW_80_VHT",
        ):
            self.assertIn(preset, self.agent)
        self.assertIn("当前严格流程尚未实现", self.agent)

    def test_trial_directories_must_be_new(self):
        self.assertIn('mkdir "$rundir"', self.agent)
        self.assertNotIn('mkdir -p "$rundir"', self.agent)

    def test_missing_plot_dependency_has_deployment_guidance(self):
        self.assertIn("CSI_PLOT", self.agent)
        self.assertIn("deploy", self.agent)


if __name__ == "__main__":
    unittest.main()
