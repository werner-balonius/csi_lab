# CSI Lab Improved

This package controls a one-transmitter/two-receiver PicoScenes experiment, extracts the main CSI segment, aligns identical packets, estimates receiver clock drift, and optionally pairs CSI packets with OAK-D RGB-D frames.

## Scientific Boundary

The camera output is synchronized RGB-D reference data. It is not a 3D pose label: no skeleton, joint coordinate, or pose-confidence array is generated. A separate pose estimator and camera/Wi-Fi coordinate calibration are required before calling the output 3D pose ground truth.

Packet format and CSI segment are different concepts. Formal sensing input must use the main `CSI` segment, not `LegacyCSI`. The default experiment profile is HT 20 MHz with 56 tones. An 80 MHz HT profile does not exist in IEEE 802.11n; use a controlled VHT20/VHT80 pair for a bandwidth comparison, while recording the PHY change explicitly.

## Files

| File | Purpose |
|---|---|
| `csi_lab.sh` | Controller-side preflight, collection, transfer, alignment and validation |
| `csi_agent.sh` | Receiver/transmitter/camera node operations |
| `csi_extract.py` | Extracts only the main `CSI` segment through PicoScenes-Python-Toolbox |
| `csi_align.py` | Exact packet matching, robust offset/drift estimation and RGB-D pairing |
| `validate_trial.py` | Strict aligned-NPZ and camera-quality contract |
| `csi_cam.py` | OAK-D depth/color capture and timing metadata |
| `profiles/` | Explicit HT20, VHT20/VHT80 and broadcast/multicast examples |
| `comparison_trials.txt` | Small repeated empty/walk comparison plan |
| `AUDIT_2026-08-08.md` | Claim-by-claim verification record and remaining boundaries |

## Dependencies

Controller: Bash, Python 3 and NumPy.

Receiver nodes: PicoScenes, NumPy, and the built `picoscenes` Python module from PicoScenes-Python-Toolbox. Camera host additionally needs `depthai`.

Deploy the node files explicitly:

```bash
scp csi_agent.sh csi_extract.py csi_cam.py node1:~/
scp csi_agent.sh csi_extract.py node3:~/
scp csi_agent.sh node2:~/
ssh node1 'chmod +x ~/csi_agent.sh ~/csi_extract.py ~/csi_cam.py'
ssh node3 'chmod +x ~/csi_agent.sh ~/csi_extract.py'
ssh node2 'chmod +x ~/csi_agent.sh'
```

`csi_extract.py` replaces the missing external `csi_plot.py` from the original archive. Preflight fails with deployment guidance if the extractor or `picoscenes` module is missing.

## Configuration

```bash
cp csi_lab.conf.example csi_lab.conf
```

Important fields are `CSI_CHANNEL`, `CSI_BANDWIDTH`, `CSI_PRESET`, `CSI_EXPECTED_PACKET_FORMAT`, `CSI_EXPECTED_SUBCARRIERS`, `CSI_TRAFFIC_MODE`, and `CSI_TARGET_MAC`. The expected packet format is derived from the preset unless explicitly set. Every node receives the radio values and the trial records the configured and captured metadata.

```bash
CSI_LAB_CONFIG=profiles/ht20_broadcast.conf ./csi_lab.sh batch comparison_trials.txt
CSI_LAB_CONFIG=profiles/ht20_multicast.conf ./csi_lab.sh batch comparison_trials.txt
CSI_LAB_CONFIG=profiles/vht20_broadcast.conf ./csi_lab.sh batch comparison_trials.txt
CSI_LAB_CONFIG=profiles/vht80_broadcast.conf ./csi_lab.sh batch comparison_trials.txt
```

Confirm that `5210 MHz / 80 MHz` is legal and supported by the radios and local regulatory configuration before the VHT80 run.

## Acceptance Contract

A trial succeeds only when:

- both receiver files and metadata are transferred with exact remote/local byte-size agreement;
- prepared receiver NPZ files explicitly report `csi_segment=CSI`;
- captured `packet_format` and `bandwidth_mhz` match the selected experiment profile;
- identical packet IDs are matched monotonically;
- raw and model-input CSI arrays have equal, nonzero row counts;
- raw and model-input CSI arrays contain only finite values;
- model-input subcarrier count equals the selected profile;
- precise `system_ns` timestamps exist and corrected Node3 timestamps are produced;
- camera-enabled trials contain CSI-to-depth pairing arrays;
- camera metadata reports zero depth sequence gaps and a decodable H265 stream.

`alignment_report.json` reports the raw median offset plus a robust linear clock model. `node3_system_ns_corrected` maps Node3 timestamps into the Node1 time base. `node3_clock_residual_ns` measures remaining packet-pair timing error after correction.

Batch mode writes `batch_*_summary.tsv`, continues after a failed group, and returns a nonzero final status when any group failed.

## Local Tests

```bash
python3 -m unittest discover -s tests -v
bash -n csi_lab.sh csi_agent.sh field.sh
```

Hardware-free tests cover clock drift, CSI-segment enforcement, captured format/bandwidth checks, finite CSI values, subcarrier validation, camera quality, configuration propagation and orchestration contracts. A successful local test does not prove that a radio supports the requested profile; the real-node preflight and a short pilot capture remain mandatory.
