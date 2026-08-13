#!/usr/bin/env python3
"""subcarrier_probe.py — 子载波级特征探查（在接收端节点上运行）。

动机
----
仓库里的 100 ms 特征已跨 234 个子载波取平均，小幅动作的频率选择性响应
会被抹平。本脚本直接读节点本地 `*_target_csi.npz` 的完整
(T, 242, 2, 1) 复数矩阵，比较多种特征在
「动作段 vs 同一 trial 的静止段」上的可分性。

关键约定
--------
* 基线一律取**同一 trial 内**的前后静止段，不用外部空场。
  外部空场会把采集间的状态差异计入动作响应
  （见 docs/experiments/20260811_baseline_review.md）。
* 导频列幅度恒为 0，必须先剔除（242 -> 234）。
* 相位分析用**天线间相位差**，不用绝对相位：AX210 每包存在随机
  CFO/STO，绝对相位不可用，天线差分可消掉共模项。
* 只输出统计量，不外传原始 CSI，不涉及影像。

分段
----
默认协议：前 9 s 静止 / 9-25 s 动作 / 尾部 10 s 静止。
为避开动作起止的过渡，两端各留 --guard 秒保护带。

用法
----
    python3 subcarrier_probe.py <csi_data目录> [--json out.json]
"""

from __future__ import annotations

import argparse
import glob
import json
import os

import numpy as np

PILOT = [6, 32, 74, 100, 141, 167, 209, 235]
KEEP = np.array([i for i in range(242) if i not in PILOT])


def load(npz_path):
    data = np.load(npz_path)
    key = "target_csi" if "target_csi" in data.files else data.files[0]
    csi = data[key]
    if csi.ndim == 4:
        csi = csi[:, :, :, 0]
    return csi[:, KEEP, :]


def split(n, rate, act0, act1, guard):
    """返回 (静止掩码, 动作掩码)，两端留保护带避开过渡。"""
    t = np.arange(n) / rate
    action = (t >= act0 + guard) & (t <= act1 - guard)
    idle = (t <= act0 - guard) | (t >= act1 + guard)
    return idle, action


def ratio(series, idle, action, use_std=False):
    a, b = series[action], series[idle]
    if a.size < 10 or b.size < 10:
        return float("nan")
    va, vb = (a.std(), b.std()) if use_std else (a.mean(), b.mean())
    return float(va / vb) if vb else float("nan")


def features(csi, idle, action):
    amp = np.abs(csi).astype(np.float64)
    out = {}

    for ant in range(amp.shape[2]):
        a = amp[:, :, ant]
        m = a.mean(axis=0)
        ok = m > 0
        an = a[:, ok] / m[ok]
        d = np.abs(np.diff(an, axis=0))

        # 现有特征：跨子载波平均的变化能量（作对照）
        out[f"a{ant}_chg_mean"] = ratio(d.mean(axis=1), idle[1:], action[1:])
        # 只看最活跃的 10% 子载波——小动作可能只影响少数子载波
        per_sc = d.mean(axis=0)
        top = np.argsort(per_sc)[-max(1, per_sc.size // 10):]
        out[f"a{ant}_chg_top10"] = ratio(d[:, top].mean(axis=1), idle[1:], action[1:])
        # 频率选择性强度：同一时刻子载波间的方差
        out[f"a{ant}_freq_var"] = ratio(an.var(axis=1), idle, action)
        # 全带平均幅度的波动幅度
        out[f"a{ant}_amp_std"] = ratio(an.mean(axis=1), idle, action, use_std=True)

    # 天线间相位差：消掉每包随机 CFO/STO 后仍保留空间信息
    dphi = np.angle(csi[:, :, 0] * np.conj(csi[:, :, 1]))
    dphi = np.unwrap(dphi, axis=1)
    out["antphase_chg"] = ratio(
        np.abs(np.diff(dphi, axis=0)).mean(axis=1), idle[1:], action[1:]
    )
    out["antphase_std"] = ratio(dphi.std(axis=1), idle, action)

    # 天线幅度比：对共模增益变化不敏感
    r = amp[:, :, 0].mean(axis=1) / np.maximum(amp[:, :, 1].mean(axis=1), 1e-9)
    out["antratio_std"] = ratio(r, idle, action, use_std=True)
    return out


COLUMNS = [
    ("a0_chg_mean", "chg_mean"),
    ("a0_chg_top10", "chg_top10"),
    ("a0_freq_var", "freq_var"),
    ("a0_amp_std", "amp_std"),
    ("antphase_chg", "phase_chg"),
    ("antphase_std", "phase_std"),
    ("antratio_std", "ant_ratio"),
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("root")
    ap.add_argument("--act-start", type=float, default=9.0)
    ap.add_argument("--act-end", type=float, default=25.0)
    ap.add_argument("--guard", type=float, default=1.0)
    ap.add_argument("--rate", type=float, default=200.0)
    ap.add_argument("--json", default="")
    a = ap.parse_args()

    dirs = sorted(
        d for d in glob.glob(os.path.join(a.root, "20260811_*"))
        if os.path.isdir(d) and not d.endswith("_cam")
    )

    print(f"分段: 静止 <{a.act_start - a.guard:.0f}s 与 >{a.act_end + a.guard:.0f}s，"
          f"动作 {a.act_start + a.guard:.0f}-{a.act_end - a.guard:.0f}s")
    print()
    print(f"{'trial':32s} " + " ".join(f"{lbl:>10s}" for _, lbl in COLUMNS))

    results = {}
    for d in dirs:
        npz = glob.glob(os.path.join(d, "*_target_csi.npz"))
        if not npz:
            continue
        name = os.path.basename(d)[16:]
        try:
            csi = load(npz[0])
        except Exception as exc:  # noqa: BLE001
            print(f"{name:32s} 读取失败: {exc}")
            continue
        idle, action = split(csi.shape[0], a.rate, a.act_start, a.act_end, a.guard)
        if action.sum() < 200 or idle.sum() < 200:
            print(f"{name:32s} 段长不足，跳过")
            continue
        f = features(csi, idle, action)
        results[name] = f
        print(f"{name[:32]:32s} " + " ".join(f"{f[k]:10.2f}" for k, _ in COLUMNS))

    if a.json:
        with open(a.json, "w") as fh:
            json.dump(results, fh, indent=2)
        print(f"\n已写出 {a.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
