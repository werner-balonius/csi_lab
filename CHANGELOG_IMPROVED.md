# Improved Version Changes

## Implemented

- Added `csi_extract.py` to replace the missing `csi_plot.py` dependency. It uses PicoScenes-Python-Toolbox, selects the main `CSI` segment, records PHY/bandwidth/subcarrier metadata, separates mixed packet-format signatures, and preserves `(tone, Rx, Tx)` ordering.
- Normalized PicoScenes center-null representations through physical `SubcarrierIndex`: HT20 becomes 56 occupied tones; HE20 and VHT80 become 242 occupied tones before format-specific model preparation.
- Added format-specific model-input selection: HT20 keeps 56 occupied tones, HE20 removes HE pilots to 234 data tones, and VHT80 removes VHT pilots to 234 data tones.
- Added robust Node3-to-Node1 offset/drift fitting. CSV and NPZ outputs now include corrected Node3 timestamps and residuals; JSON includes drift in ppm plus residual MAD/std/p95/max.
- Added `validate_trial.py`. Missing model arrays, `LegacyCSI`, captured format/bandwidth mismatches, non-finite CSI, wrong tone counts, row mismatches, nonmonotonic frame/timestamp arrays, missing camera pairs, depth gaps, and undecodable H265 now fail the trial.
- Made PHY index, channel, bandwidth, preset, traffic mode, destination MAC, packet interval, and expected model-input tones configurable and reproducibly recorded.
- Added HT20 broadcast/multicast and VHT20/VHT80 bandwidth-comparison profiles.
- Limited node-side preflight to the PHY/bandwidth combinations implemented by the strict extraction and validation path, so invalid HT80-style configurations fail before collection.
- Fixed batch failure handling. Failed repeats are recorded, later repeats continue, and the final batch exit status remains nonzero when any repeat failed.
- Added collision-resistant local result directories and refusal to reuse receiver/camera trial directories.
- Added shell-safe quoting for remote agent arguments.
- Added exact remote/local byte-size verification for CSI, NPZ, receiver metadata, depth, color and camera metadata files.
- Enforced camera quality both at capture and final validation.
- Corrected documentation: RGB-D frames are reference measurements, not 3D pose labels.

## Compatibility Notes

- Existing commands remain: `check`, one named trial, `batch`, and `restore`.
- Existing aligned NPZ keys remain; new metadata and corrected-time keys are additive.
- The default profile is now `TX_CBW_20_HT` with 56 tones to match the current sensing requirement. Use an explicit profile when processing the older HE-SU workflow.
- Existing old `target_csi.npz` files without `csi_segment`, `packet_format`, `bandwidth_mhz`, and `subcarrier_index` are intentionally rejected. Re-extract them with `csi_extract.py`.

## Still Requires Real Hardware

- Deploy and build/install PicoScenes-Python-Toolbox on Node1 and Node3.
- Confirm that all three radios and the local regulatory domain support the requested 80 MHz center frequency.
- Run short HT20, VHT20, VHT80, broadcast, and multicast pilot captures before a large batch.
- Confirm with the supervisor whether OAK-D is an approved substitute for the originally assigned Azure Kinect.
- Choose and validate a pose-estimation model before generating joint-coordinate labels.
