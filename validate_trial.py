#!/usr/bin/env python3
"""Validate the aligned CSI data contract before a trial is accepted."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

import numpy as np


class ValidationError(ValueError):
    pass


BASE_KEYS = {
    "node1_csi",
    "node3_csi",
    "node1_csi_data",
    "node3_csi_data",
    "node1_frame_index",
    "node3_frame_index",
    "node1_system_ns",
    "node3_system_ns",
    "node3_system_ns_corrected",
    "node3_clock_residual_ns",
    "sequence",
    "task_id",
    "csi_segment",
    "packet_format",
    "bandwidth_mhz",
    "data_subcarrier_index",
    "pilot_subcarrier_index",
}

NON_ROW_KEYS = {
    "csi_segment",
    "packet_format",
    "bandwidth_mhz",
    "data_subcarrier_index",
    "pilot_subcarrier_index",
    "depth_timestamp_epoch_ns",
}

CAMERA_KEYS = {
    "depth_frame_index",
    "depth_dt_ns",
    "depth_timestamp_epoch_ns",
}


def _strictly_increasing(name: str, values: np.ndarray) -> None:
    if values.ndim != 1:
        raise ValidationError(f"{name} must be one-dimensional")
    if values.size > 1 and not np.all(np.diff(values.astype(np.int64)) > 0):
        raise ValidationError(f"{name} must be strictly increasing")


def _load_camera_meta(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValidationError(f"cannot read camera metadata {path}: {exc}") from exc


def validate_npz(
    path: Path,
    expected_subcarriers: int,
    require_camera: bool,
    camera_meta: Path | None = None,
    expected_packet_format: str | None = None,
    expected_bandwidth_mhz: int | None = None,
) -> dict[str, int | float | bool | str]:
    path = Path(path).expanduser().resolve()
    if expected_subcarriers <= 0:
        raise ValidationError("expected_subcarriers must be positive")

    try:
        archive = np.load(path, allow_pickle=False)
    except (OSError, ValueError) as exc:
        raise ValidationError(f"cannot read NPZ {path}: {exc}") from exc

    with archive as data:
        required = BASE_KEYS | (CAMERA_KEYS if require_camera else set())
        missing = required - set(data.files)
        if missing:
            raise ValidationError(f"missing keys: {', '.join(sorted(missing))}")

        rows = int(data["node1_csi_data"].shape[0])
        if rows == 0:
            raise ValidationError("aligned CSI contains zero rows")

        row_keys = required - NON_ROW_KEYS
        for key in sorted(row_keys):
            array = np.asarray(data[key])
            if array.ndim == 0 or array.shape[0] != rows:
                raise ValidationError(
                    f"{key} row count {array.shape[0] if array.ndim else 0} != {rows}"
                )

        for key in ("node1_csi_data", "node3_csi_data"):
            array = np.asarray(data[key])
            if array.ndim < 3:
                raise ValidationError(f"{key} has invalid shape {array.shape}")
            if int(array.shape[1]) != expected_subcarriers:
                raise ValidationError(
                    f"{key} has {array.shape[1]} subcarriers; expected {expected_subcarriers}"
                )

        for key in ("node1_csi", "node3_csi", "node1_csi_data", "node3_csi_data"):
            if not np.all(np.isfinite(np.asarray(data[key]))):
                raise ValidationError(f"{key} contains non-finite CSI values")

        if data["node1_csi_data"].shape != data["node3_csi_data"].shape:
            raise ValidationError("Node1 and Node3 model-input CSI shapes differ")
        segment = str(np.asarray(data["csi_segment"]).item())
        if segment != "CSI":
            raise ValidationError(
                f"model input must use the main CSI segment, found {segment}"
            )
        packet_format = str(np.asarray(data["packet_format"]).item())
        bandwidth_mhz = int(np.asarray(data["bandwidth_mhz"]).item())
        if expected_packet_format and packet_format != expected_packet_format:
            raise ValidationError(
                f"packet_format is {packet_format}; expected {expected_packet_format}"
            )
        if expected_bandwidth_mhz is not None and bandwidth_mhz != expected_bandwidth_mhz:
            raise ValidationError(
                f"bandwidth_mhz is {bandwidth_mhz}; expected {expected_bandwidth_mhz}"
            )

        data_indices = np.asarray(data["data_subcarrier_index"])
        pilot_indices = np.asarray(data["pilot_subcarrier_index"])
        if data_indices.ndim != 1 or data_indices.size != expected_subcarriers:
            raise ValidationError(
                "data_subcarrier_index does not match the model-input tone axis"
            )
        if np.unique(data_indices).size != data_indices.size:
            raise ValidationError("data_subcarrier_index contains duplicates")
        if pilot_indices.ndim != 1:
            raise ValidationError("pilot_subcarrier_index must be one-dimensional")
        if np.intersect1d(data_indices, pilot_indices).size:
            raise ValidationError("data and pilot subcarrier indices overlap")

        _strictly_increasing("node1_frame_index", data["node1_frame_index"])
        _strictly_increasing("node3_frame_index", data["node3_frame_index"])
        for key in (
            "node1_system_ns",
            "node3_system_ns",
            "node3_system_ns_corrected",
        ):
            _strictly_increasing(key, data[key])
            if not np.all(np.asarray(data[key]) > 0):
                raise ValidationError(f"{key} contains missing or zero timestamps")

        summary: dict[str, int | float | bool | str] = {
            "valid": True,
            "file": str(path),
            "rows": rows,
            "data_subcarriers": expected_subcarriers,
            "packet_format": packet_format,
            "bandwidth_mhz": bandwidth_mhz,
            "clock_residual_p95_ns": float(
                np.percentile(np.abs(data["node3_clock_residual_ns"]), 95)
            ),
        }

        if require_camera:
            if camera_meta is None:
                raise ValidationError("camera metadata is required")
            meta = _load_camera_meta(Path(camera_meta))
            depth = meta.get("depth", {})
            color = meta.get("color", {})
            frames = int(depth.get("frames", 0))
            gaps = int(depth.get("sequence_gaps", -1))
            if frames <= 0:
                raise ValidationError("camera metadata reports zero depth frames")
            if gaps != 0:
                raise ValidationError(f"camera has {gaps} depth sequence gaps")
            if color.get("decodable") is not True:
                raise ValidationError("camera H265 stream is not decodable")

            depth_timestamps = np.asarray(data["depth_timestamp_epoch_ns"])
            if depth_timestamps.ndim != 1 or depth_timestamps.size != frames:
                raise ValidationError(
                    "depth_timestamp_epoch_ns count does not match camera metadata"
                )
            depth_indices = np.asarray(data["depth_frame_index"], dtype=np.int64)
            if np.any(depth_indices < 0) or np.any(depth_indices >= frames):
                raise ValidationError("depth_frame_index is outside the depth frame range")
            summary["camera_depth_frames"] = frames
            summary["camera_sequence_gaps"] = gaps
            summary["camera_color_decodable"] = True

    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("npz", type=Path)
    parser.add_argument("--expected-subcarriers", type=int, required=True)
    parser.add_argument("--expected-packet-format")
    parser.add_argument("--expected-bandwidth-mhz", type=int)
    parser.add_argument("--require-camera", action="store_true")
    parser.add_argument("--camera-meta", type=Path)
    parser.add_argument("--json-output", type=Path)
    args = parser.parse_args()

    try:
        summary = validate_npz(
            args.npz,
            args.expected_subcarriers,
            args.require_camera,
            args.camera_meta,
            args.expected_packet_format,
            args.expected_bandwidth_mhz,
        )
    except ValidationError as exc:
        print(f"VALIDATION_ERROR={exc}", file=sys.stderr)
        return 2

    rendered = json.dumps(summary, ensure_ascii=False, indent=2)
    if args.json_output:
        args.json_output.write_text(rendered + "\n", encoding="utf-8")
    print(f"VALIDATION_OK=1")
    print(f"ROWS={summary['rows']}")
    print(f"DATA_SUBCARRIERS={summary['data_subcarriers']}")
    print(f"CLOCK_RESIDUAL_P95_NS={summary['clock_residual_p95_ns']:.0f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
