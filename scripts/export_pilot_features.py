#!/usr/bin/env python3
"""Export compact, privacy-safe 100 ms features from local aligned CSI trials.

The raw RGB-D and full CSI arrays stay outside Git. This exporter produces a
small table suitable for plotting and early baselines, plus sanitized metadata
and SHA-256 hashes that identify the local aligned files.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import io
import json
from pathlib import Path
from typing import Any

import numpy as np


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = REPO_ROOT / "docs/experiments/20260811_pilot/manifest.csv"
DEFAULT_OUTPUT = REPO_ROOT / "data/processed/20260811_pilot/pilot_100ms_features.csv.gz"
DEFAULT_METADATA = REPO_ROOT / "data/processed/20260811_pilot/trial_metadata.json"

SAFE_EXPERIMENT_KEYS = {
    "trial",
    "mode",
    "tx_duration_s",
    "rx_duration_s",
    "tx_expected_packets",
    "tx_launch_system_ns",
    "tx_rf_interface",
    "frequency_mhz",
    "txpower_dbm_reported",
    "txpower_control",
    "rx_gain_mode",
    "local_voice_protocol",
    "action_label",
    "layout_id",
    "node1_xyz_m",
    "node2_xyz_m",
    "node3_xyz_m",
    "camera_xyz_m",
    "camera_orientation",
    "layout_reference",
    "chair_center_xyz_m",
    "chair_orientation",
    "chair_seat_height_m",
    "chair_description",
    "chair_stability",
    "started_at",
}

FEATURE_FIELDS = [
    "trial",
    "class",
    "layout_id",
    "role",
    "time_s",
    "protocol_phase",
    "matched_packets_in_bin",
    "depth_frame_median",
    "node1_rssi_mean_dbm",
    "node1_rssi_std_db",
    "node3_rssi_mean_dbm",
    "node3_rssi_std_db",
    "node1_amp_ant0_mean",
    "node1_amp_ant1_mean",
    "node1_amp_ant0_std",
    "node1_amp_ant1_std",
    "node1_change_ant0",
    "node1_change_ant1",
    "node3_amp_ant0_mean",
    "node3_amp_ant1_mean",
    "node3_amp_ant0_std",
    "node3_amp_ant1_std",
    "node3_change_ant0",
    "node3_change_ant1",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results-root", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--metadata-output", type=Path, default=DEFAULT_METADATA)
    parser.add_argument("--bin-ms", type=float, default=100.0)
    parser.add_argument("--action-start-s", type=float, default=9.0)
    parser.add_argument("--action-end-s", type=float, default=25.0)
    return parser.parse_args()


def read_manifest(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as stream:
        return list(csv.DictReader(stream))


def parse_experiment(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        key, separator, value = line.partition(":")
        if separator and key in SAFE_EXPERIMENT_KEYS:
            values[key] = value.strip()
    return values


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def clean_rssi(values: np.ndarray) -> np.ndarray:
    result = np.asarray(values, dtype=np.float64)
    result[result < -200] = np.nan
    return result


def number(value: float | int) -> str:
    value = float(value)
    return "" if not np.isfinite(value) else f"{value:.9g}"


def protocol_phase(label: str, time_s: float, action_start: float, action_end: float) -> str:
    if label in {"empty", "empty_chair"}:
        return "empty"
    if time_s < action_start:
        return "pre_empty_hint"
    if time_s < action_end:
        return "action_candidate_hint"
    return "post_empty_hint"


def node_bin_features(
    amplitude: np.ndarray,
    node: str,
    mask: np.ndarray,
    previous_spectrum: np.ndarray | None,
) -> tuple[dict[str, str], np.ndarray]:
    window = amplitude[mask]
    spectrum = np.nanmean(window, axis=0)
    antennas = spectrum.shape[1]

    output: dict[str, str] = {}
    for antenna in range(2):
        prefix = f"{node}_amp_ant{antenna}"
        if antenna < antennas:
            values = window[:, :, antenna, ...]
            output[f"{prefix}_mean"] = number(np.nanmean(values))
            output[f"{prefix}_std"] = number(np.nanstd(values))
            if previous_spectrum is None:
                output[f"{node}_change_ant{antenna}"] = ""
            else:
                delta = np.abs(
                    spectrum[:, antenna, ...] - previous_spectrum[:, antenna, ...]
                )
                output[f"{node}_change_ant{antenna}"] = number(np.nanmedian(delta))
        else:
            output[f"{prefix}_mean"] = ""
            output[f"{prefix}_std"] = ""
            output[f"{node}_change_ant{antenna}"] = ""
    return output, spectrum


def export_trial_rows(
    manifest_row: dict[str, str],
    npz_path: Path,
    bin_seconds: float,
    action_start: float,
    action_end: float,
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    with np.load(npz_path, allow_pickle=False) as data:
        time_s = data["node3_system_ns"].astype(np.float64) / 1e9
        time_s -= time_s[0]
        bin_index = np.floor(time_s / bin_seconds).astype(np.int64)
        previous: dict[str, np.ndarray | None] = {"node1": None, "node3": None}
        amplitudes: dict[str, np.ndarray] = {}
        for node in ("node1", "node3"):
            csi_key = (
                f"{node}_csi_data"
                if f"{node}_csi_data" in data.files
                else f"{node}_csi"
            )
            # NPZ members are compressed. Load each full member exactly once;
            # indexing data[csi_key] inside every bin would decompress it hundreds
            # of times and make the exporter appear hung.
            amplitudes[node] = np.abs(data[csi_key])
        rssi = {
            "node1": clean_rssi(data["node1_rssi"]),
            "node3": clean_rssi(data["node3_rssi"]),
        }
        depth_frame_index = (
            np.asarray(data["depth_frame_index"])
            if "depth_frame_index" in data.files
            else None
        )

        for current_bin in np.unique(bin_index):
            mask = bin_index == current_bin
            center_time = float(np.nanmean(time_s[mask]))
            output = {
                "trial": manifest_row["trial"],
                "class": manifest_row["class"],
                "layout_id": manifest_row["layout_id"],
                "role": manifest_row["role"],
                "time_s": number(center_time),
                "protocol_phase": protocol_phase(
                    manifest_row["class"], center_time, action_start, action_end
                ),
                "matched_packets_in_bin": str(int(mask.sum())),
                "depth_frame_median": "",
            }
            if depth_frame_index is not None:
                output["depth_frame_median"] = number(
                    np.nanmedian(depth_frame_index[mask])
                )
            for node in ("node1", "node3"):
                output[f"{node}_rssi_mean_dbm"] = number(np.nanmean(rssi[node][mask]))
                output[f"{node}_rssi_std_db"] = number(np.nanstd(rssi[node][mask]))
                features, spectrum = node_bin_features(
                    amplitudes[node], node, mask, previous[node]
                )
                output.update(features)
                previous[node] = spectrum
            rows.append({field: output.get(field, "") for field in FEATURE_FIELDS})
    return rows


def sanitized_alignment(report_path: Path) -> dict[str, Any]:
    if not report_path.is_file():
        return {}
    report = json.loads(report_path.read_text(encoding="utf-8"))
    result = {
        key: report.get(key)
        for key in (
            "generated_at_utc",
            "alignment_key",
            "matched_packets",
            "node1_match_ratio",
            "node3_match_ratio",
            "unmatched_node1",
            "unmatched_node3",
            "node3_minus_node1_system_ns_median",
            "node3_minus_node1_system_ns_mad",
            "node3_non_monotonic_steps",
            "warnings",
            "data_subcarriers",
            "pilot_subcarriers_removed",
            "aligned_shapes",
        )
    }
    for node in ("node1", "node3"):
        source = report.get(node, {})
        result[node] = {
            key: source.get(key)
            for key in (
                "all_frames",
                "target_packets",
                "duplicate_keys",
                "system_ns_start",
                "system_ns_end",
                "utc_start",
                "utc_end",
            )
        }
    camera = report.get("camera") or {}
    result["camera"] = {
        key: camera.get(key)
        for key in (
            "host_node",
            "reference_receiver",
            "depth_frames",
            "depth_measured_fps",
            "depth_sequence_gaps",
            "clock_offset_drift_ns",
            "pair_abs_dt_median_ns",
            "pair_abs_dt_p95_ns",
            "pair_abs_dt_max_ns",
            "pairs_beyond_half_frame",
            "calibration",
        )
    }
    return result


def export_metadata(rows: list[dict[str, str]], results_root: Path, output: Path) -> None:
    records: list[dict[str, Any]] = []
    for manifest_row in rows:
        trial_dir = results_root / manifest_row["directory"]
        npz_path = trial_dir / "aligned/aligned_csi.npz"
        record: dict[str, Any] = {
            "manifest": manifest_row,
            "experiment": parse_experiment(trial_dir / "experiment.txt"),
            "alignment": sanitized_alignment(trial_dir / "aligned/alignment_report.json"),
            "aligned_csi_size_bytes": npz_path.stat().st_size if npz_path.is_file() else None,
            "aligned_csi_sha256": sha256(npz_path) if npz_path.is_file() else None,
        }
        records.append(record)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(records, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def main() -> None:
    args = parse_args()
    if args.bin_ms <= 0:
        raise SystemExit("--bin-ms must be positive")
    manifest_rows = read_manifest(args.manifest)
    feature_rows: list[dict[str, str]] = []
    for manifest_row in manifest_rows:
        if manifest_row["valid"] != "yes":
            continue
        npz_path = args.results_root / manifest_row["directory"] / "aligned/aligned_csi.npz"
        if not npz_path.is_file():
            raise SystemExit(f"missing aligned CSI: {npz_path}")
        feature_rows.extend(
            export_trial_rows(
                manifest_row,
                npz_path,
                args.bin_ms / 1000.0,
                args.action_start_s,
                args.action_end_s,
            )
        )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    # Fix gzip mtime and stored filename so identical inputs produce identical
    # bytes and therefore stable Git diffs/checksums.
    with args.output.open("wb") as raw_stream:
        with gzip.GzipFile(
            filename="", mode="wb", fileobj=raw_stream, mtime=0
        ) as gzip_stream:
            with io.TextIOWrapper(gzip_stream, encoding="utf-8", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=FEATURE_FIELDS)
                writer.writeheader()
                writer.writerows(feature_rows)
    export_metadata(manifest_rows, args.results_root, args.metadata_output)
    print(f"wrote {len(feature_rows)} rows to {args.output}")
    print(f"wrote {len(manifest_rows)} trial records to {args.metadata_output}")


if __name__ == "__main__":
    main()
