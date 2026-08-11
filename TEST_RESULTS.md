# Verification Record

Date: 2026-08-07

## Automated Checks

- Python unit/contract tests: 30 of 30 tests passed.
- Python compilation: `csi_align.py`, `csi_extract.py`, `validate_trial.py`, `csi_cam.py`, and `depth_view.py` compiled successfully.
- Bash syntax: `csi_lab.sh`, `csi_agent.sh`, and `field.sh` passed `bash -n` under WSL.
- `shellcheck` and `ruff` were not installed in the local environment, so those optional linters were not run.

## Existing-Data Integration Check

Inputs:

- `node1_walk_link1(1).csi`
- `node3_walk_link1.csi`

Observed and validated:

- Main CSI segment: `CSI`
- Packet format: `HESU`
- Bandwidth: `20 MHz`
- Extracted occupied tones: `242`
- Node1 target packets: `5556`
- Node3 target packets: `5658`
- Exact packet matches: `5386`
- Match ratios: `0.969402` and `0.951926`
- Nonmonotonic matched steps: `0`
- Raw Node3-Node1 median clock offset: approximately `-4.860 ms`
- Estimated relative clock drift: approximately `-21.491 ppm`
- Corrected inlier residual p95: approximately `0.084 ms`
- Model-input shape: `(5386, 234, 2, 1)` for both receivers
- Strict validator result: `VALIDATION_OK=1`
- Captured format/bandwidth contract: `HESU / 20 MHz`
- CSI finite-value contract: passed

This integration check proves the offline extraction, exact matching, drift correction, model-input generation, and validation path for the supplied HE-SU captures. It does not prove the new HT20/VHT80/multicast hardware configurations or camera capture path; those require new pilot experiments.
