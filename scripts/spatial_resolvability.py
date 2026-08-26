#!/usr/bin/env python3
"""空间分辨能力判据：系统能否区分人站在 A/B/C 三个位置。

背景
----
四轮特征工程全部完成，位置回归全部未通过
（幅度 +9.8% / 频谱 +13.5% / 相位 +14.2% / 组合 +15.2% / 多普勒 −0.5%），
X 残差 ≥ 0.53 m，大于人体自身宽度。

待检验假设：

    H：本配置测量的是「场景中有多少变化」，而非「变化发生在哪里」。

若 H 成立，改协议、加样本都没用，必须改物理配置。

本脚本用静止站位数据判定 H。静止使「位置」成为唯一变量，
规避了协议周期性陷阱（8-20 实测：仅时间正弦基即达 21.5% 改善）。

为什么需要阳性对照
------------------
主判据的类间距离**无法先验估计**：
已知人体级散射体造成 17–27% 频谱变化（金属板 27.27%、真人静站 20.65%），
但那是「有人 vs 无人」；本实验比较的是三个都有人的场景，
共同的「有人」成分会被抵消，剩下的纯位置差异有多大是未知的
——这正是实验要回答的问题。

因此必须先验证**实验有能力测出已知量**：

    金属板 vs 空场   应落在 17–27%（8-20 实测 27.27%，信噪比 127x）
    站位   vs 空场   应落在 17–27%

若连已知量都测不出，阴性结果说明不了任何问题。
**这是 8-20 置换检验教训的直接应用：阴性结论必须先证明实验有功效。**

判据
----
主判据：
    分离度 = 类间平均 L2 / 类内平均 L2

    > 2.5   有空间分辨能力  -> H 被推翻，走路线 A（改协议重采）
    1.5-2.5 边缘            -> 每位置扩到 10 次重复再判
    < 1.5   无空间分辨能力  -> H 成立，走路线 B（改物理配置）

辅助判据（按证据强度排序）：
    1. 空间单调性  d(A,B), d(B,C) < d(A,C)   ← 最强，随机差异无空间顺序
    2. node3 对照  node3 分离度应显著低于 node1
    3. 留一分类    3 类 15 样本，> 60%（随机基线 33%）
    4. 空场稳定性  6 条空场间 L2 < 1%

零分布：
    6 条空场随机分 3 组算「伪分离度」，重复 200 次。
    **不使用置换检验**——8-20 已证其在 CSI 上失效
    （随机置换破坏时间自相关，空场数据也 p=0.0000）。

用法
----
    python3 scripts/spatial_resolvability.py \\
        --results ~/csi_results --date 20260825
"""
import argparse
import glob
import itertools
import os
import re
import sys

import numpy as np

PILOT_IDX = [6, 32, 74, 100, 141, 167, 209, 235]
POSITIONS = ["A", "B", "C"]
# 已知效应量区间（8-20 实测：金属板 27.27%/26.86%，纸箱 17-18%）
KNOWN_LO, KNOWN_HI = 17.0, 30.0

if np.__config__.CONFIG.get("Build Dependencies", {}).get(
        "blas", {}).get("name") == "accelerate":
    np.seterr(divide="ignore", over="ignore", invalid="ignore")


def load_spectrum(trial_dir, node):
    """归一化频谱（时间平均后按天线归一，消除 AGC 宽带增益）。

    返回展平的 (n_sub * n_ant,) 向量。
    """
    p = os.path.join(trial_dir, "aligned", "aligned_csi.npz")
    if os.path.exists(p):
        c = np.load(p)[f"{node}_csi_data"]
    else:
        hits = glob.glob(os.path.join(trial_dir, f"{node}_*_target_csi.npz"))
        if not hits:
            raise FileNotFoundError(f"{trial_dir} 无 {node} 数据")
        c = np.load(hits[0])["target_csi"]
        if c.shape[1] == 242:
            m = np.ones(242, dtype=bool)
            m[PILOT_IDX] = False
            c = c[:, m]
    a = np.abs(c[:, :, :, 0]).astype(np.float64)
    s = a.mean(axis=0)
    return (s / (s.mean(axis=0, keepdims=True) + 1e-12)).ravel()


def rel_l2(a, b):
    """相对 L2 距离（%）。"""
    return float(np.linalg.norm(a - b) / (np.linalg.norm((a + b) / 2) + 1e-12) * 100)


def collect(results, date):
    """扫描结果目录，按类别归集 trial。"""
    pat = os.path.join(os.path.expanduser(results), f"{date}_*")
    out = {"empty": [], "plate": [], "cal_empty": []}
    for p in POSITIONS:
        out[p] = []
    for d in sorted(glob.glob(pat)):
        b = os.path.basename(d)
        if "g_cal_plate" in b:
            out["plate"].append(d)
        elif "g_cal_empty" in b:
            out["cal_empty"].append(d)
        elif "g_empty" in b:
            out["empty"].append(d)
        else:
            m = re.search(r"g_stand_([ABC])_r\d+", b)
            if m:
                out[m.group(1)].append(d)
    return out


def pairwise(vs):
    return [rel_l2(a, b) for a, b in itertools.combinations(vs, 2)]


def cross(v1, v2):
    return [rel_l2(a, b) for a in v1 for b in v2]


def analyze_node(specs, node, rng, n_null=200):
    """返回该节点的完整判据结果。"""
    R = {}
    emp = specs["empty"]
    pos = {p: specs[p] for p in POSITIONS}

    # ---- 阳性对照 ----
    R["pos_ctrl"] = {}
    if specs["plate"] and specs["cal_empty"]:
        R["pos_ctrl"]["plate_vs_empty"] = float(
            np.mean(cross(specs["plate"], specs["cal_empty"])))
    if emp:
        for p in POSITIONS:
            if pos[p]:
                R["pos_ctrl"][f"stand{p}_vs_empty"] = float(
                    np.mean(cross(pos[p], emp)))

    # ---- 空场稳定性 ----
    R["empty_within"] = float(np.mean(pairwise(emp))) if len(emp) > 1 else np.nan

    # ---- 主判据 ----
    within = []
    for p in POSITIONS:
        if len(pos[p]) > 1:
            within += pairwise(pos[p])
    between = []
    for p, q in itertools.combinations(POSITIONS, 2):
        if pos[p] and pos[q]:
            between += cross(pos[p], pos[q])
    R["within"] = float(np.mean(within)) if within else np.nan
    R["between"] = float(np.mean(between)) if between else np.nan
    R["sep"] = R["between"] / R["within"] if within and R["within"] > 0 else np.nan

    # ---- 辅助 1：空间单调性 ----
    d = {}
    for p, q in itertools.combinations(POSITIONS, 2):
        if pos[p] and pos[q]:
            d[f"{p}{q}"] = float(np.mean(cross(pos[p], pos[q])))
    R["pair_dist"] = d
    R["monotonic"] = (
        "AB" in d and "BC" in d and "AC" in d
        and d["AB"] < d["AC"] and d["BC"] < d["AC"]
    )

    # ---- 辅助 3：留一分类（最近类均值）----
    X, y = [], []
    for p in POSITIONS:
        for v in pos[p]:
            X.append(v)
            y.append(p)
    if len(X) >= 6:
        correct = 0
        for i in range(len(X)):
            cent = {}
            for p in POSITIONS:
                others = [X[j] for j in range(len(X)) if y[j] == p and j != i]
                if others:
                    cent[p] = np.mean(others, axis=0)
            if cent:
                pred = min(cent, key=lambda p: rel_l2(X[i], cent[p]))
                correct += (pred == y[i])
        R["loo_acc"] = correct / len(X) * 100
    else:
        R["loo_acc"] = np.nan

    # ---- 零分布：空场随机分 3 组 ----
    if len(emp) >= 6:
        null = []
        idx = np.arange(len(emp))
        for _ in range(n_null):
            rng.shuffle(idx)
            g = [[emp[i] for i in idx[k::3]] for k in range(3)]
            w, bt = [], []
            for grp in g:
                if len(grp) > 1:
                    w += pairwise(grp)
            for a, b in itertools.combinations(range(3), 2):
                if g[a] and g[b]:
                    bt += cross(g[a], g[b])
            if w and bt and np.mean(w) > 0:
                null.append(np.mean(bt) / np.mean(w))
        R["null"] = np.array(null)
    else:
        R["null"] = np.array([])
    return R


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--results", default="~/csi_results")
    ap.add_argument("--date", required=True, help="如 20260825")
    ap.add_argument("--nodes", default="node1,node3")
    ap.add_argument("--seed", type=int, default=20260821)
    args = ap.parse_args()

    dirs = collect(args.results, args.date)
    n_stand = sum(len(dirs[p]) for p in POSITIONS)
    print("=" * 68)
    print("空间分辨能力判据实验")
    print("=" * 68)
    print(f"  空场 {len(dirs['empty'])}   "
          f"A/B/C {'/'.join(str(len(dirs[p])) for p in POSITIONS)}   "
          f"金属板 {len(dirs['plate'])}   校准空场 {len(dirs['cal_empty'])}")
    if n_stand < 6:
        sys.exit("站位 trial 不足（需 >= 6）")
    print()

    rng = np.random.default_rng(args.seed)
    results = {}
    for node in args.nodes.split(","):
        specs = {}
        try:
            for k, ds in dirs.items():
                specs[k] = [load_spectrum(d, node) for d in ds]
        except (FileNotFoundError, KeyError) as e:
            print(f"[{node}] 跳过：{e}")
            continue
        results[node] = analyze_node(specs, node, rng)

    main_node = args.nodes.split(",")[0]

    # ---------------- 阳性对照 ----------------
    print("=== 阳性对照（必须先通过）===")
    print(f"  已知效应量区间：{KNOWN_LO:.0f}–{KNOWN_HI:.0f}%")
    print("  依据：8-20 金属板 27.27%/26.86%（信噪比 127x）、纸箱 17-18%")
    print()
    ok_ctrl = True
    for node, R in results.items():
        for k, v in R["pos_ctrl"].items():
            flag = "OK" if KNOWN_LO <= v <= KNOWN_HI else (
                "偏低 ⚠" if v < KNOWN_LO else "偏高")
            if node == main_node and k == "plate_vs_empty" and v < KNOWN_LO:
                ok_ctrl = False
            print(f"  {node} {k:<22s} {v:7.2f}%   {flag}")
    print()
    if not ok_ctrl:
        print("  ✗ 金属板对照未达已知效应量 -> 采集或分析链路有问题")
        print("    此时三位置比较无意义，请排查后重采。")
        print()

    # ---------------- 空场稳定性 ----------------
    print("=== 辅助 4：空场稳定性（主判据可信的前提）===")
    for node, R in results.items():
        v = R["empty_within"]
        print(f"  {node} 空场间平均 L2 {v:7.3f}%   "
              f"{'OK' if v < 1.0 else ('边缘' if v < 2.0 else '过大 ⚠')}")
    print()

    # ---------------- 主判据 ----------------
    print("=== 主判据：分离度 = 类间 / 类内 ===")
    for node, R in results.items():
        print(f"  {node}: 类内 {R['within']:6.2f}%   类间 {R['between']:6.2f}%   "
              f"分离度 {R['sep']:5.2f}")
        if len(R["null"]):
            p = float(np.mean(R["null"] >= R["sep"]))
            print(f"          空场零分布 中位 {np.median(R['null']):.2f}  "
                  f"p95 {np.percentile(R['null'],95):.2f}   p = {p:.4f}")
    print()

    # ---------------- 辅助判据 ----------------
    print("=== 辅助 1：空间单调性（最强证据）===")
    print("  若存在空间编码，应有 d(A,B) 与 d(B,C) 均小于 d(A,C)")
    for node, R in results.items():
        d = R["pair_dist"]
        if len(d) == 3:
            print(f"  {node}: d(A,B)={d['AB']:6.2f}%  d(B,C)={d['BC']:6.2f}%  "
                  f"d(A,C)={d['AC']:6.2f}%   "
                  f"{'单调 ✓' if R['monotonic'] else '不单调 ✗'}")
    print()

    print("=== 辅助 2：node3 空间对照 ===")
    print("  node3 在链路外 3m，到 A/B/C 距离 3.09/3.00/3.09m（差异 3%）")
    print("  它应对三点近似无差别响应；若分离度与 node1 相当，")
    print("  说明测到的是全局场景变化而非位置。")
    if len(results) >= 2:
        ns = list(results)
        s0, s1 = results[ns[0]]["sep"], results[ns[1]]["sep"]
        print(f"  {ns[0]} {s0:.2f}  vs  {ns[1]} {s1:.2f}   "
              f"比值 {s0/s1:.2f}x" if s1 > 0 else "")
    print()

    print("=== 辅助 3：留一分类（随机基线 33.3%）===")
    for node, R in results.items():
        v = R["loo_acc"]
        print(f"  {node} 准确率 {v:5.1f}%   "
              f"{'支持可分' if v > 60 else '不支持'}")
    print()

    # ---------------- 结论 ----------------
    print("=" * 68)
    R = results.get(main_node)
    if R is None:
        sys.exit("主节点无结果")
    sep = R["sep"]
    print(f"主节点 {main_node} 分离度 = {sep:.2f}")
    print()
    if not ok_ctrl:
        print("结论：**实验失效** —— 阳性对照未通过，不下判断。")
        return 2
    if sep > 2.5:
        print("结论：系统**有**空间分辨能力（分离度 > 2.5）")
        print("  -> H 被推翻。此前失败源于协议周期性与样本量。")
        print("  -> 走路线 A：随机化走动节奏，每类 >= 8 条 trial，重新采集。")
    elif sep < 1.5:
        print("结论：系统**无**空间分辨能力（分离度 < 1.5）")
        print("  -> H 成立。特征工程与数据量都救不了。")
        print("  -> 走路线 B：改物理配置。按成本排序：")
        print("       拉大链路夹角至 90°（仅移动节点）")
        print("       带宽 20 -> 80 MHz（距离分辨率 7.5 -> 1.9 m）")
        print("       增加 Rx 节点（需新硬件）")
    else:
        print("结论：边缘（1.5 <= 分离度 <= 2.5）")
        print("  -> 每位置扩到 10 次重复再判；仍不明确则按'无分辨力'处理。")
    print()
    print("注：本实验不使用置换检验（8-20 已证其在 CSI 上失效），")
    print("    零分布由 6 条空场跑同一程序获得。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
