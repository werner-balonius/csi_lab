#!/usr/bin/env python3
"""Report per-trial closure and event metrics for distance experiments."""
from __future__ import annotations

import argparse
import csv
import json
import os
from collections import defaultdict

import numpy as np

from distance_sensitivity import (
    clean_rssi,
    discover,
    normalized_spectrum,
    packet_motion,
    rel_l2,
)


def median(values: np.ndarray) -> float:
    return float(np.nanmedian(values))


def analyze(trial: dict, node: str) -> dict:
    aligned = os.path.join(trial["path"], "aligned", "aligned_csi.npz")
    report_path = os.path.join(trial["path"], "aligned", "alignment_report.json")
    with np.load(aligned, allow_pickle=False) as z:
        csi = z[f"{node}_csi_data"]
        ns = z[f"{node}_system_ns"].astype(np.int64).reshape(-1)
        rssi = clean_rssi(z[f"{node}_rssi"])
    with open(report_path, encoding="utf-8") as f:
        report = json.load(f)

    n = min(len(csi), len(ns), len(rssi))
    csi, ns, rssi = csi[:n], ns[:n], rssi[:n]
    t = (ns - ns[0]) / 1e9
    pre = (t >= 0) & (t < 4)
    event = (t >= 8) & (t < 21)
    post = (t >= 27) & (t < 34)
    baseline = pre | post

    pre_spec = normalized_spectrum(csi, pre)
    event_spec = normalized_spectrum(csi, event)
    post_spec = normalized_spectrum(csi, post)
    baseline_spec = normalized_spectrum(csi, baseline)

    mot_idx, mot_energy = packet_motion(csi)
    mot_t = t[mot_idx]
    mot_pre = median(mot_energy[(mot_t >= 0) & (mot_t < 4)])
    mot_event = median(mot_energy[(mot_t >= 8) & (mot_t < 21)])
    mot_post = median(mot_energy[(mot_t >= 27) & (mot_t < 34)])
    mot_base = median(
        mot_energy[((mot_t >= 0) & (mot_t < 4)) |
                   ((mot_t >= 27) & (mot_t < 34))]
    )

    return {
        "trial": trial["trial"],
        "distance_m": trial["distance"],
        "offset_m": trial["offset"],
        "state": trial["state"],
        "rep": trial["rep"],
        "matched_packets": report["matched_packets"],
        "node1_match_ratio": report["node1_match_ratio"],
        "node3_match_ratio": report["node3_match_ratio"],
        "non_monotonic_steps": report["node3_non_monotonic_steps"],
        "data_subcarriers": report["data_subcarriers"],
        "pre_event_spectrum_pct": rel_l2(pre_spec, event_spec),
        "event_post_spectrum_pct": rel_l2(event_spec, post_spec),
        "event_combined_spectrum_pct": rel_l2(event_spec, baseline_spec),
        "closure_spectrum_pct": rel_l2(pre_spec, post_spec),
        "motion_event_pre_ratio": mot_event / (mot_pre + 1e-12),
        "motion_event_post_ratio": mot_event / (mot_post + 1e-12),
        "motion_event_combined_ratio": mot_event / (mot_base + 1e-12),
        "rssi_pre_dbm": median(rssi[pre]),
        "rssi_event_dbm": median(rssi[event]),
        "rssi_post_dbm": median(rssi[post]),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--results", default="~/csi_results")
    ap.add_argument("--date", help="Optional YYYYMMDD prefix")
    ap.add_argument("--distance", type=int, choices=(1, 3, 5))
    ap.add_argument("--node", default="node1", choices=("node1", "node3"))
    ap.add_argument("--output", help="Optional per-trial CSV")
    args = ap.parse_args()

    trials, _ = discover(args.results, args.date)
    if args.distance:
        trials = [x for x in trials if x["distance"] == args.distance]
    rows = [analyze(trial, args.node) for trial in trials]

    print(f"completed human trials={len(rows)} node={args.node}")
    print("trial  event_% closure_% motion/pre motion/post n1_match n3_match")
    for row in rows:
        print(
            f"{row['trial']}  {row['event_combined_spectrum_pct']:.3f}  "
            f"{row['closure_spectrum_pct']:.3f}  "
            f"{row['motion_event_pre_ratio']:.3f}  "
            f"{row['motion_event_post_ratio']:.3f}  "
            f"{row['node1_match_ratio']:.4f}  {row['node3_match_ratio']:.4f}"
        )

    groups: dict[tuple[float, str], list[dict]] = defaultdict(list)
    for row in rows:
        groups[(row["offset_m"], row["state"])].append(row)
    if groups:
        print("\ngroup  event/combined_% median [range]  "
              "pre/event_% median [range]  motion/base median [range]  "
              "closure_% median/max")
    for (offset, state), group in sorted(groups.items()):
        event = np.asarray([x["event_combined_spectrum_pct"] for x in group])
        pre_event = np.asarray([x["pre_event_spectrum_pct"] for x in group])
        motion = np.asarray([x["motion_event_combined_ratio"] for x in group])
        closure = np.asarray([x["closure_spectrum_pct"] for x in group])
        print(
            f"off={offset:.1f} {state:<6}  {np.median(event):.3f} "
            f"[{np.min(event):.3f}, {np.max(event):.3f}]  "
            f"{np.median(pre_event):.3f} "
            f"[{np.min(pre_event):.3f}, {np.max(pre_event):.3f}]  "
            f"{np.median(motion):.3f} "
            f"[{np.min(motion):.3f}, {np.max(motion):.3f}]  "
            f"{np.median(closure):.3f}/{np.max(closure):.3f}"
        )

    if args.output and rows:
        output = os.path.expanduser(args.output)
        os.makedirs(os.path.dirname(os.path.abspath(output)), exist_ok=True)
        with open(output, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
        print(f"CSV={output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
