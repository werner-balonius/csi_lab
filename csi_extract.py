#!/usr/bin/env python3
"""Extract model-ready main CSI frames from a PicoScenes capture."""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import sys

import numpy as np


PACKET_FORMATS = {0: "NonHT", 1: "HT", 2: "VHT", 3: "HESU", 4: "HEMU"}


def format_mac(values) -> str:
    return ":".join(f"{int(value):02x}" for value in (values or []))


def reshape_main_csi(frame: dict) -> np.ndarray:
    segment = frame.get("CSI")
    if not segment:
        raise ValueError("frame does not contain the main CSI segment")
    tones = int(segment.get("numTones") or 0)
    tx = int(segment.get("numTx") or 0)
    rx = int(segment.get("numRx") or 0)
    matrices = int(segment.get("numCSI") or 1)
    values = np.asarray(segment.get("CSI") or [], dtype=np.complex64)
    expected = tones * tx * rx * matrices
    if min(tones, tx, rx, matrices) <= 0 or values.size != expected:
        raise ValueError(
            f"invalid main CSI dimensions: tones={tones}, tx={tx}, rx={rx}, "
            f"numCSI={matrices}, values={values.size}"
        )
    shaped = values.reshape((tones, tx, rx, matrices), order="F").transpose(0, 2, 1, 3)
    return shaped[..., 0] if matrices == 1 else shaped


def normalize_occupied_csi(
    matrix: np.ndarray,
    subcarrier_indices,
    packet_format: str,
    bandwidth_mhz: int,
) -> tuple[np.ndarray, np.ndarray]:
    indices = np.asarray(subcarrier_indices, dtype=np.int16)
    if indices.ndim != 1 or indices.size != matrix.shape[0]:
        raise ValueError("SubcarrierIndex does not match the CSI tone axis")
    expected_occupied = {
        ("HT", 20): 56,
        ("VHT", 20): 56,
        ("VHT", 80): 242,
        ("HESU", 20): 242,
    }.get((packet_format, bandwidth_mhz))
    if expected_occupied is None or indices.size == expected_occupied:
        return matrix, indices

    if expected_occupied == 56 and indices.size == 57:
        keep = indices != 0
    elif expected_occupied == 242 and indices.size == 245:
        keep = ~np.isin(indices, (-1, 0, 1))
    else:
        raise ValueError(
            f"unexpected tone representation for {packet_format}{bandwidth_mhz}: "
            f"found {indices.size}, expected {expected_occupied} occupied tones"
        )
    return matrix[keep], indices[keep]


def dominant_signature(signatures):
    counts = Counter(signatures)
    if not counts:
        raise ValueError("no CSI signatures")
    return counts.most_common(1)[0][0]


def extract_capture(path: Path, output: Path) -> dict:
    try:
        from picoscenes import Picoscenes
    except ImportError as exc:
        raise RuntimeError(
            "PicoScenes-Python-Toolbox is required; build/install its picoscenes module"
        ) from exc

    frames = Picoscenes(str(path)).raw
    candidates = []
    signatures = []
    for frame_index, frame in enumerate(frames):
        header = frame.get("StandardHeader") or {}
        pico = frame.get("PicoScenesHeader") or {}
        if pico.get("TaskId") is None or not frame.get("CSI"):
            continue
        try:
            matrix = reshape_main_csi(frame)
            segment = frame["CSI"]
            packet_format_code = int(segment.get("PacketFormat", -1))
            packet_format = PACKET_FORMATS.get(
                packet_format_code, f"format_{packet_format_code}"
            )
            bandwidth = int(segment.get("CBW") or 0)
            matrix, subcarrier_indices = normalize_occupied_csi(
                matrix,
                segment.get("SubcarrierIndex") or [],
                packet_format,
                bandwidth,
            )
        except ValueError:
            continue
        source = format_mac(header.get("Addr2"))
        if not source:
            continue
        key = (source, tuple(matrix.shape), packet_format, bandwidth)
        signatures.append(key)
        candidates.append(
            (
                frame_index, frame, source, matrix, subcarrier_indices,
                packet_format, bandwidth,
            )
        )

    if not signatures:
        raise RuntimeError("no valid main CSI frames with TaskId were found")
    target_source, target_shape, target_format, target_bandwidth = dominant_signature(
        signatures
    )
    selected = [
        (index, frame, matrix, indices)
        for index, frame, source, matrix, indices, packet_format, bandwidth in candidates
        if (
            source == target_source
            and tuple(matrix.shape) == target_shape
            and packet_format == target_format
            and bandwidth == target_bandwidth
        )
    ]
    first_segment = selected[0][1]["CSI"]
    packet_format_code = int(first_segment.get("PacketFormat", -1))
    packet_format = PACKET_FORMATS.get(packet_format_code, f"format_{packet_format_code}")
    bandwidth = int(first_segment.get("CBW") or 0)

    output.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        output,
        target_csi=np.stack([entry[2] for entry in selected]),
        frame_index=np.asarray([entry[0] for entry in selected], dtype=np.int64),
        source_mac=np.asarray(target_source),
        csi_segment=np.asarray("CSI"),
        packet_format=np.asarray(packet_format),
        bandwidth_mhz=np.asarray(bandwidth, dtype=np.int16),
        num_tones=np.asarray(target_shape[0], dtype=np.int16),
        subcarrier_index=np.asarray(selected[0][3], dtype=np.int16),
    )
    return {
        "source_file": str(path.resolve()),
        "output_file": str(output.resolve()),
        "all_frames": len(frames),
        "selected_frames": len(selected),
        "source_mac": target_source,
        "csi_segment": "CSI",
        "packet_format": packet_format,
        "bandwidth_mhz": bandwidth,
        "csi_shape_per_frame": list(target_shape),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("capture", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    output = args.output or args.capture.with_name(
        f"{args.capture.stem}_target_csi.npz"
    )
    try:
        report = extract_capture(args.capture, output)
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"EXTRACT_ERROR={exc}", file=sys.stderr)
        return 2
    report_path = output.with_suffix(".json")
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print("CSI_SEGMENT=CSI")
    print(f"PACKET_FORMAT={report['packet_format']}")
    print(f"BANDWIDTH_MHZ={report['bandwidth_mhz']}")
    print(f"NUM_TONES={report['csi_shape_per_frame'][0]}")
    print(f"TARGET_FRAMES={report['selected_frames']}")
    print(f"TARGETNPZ={output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
