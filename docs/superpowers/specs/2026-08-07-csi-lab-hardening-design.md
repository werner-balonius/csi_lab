# CSI Lab Hardening Design

## Goal

Strengthen the existing one-transmitter/two-receiver CSI collection workflow without changing its normal command-line entry points or existing aligned NPZ keys.

## Compatibility

- Keep `./csi_lab.sh <trial> <duration>`, `batch`, `check`, and `restore`.
- Keep the current `aligned_csi.npz` keys and add corrected timestamps rather than replacing raw timestamps.
- Replace the missing `csi_plot.py` with a bundled `csi_extract.py` that uses PicoScenes-Python-Toolbox and explicitly selects the main `CSI` segment.
- Treat RGB-D frames as synchronized reference data. Do not describe them as 3D pose labels until a pose estimator creates joint coordinates.

## Components

1. `csi_align.py` estimates a robust linear Node3-to-Node1 clock model, exports corrected timestamps and residual statistics, and fails requested NPZ/camera exports instead of silently warning.
2. `csi_extract.py` preserves physical subcarrier indices, separates packet-format signatures, normalizes Toolbox center-null representations, and rejects `LegacyCSI` as model input.
3. `validate_trial.py` validates required NPZ arrays, expected subcarrier count, timestamps, frame indices, and optional camera metadata.
4. `csi_lab.sh` invokes strict validation, verifies transfer size against the remote file, records failed batch rows, and continues after a failed trial.
5. `csi_agent.sh` reads PHY/channel/preset/destination parameters from environment variables, validates supported settings, creates collision-free trial directories, and reports the selected experiment configuration.
6. Documentation distinguishes packet alignment, clock correction, RGB-D pairing, and future pose-label generation, and provides controlled comparison examples.

## Failure Policy

- A requested NPZ export that cannot be created is a failed alignment.
- Camera-enabled collection fails when metadata is missing, depth sequence gaps are nonzero, or the H265 stream is not decodable.
- Batch mode records each failure and proceeds to the next planned repeat, then returns nonzero if any repeat failed.
- Unsupported PHY/preset combinations fail before hardware reconfiguration.

## Tests

Python unit tests cover robust clock fitting, strict NPZ validation, camera validation, and pilot-count expectations. Shell regression tests statically and behaviorally check batch error propagation, quoted remote arguments, configuration propagation, and unique directory creation. Existing Node1/Node3 captures provide an integration test for packet matching and clock-model reporting.
