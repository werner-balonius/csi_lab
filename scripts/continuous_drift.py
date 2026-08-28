#!/usr/bin/env python3
"""Segment one uninterrupted empty-room CSI capture and quantify drift.

The input trial must contain ``aligned/aligned_csi.npz``.  CSI amplitude is
averaged within fixed-duration blocks and normalized per antenna by its own
band mean, removing broadband AGC changes before spectra are compared.

Example:
    python3 scripts/continuous_drift.py \
      ../csi_results/20260828_183452_1t2r_ds_d01m_empty_continuous_300s_01 \
      --block-seconds 30 --discard-seconds 30 --csv /tmp/drift.csv
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

import numpy as np


def normalized_spectrum(amplitude: np.ndarray) -> np.ndarray:
    spectrum = np.nanmean(amplitude, axis=0)
    return spectrum / (np.nanmean(spectrum, axis=0, keepdims=True) + 1e-12)


def relative_l2(a: np.ndarray, b: np.ndarray) -> float:
    midpoint = (a + b) / 2
    return float(np.linalg.norm(a - b) / (np.linalg.norm(midpoint) + 1e-12) * 100)


def interval_spectrum(
    amplitude: np.ndarray, time_s: np.ndarray, start: float, end: float
) -> tuple[np.ndarray, np.ndarray]:
    mask = (time_s >= start) & (time_s < end)
    if mask.sum() < 100:
        raise ValueError(f"insufficient packets in {start:g}-{end:g} s: {mask.sum()}")
    return mask, normalized_spectrum(amplitude[mask])


def load_node(npz: np.lib.npyio.NpzFile, node: str) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    amplitude = np.abs(npz[f"{node}_csi_data"][:, :, :, 0]).astype(np.float64)
    system_ns = npz[f"{node}_system_ns"].astype(np.int64).reshape(-1)
    rssi = npz[f"{node}_rssi"].astype(float).reshape(-1)
    rssi[(rssi >= 0) | (rssi < -110)] = np.nan
    n = min(len(amplitude), len(system_ns), len(rssi))
    amplitude, system_ns, rssi = amplitude[:n], system_ns[:n], rssi[:n]
    return amplitude, (system_ns - system_ns[0]) / 1e9, rssi


def analyze_node(
    npz: np.lib.npyio.NpzFile,
    node: str,
    block_seconds: int,
    discard_seconds: int,
    fine_seconds: int,
) -> tuple[list[dict], dict]:
    amplitude, time_s, rssi = load_node(npz, node)
    duration = float(time_s[-1])
    # The final received packet is normally a few milliseconds before the
    # nominal trial boundary (for example 299.98 s for a 300 s capture).  Add a
    # small tolerance before flooring so the complete final block is retained.
    boundary_tolerance = min(1.0, block_seconds * 0.05)
    analysis_end = int((duration + boundary_tolerance) // block_seconds) * block_seconds
    if analysis_end - discard_seconds < 2 * block_seconds:
        raise ValueError(
            f"{node}: capture after discard is too short: "
            f"duration={duration:.2f}s discard={discard_seconds}s"
        )

    blocks = []
    for start in range(0, analysis_end, block_seconds):
        mask, spectrum = interval_spectrum(amplitude, time_s, start, start + block_seconds)
        blocks.append((start, mask, spectrum))
    reference = next((s for start, _, s in blocks if start == discard_seconds), None)
    if reference is None:
        raise ValueError("discard-seconds must align with a block boundary")

    rows = []
    for start, mask, spectrum in blocks:
        absolute_mean = np.nanmean(amplitude[mask], axis=(0, 1))
        row = {
            "node": node,
            "start_s": start,
            "end_s": start + block_seconds,
            "packets": int(mask.sum()),
            "rssi_median_dbm": float(np.nanmedian(rssi[mask])),
            "rssi_std_db": float(np.nanstd(rssi[mask])),
            "spectrum_vs_reference_pct": relative_l2(spectrum.ravel(), reference.ravel()),
        }
        for antenna, value in enumerate(absolute_mean):
            row[f"absolute_mean_ant{antenna}"] = float(value)
        rows.append(row)

    late_blocks = [x for x in blocks if x[0] >= discard_seconds]
    consecutive = [
        relative_l2(late_blocks[i - 1][2].ravel(), late_blocks[i][2].ravel())
        for i in range(1, len(late_blocks))
    ]
    midpoint = discard_seconds + (analysis_end - discard_seconds) / 2
    _, first_half = interval_spectrum(amplitude, time_s, discard_seconds, midpoint)
    _, second_half = interval_spectrum(amplitude, time_s, midpoint, analysis_end)
    edge_width = min(2 * block_seconds, (analysis_end - discard_seconds) // 2)
    _, first_edge = interval_spectrum(
        amplitude, time_s, discard_seconds, discard_seconds + edge_width
    )
    _, last_edge = interval_spectrum(
        amplitude, time_s, analysis_end - edge_width, analysis_end
    )

    fine = []
    for start in range(discard_seconds, analysis_end, fine_seconds):
        _, spectrum = interval_spectrum(amplitude, time_s, start, start + fine_seconds)
        fine.append(spectrum)
    fine_consecutive = [
        relative_l2(fine[i - 1].ravel(), fine[i].ravel()) for i in range(1, len(fine))
    ]
    distances = np.asarray([r["spectrum_vs_reference_pct"] for r in rows if r["start_s"] >= discard_seconds])
    trend_x = np.arange(len(distances), dtype=float)

    summary = {
        "node": node,
        "duration_s": duration,
        "packets": len(time_s),
        "post_discard_half_pct": relative_l2(first_half.ravel(), second_half.ravel()),
        "post_discard_half_ant_pct": [
            relative_l2(first_half[:, i], second_half[:, i])
            for i in range(first_half.shape[1])
        ],
        "post_discard_first_vs_last_pct": relative_l2(first_edge.ravel(), last_edge.ravel()),
        "consecutive_block_median_pct": float(np.median(consecutive)),
        "consecutive_block_max_pct": float(np.max(consecutive)),
        "consecutive_fine_median_pct": float(np.median(fine_consecutive)),
        "consecutive_fine_p95_pct": float(np.percentile(fine_consecutive, 95)),
        "consecutive_fine_max_pct": float(np.max(fine_consecutive)),
        "distance_from_reference_trend_correlation": float(np.corrcoef(trend_x, distances)[0, 1]),
    }
    return rows, summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("trial", help="Trial directory containing aligned/aligned_csi.npz")
    parser.add_argument("--nodes", default="node1,node3")
    parser.add_argument("--block-seconds", type=int, default=30)
    parser.add_argument("--discard-seconds", type=int, default=30)
    parser.add_argument("--fine-seconds", type=int, default=10)
    parser.add_argument("--csv", help="Optional block-level CSV output")
    args = parser.parse_args()

    trial = Path(args.trial).expanduser().resolve()
    npz_path = trial / "aligned" / "aligned_csi.npz"
    if not npz_path.is_file():
        parser.error(f"missing {npz_path}")
    if min(args.block_seconds, args.fine_seconds) <= 0 or args.discard_seconds < 0:
        parser.error("segment durations must be positive and discard must be non-negative")
    if args.discard_seconds % args.block_seconds:
        parser.error("discard-seconds must be a multiple of block-seconds")

    report_path = trial / "aligned" / "alignment_report.json"
    if report_path.is_file():
        report = json.loads(report_path.read_text())
        print(
            f"matched={report.get('matched_packets')} "
            f"node1_ratio={report.get('node1_match_ratio', float('nan')):.6f} "
            f"node3_ratio={report.get('node3_match_ratio', float('nan')):.6f} "
            f"non_monotonic={report.get('node3_non_monotonic_steps')}"
        )
        for node in ("node1", "node3"):
            info = report.get(node, {})
            print(
                f"{node}_duplicate_keys={info.get('duplicate_keys')} "
                f"target_packets={info.get('target_packets')}"
            )

    all_rows = []
    with np.load(npz_path, allow_pickle=False) as npz:
        for node in args.nodes.split(","):
            rows, summary = analyze_node(
                npz, node.strip(), args.block_seconds, args.discard_seconds, args.fine_seconds
            )
            all_rows.extend(rows)
            print(f"\n--- {node} ---")
            print("sec packets rssi_med rssi_std spectrum_vs_reference_pct")
            for row in rows:
                print(
                    f"{row['start_s']:03d}-{row['end_s']:03d} "
                    f"{row['packets']:5d} {row['rssi_median_dbm']:8.2f} "
                    f"{row['rssi_std_db']:8.3f} "
                    f"{row['spectrum_vs_reference_pct']:10.3f}"
                )
            print("summary=" + json.dumps(summary, ensure_ascii=False))

    if args.csv:
        output = Path(args.csv).expanduser().resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(all_rows[0]))
            writer.writeheader()
            writer.writerows(all_rows)
        print(f"CSV={output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
