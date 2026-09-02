#!/usr/bin/env python3
"""配置消融：包率 / 子载波数 / 空间链路数对存在性检测可分性的影响。

零采集成本，全部在已采数据上后处理。回答论文「本系统的最低配置需求」：
需要多高的包率？需要多少子载波（即多大带宽）？第二个接收端值多少？

--------------------------------------------------------------------------
为什么完全在 trial 内部比较
--------------------------------------------------------------------------
跨 trial 的空场漂移实测 2-37%，远大于待测效应（HANDOFF §0.1）。因此本脚本
**不做任何跨 trial 比较**：每条 trial 自带前置空场、事件段、离场后空场三个
窗口，阳性与阴性都取自同一条 trial，且都相对该 trial 自己的前置空场计算。

  0-5 s    前置空场   → 作为基线参考
  8-21 s   事件段     → 阳性样本（人在标记点上）
  24-34 s  离场后空场 → 阴性样本（人已离开，2 s 沉降余量）

阴性对照：把离场后空场对半切，用后半段冒充阳性重算 AUC。管线无偏应得
AUC≈0.5；偏离多少即为窗口位置本身贡献的可分性，必须从报告值中扣除。

--------------------------------------------------------------------------
特征
--------------------------------------------------------------------------
  spectrum  AGC 归一化频谱形状相对基线的相对 L2 距离（%）
  motion    约 25 ms 滞后的归一化幅度变化能量

抽稀包率时 motion 的滞后按物理时间恒定折算（lag = round(5/stride)，下限 1），
否则「降包率」会混入「换了个时间尺度」的效应。

不使用 |H| 绝对幅度 —— AX210 的 AGC 补偿 +13.88 dB，绝对幅度不含遮挡信息
（HANDOFF §0.01）。

用法:
    ~/csienv/bin/python scripts/config_ablation.py --results ~/csi_results \
        --session 20260901 --out docs/experiments/ablation
"""
from __future__ import annotations

import argparse
import csv
import glob
import json
import os
import re
import sys

import numpy as np

BASE_END, EV_START, EV_END, POST_START, POST_END = 5.0, 8.0, 21.0, 24.0, 34.0
MIN_PKTS_PER_SEC = 20          # 5 pkt/s 档时每秒仅约 5 包，见 min_frac
NODES = ("node1", "node3")

STRIDES     = (1, 2, 4, 10, 20, 40)
SUBCARRIERS = (234, 117, 58, 29, 14)
CHAINS      = (("node1", 1, 1), ("node1", 2, 2),
               ("both",  1, 2), ("both",  2, 4))


# ------------------------------------------------------------------ 特征
def normalized_spectrum(csi: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """每根天线除以自身频带均值，消去 AGC 整体增益，只保留频谱形状。"""
    a = np.abs(csi[mask]).astype(np.float64)
    s = np.nanmean(a, axis=0)                       # (subcarrier, antenna)
    return (s / (np.nanmean(s, axis=0, keepdims=True) + 1e-12)).ravel()


def rel_l2(a: np.ndarray, b: np.ndarray) -> float:
    den = np.linalg.norm((a + b) / 2) + 1e-12
    return float(np.linalg.norm(a - b) / den * 100)


def packet_motion(csi: np.ndarray, lag: int):
    a = np.abs(csi).astype(np.float64)
    a /= np.nanmean(a, axis=1, keepdims=True) + 1e-12
    d = a[lag:] - a[:-lag]
    return np.arange(lag, len(a)), np.sqrt(np.nanmean(d * d, axis=(1, 2)))


def auc(pos, neg) -> float:
    """Mann-Whitney U，含并列值折半。"""
    pos = np.asarray([v for v in pos if np.isfinite(v)], dtype=float)
    neg = np.asarray([v for v in neg if np.isfinite(v)], dtype=float)
    if len(pos) < 3 or len(neg) < 3:
        return float("nan")
    allv = np.concatenate([pos, neg])
    order = np.argsort(allv, kind="mergesort")
    ranks = np.empty(len(allv), dtype=float)
    sv = allv[order]
    i = 0
    while i < len(sv):
        j = i
        while j + 1 < len(sv) and sv[j + 1] == sv[i]:
            j += 1
        ranks[order[i:j + 1]] = (i + j) / 2.0 + 1.0
        i = j + 1
    rp = ranks[:len(pos)].sum()
    return float((rp - len(pos) * (len(pos) + 1) / 2.0) / (len(pos) * len(neg)))


# ------------------------------------------------------------------ 加载
def load(path: str):
    p = os.path.join(path, "aligned", "aligned_csi.npz")
    if not os.path.isfile(p):
        return None
    with np.load(p, allow_pickle=False) as z:
        if "node1_csi_data" not in z.files or "node3_csi_data" not in z.files:
            return None
        csi = {n: z[f"{n}_csi_data"][:, :, :, 0] for n in NODES}   # (pkt, sub, ant)
        ns = z["node1_system_ns"].astype(np.int64).reshape(-1)
    n = min(len(ns), *(len(csi[k]) for k in NODES))
    t = (ns[:n] - ns[0]) / 1e9
    return {n_: csi[n_][:n] for n_ in NODES}, t


def score(csi_sel: np.ndarray, t: np.ndarray, lag: int, min_frac: float):
    """返回 (每秒 spectrum 列表, 每秒 motion 列表)，均相对本 trial 前置空场。"""
    base = t < BASE_END
    if base.sum() < 3:
        return None
    base_spec = normalized_spectrum(csi_sel, base)
    mi, me = packet_motion(csi_sel, lag)
    mt = t[mi]
    need = max(2, int(MIN_PKTS_PER_SEC * min_frac))
    rows = {}
    for sec in range(0, int(t[-1])):
        m = (t >= sec) & (t < sec + 1)
        mm = (mt >= sec) & (mt < sec + 1)
        if m.sum() < need:
            continue
        rows[sec] = (rel_l2(normalized_spectrum(csi_sel, m), base_spec),
                     float(np.nanmedian(me[mm])) if mm.sum() >= 2 else np.nan)
    return rows


def bucket(rows: dict):
    """按窗口归类：阳性=事件段，阴性=离场后；阴性对照=离场后对半切。"""
    pos, neg, ctl_p, ctl_n = [], [], [], []
    mid = (POST_START + POST_END) / 2
    for sec, v in rows.items():
        if EV_START <= sec < EV_END:
            pos.append(v)
        elif POST_START <= sec < POST_END:
            neg.append(v)
            (ctl_p if sec >= mid else ctl_n).append(v)
    return pos, neg, ctl_p, ctl_n


# ------------------------------------------------------------------ 主流程
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", default=os.path.expanduser("~/csi_results"))
    ap.add_argument("--session", default="20260901")
    ap.add_argument("--out", default="docs/experiments/ablation")
    args = ap.parse_args()

    dirs = sorted(d for d in glob.glob(os.path.join(args.results, f"{args.session}_*"))
                  if re.search(r"_(static|motion)_r\d+$", d)
                  and "INVALID" not in d)
    if not dirs:
        print("没有匹配的人体 trial", file=sys.stderr)
        return 1
    print(f"人体 trial {len(dirs)} 条")

    os.makedirs(args.out, exist_ok=True)
    results = []

    loaded = []
    for d in dirs:
        r = load(d)
        if r is None:
            print(f"  跳过（缺 aligned）: {os.path.basename(d)}")
            continue
        loaded.append((os.path.basename(d), r[0], r[1]))
    print(f"成功加载 {len(loaded)} 条\n")

    def run(tag, params, sel_fn, lag, min_frac):
        acc = {k: [] for k in ("pos_s", "neg_s", "cp_s", "cn_s",
                               "pos_m", "neg_m", "cp_m", "cn_m")}
        for name, csi, t in loaded:
            sel, tt = sel_fn(csi, t)
            rows = score(sel, tt, lag, min_frac)
            if not rows:
                continue
            p, n, cp, cn = bucket(rows)
            acc["pos_s"] += [x[0] for x in p]; acc["neg_s"] += [x[0] for x in n]
            acc["cp_s"]  += [x[0] for x in cp]; acc["cn_s"] += [x[0] for x in cn]
            acc["pos_m"] += [x[1] for x in p]; acc["neg_m"] += [x[1] for x in n]
            acc["cp_m"]  += [x[1] for x in cp]; acc["cn_m"] += [x[1] for x in cn]
        row = dict(ablation=tag, **params,
                   auc_spectrum=auc(acc["pos_s"], acc["neg_s"]),
                   ctl_spectrum=auc(acc["cp_s"], acc["cn_s"]),
                   auc_motion=auc(acc["pos_m"], acc["neg_m"]),
                   ctl_motion=auc(acc["cp_m"], acc["cn_m"]),
                   n_pos=len(acc["pos_s"]), n_neg=len(acc["neg_s"]))
        results.append(row)
        print(f"  {tag:12s} {str(params):34s} "
              f"spec AUC {row['auc_spectrum']:.3f} (对照 {row['ctl_spectrum']:.3f})  "
              f"motion AUC {row['auc_motion']:.3f} (对照 {row['ctl_motion']:.3f})")

    both = lambda c: np.concatenate([c["node1"], c["node3"]], axis=2)

    print("=== 消融 1：包率 ===")
    for k in STRIDES:
        run("packet_rate", {"pkt_per_s": round(196.2 / k, 1), "stride": k},
            lambda c, t, k=k: (both(c)[::k], t[::k]),
            max(1, int(round(5 / k))), 1.0 / k)

    print("\n=== 消融 2：子载波数 ===")
    for n in SUBCARRIERS:
        idx = np.linspace(0, 233, n).astype(int)
        run("subcarriers", {"n_sub": n, "bw_equiv_mhz": round(20.0 * n / 234, 1)},
            lambda c, t, idx=idx: (both(c)[:, idx], t), 5, 1.0)

    print("\n=== 消融 3：空间链路数 ===")
    for nodes, ants, chains in CHAINS:
        def sel(c, t, nodes=nodes, ants=ants):
            x = both(c) if nodes == "both" else c["node1"]
            return (x if ants == 2 else x[:, :, ::2]), t
        run("chains", {"nodes": nodes, "ant_per_node": ants, "n_chains": chains},
            sel, 5, 1.0)

    csv_path = os.path.join(args.out, f"{args.session}_config_ablation.csv")
    keys = sorted({k for r in results for k in r})
    with open(csv_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["ablation"] + [k for k in keys if k != "ablation"])
        w.writeheader()
        w.writerows(results)
    print(f"\n已写出 {csv_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
