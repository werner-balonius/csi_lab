# CSI Lab Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver a backward-compatible, strictly validated CSI collection and synchronization workflow.

**Architecture:** Keep the shell orchestration and Python alignment split. Add one focused validator, expose experiment parameters through the existing environment/config mechanism, and preserve raw timing while adding a robust corrected clock domain.

**Tech Stack:** Bash, Python 3, NumPy, unittest, PicoScenes, DepthAI.

## Global Constraints

- Preserve existing CLI entry points and existing aligned NPZ keys.
- Replace the missing `csi_plot.py` with a tested main-CSI extractor based on PicoScenes-Python-Toolbox.
- Do not claim that RGB-D frames are 3D pose labels.
- Do not require hardware for automated tests.

---

### Task 1: Clock Model

**Files:** Modify `csi_align.py`; create `tests/test_csi_align.py`.

**Interface:** `fit_clock_model(node1_ns, node3_ns)` returns offset, slope, drift in ppm, residual MAD, residual standard deviation, and p95 absolute residual. NPZ output adds `node3_system_ns_corrected` and `node3_clock_residual_ns`.

- [ ] Write tests for a known offset/drift series and an outlier-contaminated series.
- [ ] Run the tests and observe missing-function failures.
- [ ] Implement robust centered linear fitting with iterative MAD rejection.
- [ ] Add corrected columns to CSV, NPZ, JSON, and console output.
- [ ] Run the tests and confirm both synthetic cases pass.

### Task 2: Strict Trial Validation

**Files:** Create `validate_trial.py`; create `tests/test_validate_trial.py`; modify `csi_lab.sh`.

**Interface:** `validate_npz(path, expected_subcarriers, require_camera)` returns a summary dictionary or raises `ValidationError`. CLI exits nonzero on any contract violation.

- [ ] Write failing tests for missing data arrays, wrong subcarrier counts, nonmonotonic indices, missing camera pairing, camera sequence gaps, and undecodable color.
- [ ] Implement the validator and JSON/console summary.
- [ ] Replace the embedded permissive check in `csi_lab.sh` with the validator.
- [ ] Run validator tests and a generated valid fixture.

### Task 3: Reliable Orchestration

**Files:** Modify `csi_lab.sh`; create `tests/test_shell_contracts.py`.

**Interface:** `trial_fail` returns from a trial; top-level `die` remains fatal. `fetch` compares remote and local byte sizes. Batch mode writes `batch_summary.tsv` and exits nonzero after all planned repeats if any failed.

- [ ] Write failing contract tests for batch continuation, argument quoting, metadata transfer, and size comparison.
- [ ] Implement per-trial failure propagation and summary recording.
- [ ] Implement safe remote argument quoting and exact transfer-size verification.
- [ ] Validate batch row values before arithmetic.
- [ ] Run shell contract tests and `bash -n`.

### Task 4: Experiment Configuration

**Files:** Modify `csi_agent.sh`, `csi_lab.sh`, `csi_lab.conf.example`; create comparison batch examples.

**Interface:** `CSI_PHY`, `CSI_CHANNEL`, `CSI_BANDWIDTH`, `CSI_PRESET`, `CSI_TARGET_MAC`, `CSI_TRAFFIC_MODE`, and `CSI_DELAY_US` are propagated to every node and recorded in metadata.

- [ ] Write failing contract tests for configurable 20/80 MHz, HT/HE preset labels, and broadcast/multicast destination selection.
- [ ] Add parameter validation and remote propagation.
- [ ] Add controlled comparison configuration examples without asserting hardware support that has not been measured.
- [ ] Run shell tests and syntax checks.

### Task 5: Camera Quality and Documentation

**Files:** Modify `csi_cam.py`, `csi_agent.sh`, `README.md`, `HANDOFF.md`; create `CHANGELOG_IMPROVED.md`.

**Interface:** Camera capture exits nonzero for empty depth, depth sequence gaps, or undecodable H265 unless `CSI_ALLOW_CAMERA_WARNINGS=1` is explicitly set.

- [ ] Write tests/contract checks for camera quality keys and enforcement.
- [ ] Enforce camera quality in the agent and trial validator.
- [ ] Correct ground-truth terminology and document the future pose-estimation boundary.
- [ ] Document `csi_plot.py` as a required externally supplied, version-pinned dependency.
- [ ] Run all tests and inspect documentation for contradictory claims.

### Task 6: Integration and Packaging

**Files:** Create `TEST_RESULTS.md`; package `csi_lab_improved.zip`.

- [ ] Run Python compilation and all unit tests.
- [ ] Run Bash syntax validation under WSL.
- [ ] Run `csi_align.py` against the existing Node1/Node3 captures and inspect JSON/NPZ/CSV outputs.
- [ ] Record hardware-dependent items that cannot be verified locally.
- [ ] Create a ZIP beside the improved folder and verify its contents.
