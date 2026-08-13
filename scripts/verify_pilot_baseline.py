#!/usr/bin/env python3
"""verify_pilot_baseline.py — 用 trial 内前后空场重算 8-11 pilot 的响应倍率。

为什么需要这个脚本
------------------
`docs/experiments/20260811_pilot/README.md` 的结果表用**外部空场**
（`empty_05m_pilot_02` / `empty_chair_05m_pilot_01`）作基线。
外部基线会把两次采集之间的状态差异计入"动作响应"。

同一份 README 的「分析限制」第 1 条已经指出必须用 trial 内前后空场归一化。
本脚本就按那个要求重算，两种基线并列输出，便于对照。

用法
----
    python verify_pilot_baseline.py [features.csv.gz]

默认读 data/processed/20260811_pilot/pilot_100ms_features.csv.gz。
只依赖标准库 + 无（纯 csv/statistics），不需要 numpy。
"""

from __future__ import annotations

import collections
import csv
import gzip
import statistics as st
import sys
from pathlib import Path

DEFAULT = Path(__file__).resolve().parents[1] / (
    "data/processed/20260811_pilot/pilot_100ms_features.csv.gz"
)

ACTION_PHASE = "action_candidate_hint"
PRE_PHASE = "pre_empty_hint"
POST_PHASE = "post_empty_hint"

# 外部基线：无座椅布局用 empty_05m_pilot_02，座椅布局用 empty_chair_05m_pilot_01
EXTERNAL_BASELINE = {
    "arm_wave": "empty_05m_pilot_02",
    "leg_lift": "empty_05m_pilot_02",
    "walk_link2": "empty_05m_pilot_02",
    "sit_to_stand": "empty_chair_05m_pilot_01",
}

METRICS = [
    ("node3_change_ant0", "CSI变化 ant0"),
    ("node3_change_ant1", "CSI变化 ant1"),
    ("node3_rssi_std_db", "RSSI std"),
]


def mean_of(rows, key):
    values = [float(r[key]) for r in rows if r.get(key) not in ("", "nan", None)]
    return st.mean(values) if values else float("nan")


def load(path: Path):
    with gzip.open(path, "rt") as stream:
        return list(csv.DictReader(stream))


def main() -> int:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT
    if not path.exists():
        print(f"找不到特征文件: {path}", file=sys.stderr)
        return 1

    rows = load(path)
    by_trial = collections.defaultdict(lambda: collections.defaultdict(list))
    for row in rows:
        by_trial[row["trial"]][row["protocol_phase"]].append(row)

    # 外部基线：整条 trial 的全部窗口
    external = {}
    for name in set(EXTERNAL_BASELINE.values()):
        flat = [r for phase in by_trial.get(name, {}).values() for r in phase]
        if flat:
            external[name] = {k: mean_of(flat, k) for k, _ in METRICS}

    print(f"特征文件: {path.name}   窗口数: {len(rows)}")
    print()
    print("外部基线取值")
    for name, vals in sorted(external.items()):
        detail = "  ".join(f"{lbl}={vals[k]:.4f}" for k, lbl in METRICS)
        print(f"  {name:28s} {detail}")
    print()

    header = (
        f"  {'trial':34s} {'类别':12s} "
        f"{'自归一':>8s} {'外部':>8s} {'倍数差':>8s}"
    )
    print("按 trial（指标 node3_change_ant0）")
    print(header)

    grouped = collections.defaultdict(list)
    for trial, phases in sorted(by_trial.items()):
        action = phases.get(ACTION_PHASE, [])
        inside = phases.get(PRE_PHASE, []) + phases.get(POST_PHASE, [])
        if not action or not inside:
            continue
        cls = action[0]["class"]
        base_name = EXTERNAL_BASELINE.get(cls)
        if base_name not in external:
            continue

        act = mean_of(action, "node3_change_ant0")
        own = mean_of(inside, "node3_change_ant0")
        ext = external[base_name]["node3_change_ant0"]
        self_ratio = act / own if own else float("nan")
        ext_ratio = act / ext if ext else float("nan")
        grouped[cls].append((self_ratio, ext_ratio))
        print(
            f"  {trial[:34]:34s} {cls:12s} "
            f"{self_ratio:8.2f} {ext_ratio:8.2f} {ext_ratio / self_ratio:7.1f}x"
        )

    print()
    print("按类汇总")
    print(f"  {'类别':14s} {'n':>2s}  {'trial内自归一化':>18s}  {'外部空场基线':>16s}")
    for cls, values in sorted(grouped.items()):
        s = [v[0] for v in values]
        e = [v[1] for v in values]
        print(
            f"  {cls:14s} {len(values):2d}  "
            f"{min(s):7.2f}-{max(s):<7.2f}  {min(e):7.2f}-{max(e):<7.2f}"
        )

    print()
    print("说明：自归一化 >1 表示动作段变化强于同一次实验的静止段。")
    print("      两列差异越大，说明外部基线受采集间状态差异影响越重。")

    # 双天线方向一致性检查
    print()
    print("两根接收天线的方向一致性（自归一化）")
    print(f"  {'trial':34s} {'ant0':>8s} {'ant1':>8s}")
    for trial, phases in sorted(by_trial.items()):
        action = phases.get(ACTION_PHASE, [])
        inside = phases.get(PRE_PHASE, []) + phases.get(POST_PHASE, [])
        if not action or not inside:
            continue
        out = []
        for key in ("node3_change_ant0", "node3_change_ant1"):
            act = mean_of(action, key)
            own = mean_of(inside, key)
            out.append(act / own if own else float("nan"))
        flag = "  <- 方向相反" if (out[0] - 1) * (out[1] - 1) < 0 else ""
        print(f"  {trial[:34]:34s} {out[0]:8.2f} {out[1]:8.2f}{flag}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
