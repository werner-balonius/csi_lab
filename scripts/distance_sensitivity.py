#!/usr/bin/env python3
"""Evaluate presence/motion detection versus link distance and lateral offset.

Expected human trial names:
  ds_d01m_off00_static_r1
  ds_d03m_off05_motion_r2
  ds_d05m_off10_static_r3

Each 35 s trial is self-normalized: 0-4 s and 27-34 s are empty baselines;
8-21 s is the scored event window. Transition intervals are discarded.

Reported metrics are grouped by trial, not by randomly splitting correlated
windows. A threshold for each held-out trial is the 95th percentile of empty
windows from the *other* trials at the same distance. This avoids learning the
test trial's own baseline threshold.
"""
from __future__ import annotations

import argparse
import csv
import glob
import os
import re
from collections import defaultdict

import numpy as np


TRIAL_RE = re.compile(
    r"ds_d(?P<distance>01|03|05)m_off(?P<offset>00|05|10)_"
    r"(?P<state>static|motion)_r(?P<rep>\d+)"
)
CONTROL_RE = re.compile(
    r"ds_d(?P<distance>01|03|05)m_plate_(?P<where>rx|mid)_r(?P<rep>\d+)"
)
SCORES = ("rssi_abs", "spectrum", "motion")


def clean_rssi(x: np.ndarray) -> np.ndarray:
    x = np.asarray(x, dtype=float).reshape(-1)
    x[(x >= 0) | (x < -110)] = np.nan
    return x


def normalized_spectrum(csi: np.ndarray, mask: np.ndarray) -> np.ndarray:
    a = np.abs(csi[mask, :, :, 0]).astype(np.float64)
    s = np.nanmean(a, axis=0)
    return (s / (np.nanmean(s, axis=0, keepdims=True) + 1e-12)).ravel()


def rel_l2(a: np.ndarray, b: np.ndarray) -> float:
    den = np.linalg.norm((a + b) / 2) + 1e-12
    return float(np.linalg.norm(a - b) / den * 100)


def packet_motion(csi: np.ndarray, lag: int = 5) -> tuple[np.ndarray, np.ndarray]:
    """AGC-resistant normalized-amplitude change at about 25 ms lag."""
    a = np.abs(csi[:, :, :, 0]).astype(np.float64)
    a /= np.nanmean(a, axis=1, keepdims=True) + 1e-12
    delta = a[lag:] - a[:-lag]
    energy = np.sqrt(np.nanmean(delta * delta, axis=(1, 2)))
    return np.arange(lag, len(a)), energy


def load_trial(path: str, node: str) -> dict:
    p = os.path.join(path, "aligned", "aligned_csi.npz")
    if not os.path.isfile(p):
        raise FileNotFoundError(p)
    with np.load(p, allow_pickle=False) as z:
        csi = z[f"{node}_csi_data"]
        ns = z[f"{node}_system_ns"].astype(np.int64).reshape(-1)
        rssi = clean_rssi(z[f"{node}_rssi"])
    n = min(len(csi), len(ns), len(rssi))
    csi, ns, rssi = csi[:n], ns[:n], rssi[:n]
    t = (ns - ns[0]) / 1e9
    baseline = ((t >= 0) & (t < 4)) | ((t >= 27) & (t < 34))
    event = (t >= 8) & (t < 21)
    if baseline.sum() < 200 or event.sum() < 500:
        raise ValueError(f"{path}: insufficient packets in protocol windows")

    base_rssi = float(np.nanmedian(rssi[baseline]))
    base_spec = normalized_spectrum(csi, baseline)
    mot_idx, mot_energy = packet_motion(csi)
    mot_t = t[mot_idx]

    rows = []
    for sec in range(35):
        is_base = (0 <= sec < 4) or (27 <= sec < 34)
        is_event = 8 <= sec < 21
        if not (is_base or is_event):
            continue
        m = (t >= sec) & (t < sec + 1)
        mm = (mot_t >= sec) & (mot_t < sec + 1)
        if m.sum() < 80 or mm.sum() < 60:
            continue
        bin_rssi = float(np.nanmedian(rssi[m]))
        rows.append({
            "second": sec,
            "label": int(is_event),
            "rssi_abs": abs(bin_rssi - base_rssi),
            "rssi_drop": base_rssi - bin_rssi,
            "spectrum": rel_l2(normalized_spectrum(csi, m), base_spec),
            "motion": float(np.nanmedian(mot_energy[mm])),
        })
    return {"rows": rows, "base_rssi": base_rssi}


def auc(pos: list[float], neg: list[float]) -> float:
    if not pos or not neg:
        return float("nan")
    p, n = np.asarray(pos)[:, None], np.asarray(neg)[None, :]
    return float(np.mean((p > n) + 0.5 * (p == n)))


def discover(results: str, date: str | None) -> tuple[list[dict], list[dict]]:
    pattern = os.path.join(os.path.expanduser(results), f"{date or '*'}_*")
    trials, controls = [], []
    for path in sorted(glob.glob(pattern)):
        name = os.path.basename(path)
        m = TRIAL_RE.search(name)
        c = CONTROL_RE.search(name)
        if m:
            x = m.groupdict()
            x.update(path=path, trial=name, distance=int(x["distance"]),
                     offset=int(x["offset"]) / 10, rep=int(x["rep"]))
            trials.append(x)
        elif c:
            x = c.groupdict()
            x.update(path=path, trial=name, distance=int(x["distance"]),
                     rep=int(x["rep"]))
            controls.append(x)
    return trials, controls


def evaluate(trials: list[dict], node: str) -> list[dict]:
    loaded = []
    for trial in trials:
        item = dict(trial)
        item.update(load_trial(trial["path"], node))
        loaded.append(item)

    predictions = []
    for test in loaded:
        train = [x for x in loaded
                 if x["distance"] == test["distance"] and x["trial"] != test["trial"]]
        for score in SCORES:
            train_neg = [r[score] for x in train for r in x["rows"] if r["label"] == 0]
            if not train_neg:
                continue
            threshold = float(np.percentile(train_neg, 95))
            event_scores = []
            for row in test["rows"]:
                pred = int(row[score] > threshold)
                predictions.append({
                    **{k: test[k] for k in ("trial", "distance", "offset", "state", "rep")},
                    "score": score, "label": row["label"], "pred": pred,
                    "value": row[score], "threshold": threshold,
                })
                if row["label"]:
                    event_scores.append(row[score])
            predictions.append({
                **{k: test[k] for k in ("trial", "distance", "offset", "state", "rep")},
                "score": score + "_trial", "label": 1,
                "pred": int(float(np.median(event_scores)) > threshold),
                "value": float(np.median(event_scores)), "threshold": threshold,
            })
    return predictions


def summarize(predictions: list[dict]) -> list[dict]:
    groups = defaultdict(list)
    for r in predictions:
        if r["score"].endswith("_trial"):
            continue
        groups[(r["distance"], r["offset"], r["state"], r["score"])].append(r)
    trial_groups = defaultdict(list)
    for r in predictions:
        if r["score"].endswith("_trial"):
            trial_groups[(r["distance"], r["offset"], r["state"],
                          r["score"].removesuffix("_trial"))].append(r)

    out = []
    for key, rows in sorted(groups.items()):
        pos = [r for r in rows if r["label"] == 1]
        neg = [r for r in rows if r["label"] == 0]
        tp = sum(r["pred"] for r in pos)
        tn = sum(not r["pred"] for r in neg)
        sensitivity = tp / len(pos) if pos else float("nan")
        specificity = tn / len(neg) if neg else float("nan")
        tg = trial_groups[key]
        out.append({
            "distance_m": key[0], "offset_m": key[1], "state": key[2],
            "score": key[3], "sensitivity": sensitivity,
            "specificity": specificity,
            "balanced_accuracy": (sensitivity + specificity) / 2,
            "accuracy": (tp + tn) / (len(pos) + len(neg)),
            "auc": auc([r["value"] for r in pos], [r["value"] for r in neg]),
            "trial_detection_rate": np.mean([r["pred"] for r in tg]) if tg else np.nan,
            "n_trials": len(tg),
        })
    return out


def print_controls(controls: list[dict], node: str) -> None:
    print("\nPhysical controls (event median relative to its own empty baseline)")
    print("distance  location  RSSI_drop_dB  spectrum_%  motion")
    for c in controls:
        d = load_trial(c["path"], node)
        event = [r for r in d["rows"] if r["label"]]
        print(f"{c['distance']:>5} m  {c['where']:<8}  "
              f"{np.median([r['rssi_drop'] for r in event]):>12.2f}  "
              f"{np.median([r['spectrum'] for r in event]):>10.2f}  "
              f"{np.median([r['motion'] for r in event]):.5f}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--results", default="~/csi_results")
    ap.add_argument("--date", help="Optional YYYYMMDD prefix")
    ap.add_argument("--node", default="node1", choices=("node1", "node3"))
    ap.add_argument("--output", help="Optional summary CSV")
    args = ap.parse_args()

    trials, controls = discover(args.results, args.date)
    print(f"human trials={len(trials)} controls={len(controls)} node={args.node}")
    if controls:
        print_controls(controls, args.node)
    if not trials:
        print("No human distance trials found.")
        return 0

    pred = evaluate(trials, args.node)
    summary = summarize(pred)
    print("\nWindow-level grouped evaluation (LOO baseline threshold)")
    print("dist off state  score      sens  spec  bal_acc  AUC  trial_det  n")
    for r in summary:
        print(f"{r['distance_m']:>3}  {r['offset_m']:>3.1f} {r['state']:<6} "
              f"{r['score']:<9} {r['sensitivity']:.3f} {r['specificity']:.3f} "
              f"{r['balanced_accuracy']:.3f} {r['auc']:.3f} "
              f"{r['trial_detection_rate']:.3f} {r['n_trials']}")
    if args.output and summary:
        out = os.path.expanduser(args.output)
        os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
        with open(out, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(summary[0]))
            w.writeheader(); w.writerows(summary)
        print(f"\nCSV={out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
