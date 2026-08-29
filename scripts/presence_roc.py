#!/usr/bin/env python3
"""存在性检测的 ROC / AUC —— 零采集成本，只用已有数据。

产出 PLAN_B §5.2 要求的量化结果：不只给「空场 2.7% vs 有人 34-37%」这样的点
估计，而是给出完整 ROC 曲线与 AUC。

--------------------------------------------------------------------------
为什么每条 trial 只跟自己比
--------------------------------------------------------------------------
跨 trial 的空场漂移实测 2-37%（20260826 §5），远大于待测效应，且 8-24/8-26
两次都没通过双空场门控。因此本脚本**绝不跨 trial 比较绝对量**：每条 trial 的
特征一律相对**它自己的前 BASELINE_SEC 秒**计算。这样漂移只要在单条 trial
内部足够小就不影响判决，而 20260826 §7 实测稳定期 trial 内可达 1.85%。

--------------------------------------------------------------------------
阴性对照（本脚本最重要的输出）
--------------------------------------------------------------------------
把空场 trial 按 trial 边界分成两组，其中一组假装成「阳性」，重算 AUC。
若管线无偏，应得 AUC ≈ 0.5；偏离多少，就说明基线漂移本身贡献了多少可分性。

这是 8-20 教训（阴性结论必须先证明实验有功效）和 8-26 教训（阳性对照拦下
无效采集）的直接应用。**真实 AUC 减去阴性对照 AUC，才是可报告的净可分性。**

--------------------------------------------------------------------------
必须按会话分组
--------------------------------------------------------------------------
跨会话的 CSI 不可直接比较：导频填充值、时钟偏差、AGC 工作点、布局都会变
（HANDOFF §0.1、§5.4）。若把不同日期的 trial 混在一起，ROC 会把「哪一天采的」
当成信号——实测混合 8-06 与 8-14 时 motion 的阴性对照 AUC 高达 0.995，
即空场之间比空场与有人之间还好分。因此本脚本默认按日期分组，逐会话独立评估，
每组至少需要 2 条阳性与 2 条阴性。

--------------------------------------------------------------------------
特征（与 scripts/distance_sensitivity.py 保持一致，便于横向比较）
--------------------------------------------------------------------------
  spectrum  AGC 归一化频谱形状相对基线的 L2 距离（%）
  motion    约 25 ms 滞后的归一化幅度变化能量，抗 AGC
  rssi_abs  RSSI 相对基线中位数的绝对变化（dB）

**不使用 |H| 绝对幅度**——HANDOFF §0.01 明确禁止：AX210 的 AGC 会补偿
+13.88 dB，绝对幅度不含遮挡信息，8-06 的错误结论正源于此。

--------------------------------------------------------------------------
防泄漏
--------------------------------------------------------------------------
1. 连续窗口按 **trial 分组**，绝不随机拆分（CSI 时序高度自相关）。
2. trial 级判决的阈值来自**其他空场 trial**的第 95 百分位，
   留一法，测试 trial 自己不参与定阈。
3. 不使用置换检验——20260820 实测空场也能得 p=0.0000。

用法:
    ~/csienv/bin/python scripts/presence_roc.py --results ~/csi_results
    ~/csienv/bin/python scripts/presence_roc.py --category plate --node node3
"""
from __future__ import annotations

import argparse
import csv
import glob
import os
import re
import sys

import numpy as np

SCORES = ("spectrum", "motion", "rssi_abs")

# trial 名片段 -> (标签, 类别)。标签 present=阳性, empty=阴性。
# 类别用于 --category 过滤：human 是最干净的一组，plate/foil 是物理对照。
LABELS: list[tuple[str, str, str]] = [
    # --- 阴性：空场 ---
    (r"_empty_05m_0\d$",            "empty",   "human"),
    (r"demo_empty_",                "empty",   "human"),
    (r"_nobox_1m_",                 "empty",   "box"),
    (r"_m[A]\d_base",               "empty",   "plate"),
    (r"_g_cal_empty_\d+$",          "empty",   "plate"),
    (r"_g_bend_empty_\d+$",         "empty",   "bend"),
    (r"_g_baseline_probe_",         "empty",   "probe"),
    (r"_g_warmup_probe_",           "empty",   "probe"),
    # --- 阳性：有人 / 有散射体 ---
    (r"_walk_link1_05m_0\d$",       "present", "human"),
    (r"demo_link1_cross",           "present", "human"),
    (r"_box_1m_state$",             "present", "box"),
    (r"_metal_A1_nobase$",          "present", "plate"),
    (r"_m[B]\d_foil_ant$",          "present", "foil"),
    (r"_m[C]\d_plate_mid$",         "present", "plate"),
    (r"_g_cal_plate_B",             "present", "plate"),
    (r"_g_cal_plate_at_rx$",        "present", "foil"),
    (r"_g_bend_plate_B$",           "present", "bend"),
]

# HANDOFF 标注为协议缺陷或诊断用途，默认排除；用 --keep-flagged 保留。
FLAGGED = {
    "block_test":   "8-06 未按设计执行：人全程站原地，无离开对照段（HANDOFF §0.02）",
    "ant_check":    "8-06 调整天线朝向的诊断 trial，非受控采集（HANDOFF §0.02）",
    "walk_check":   "预检 trial",
    "layout_check": "预检 trial",
    "cam_pretest":  "相机预检",
    "cam_recheck":  "相机预检",
    "h265_fix":     "相机编码修复验证",
}


# ----------------------------------------------------------------- 特征
def clean_rssi(x: np.ndarray) -> np.ndarray:
    x = np.asarray(x, dtype=float).reshape(-1)
    x[(x >= 0) | (x < -110)] = np.nan
    return x


def normalized_spectrum(csi: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """AGC 归一化的频谱形状：每根天线除以自身均值，消去整体增益。"""
    a = np.abs(csi[mask, :, :, 0]).astype(np.float64)
    s = np.nanmean(a, axis=0)
    return (s / (np.nanmean(s, axis=0, keepdims=True) + 1e-12)).ravel()


def rel_l2(a: np.ndarray, b: np.ndarray) -> float:
    den = np.linalg.norm((a + b) / 2) + 1e-12
    return float(np.linalg.norm(a - b) / den * 100)


def packet_motion(csi: np.ndarray, lag: int = 5):
    """约 25 ms 滞后的归一化幅度变化能量，抗 AGC。"""
    a = np.abs(csi[:, :, :, 0]).astype(np.float64)
    a /= np.nanmean(a, axis=1, keepdims=True) + 1e-12
    delta = a[lag:] - a[:-lag]
    return np.arange(lag, len(a)), np.sqrt(np.nanmean(delta * delta, axis=(1, 2)))


# ----------------------------------------------------------------- 加载
def classify(trial: str):
    for pat, label, cat in LABELS:
        if re.search(pat, trial):
            return label, cat
    return None, None


def load_trial(path: str, node: str, baseline_sec: float, skip_sec: float,
               min_pkts: int):
    p = os.path.join(path, "aligned", "aligned_csi.npz")
    if not os.path.isfile(p):
        raise FileNotFoundError("缺少 aligned_csi.npz")
    with np.load(p, allow_pickle=False) as z:
        csi = z[f"{node}_csi_data"]
        ns = z[f"{node}_system_ns"].astype(np.int64).reshape(-1)
        # 8-06 及更早的 aligned_csi.npz 没有 RSSI 键（csi_align.py 后来才加），
        # 因此 RSSI 是可选特征，缺失时该 trial 只贡献 spectrum 与 motion。
        rssi = clean_rssi(z[f"{node}_rssi"]) if f"{node}_rssi" in z.files else None
    n = min(len(csi), len(ns)) if rssi is None else min(len(csi), len(ns), len(rssi))
    csi, ns = csi[:n], ns[:n]
    if rssi is not None:
        rssi = rssi[:n]
    t = (ns - ns[0]) / 1e9

    base = t < baseline_sec
    if base.sum() < min_pkts:
        raise ValueError(f"基线窗包数不足 ({int(base.sum())} < {min_pkts})")

    base_rssi = float(np.nanmedian(rssi[base])) if rssi is not None else None
    base_spec = normalized_spectrum(csi, base)
    mot_idx, mot_energy = packet_motion(csi)
    mot_t = t[mot_idx]

    rows = []
    start = int(np.ceil(baseline_sec + skip_sec))
    for sec in range(start, int(t[-1])):
        m = (t >= sec) & (t < sec + 1)
        mm = (mot_t >= sec) & (mot_t < sec + 1)
        if m.sum() < min_pkts // 4 or mm.sum() < min_pkts // 5:
            continue
        rows.append({
            "second":   sec,
            "spectrum": rel_l2(normalized_spectrum(csi, m), base_spec),
            "motion":   float(np.nanmedian(mot_energy[mm])),
            "rssi_abs": (abs(float(np.nanmedian(rssi[m])) - base_rssi)
                         if rssi is not None else float("nan")),
        })
    if not rows:
        raise ValueError("基线窗之后没有可用的评分窗口")
    return rows, rssi is not None


# ----------------------------------------------------------------- 统计
def auc(pos, neg) -> float:
    """Mann-Whitney U。并列计 0.5，与 ROC 曲线下面积一致。"""
    if not pos or not neg:
        return float("nan")
    allv = np.concatenate([np.asarray(pos, float), np.asarray(neg, float)])
    order = np.argsort(allv, kind="mergesort")
    ranks = np.empty(len(allv), float)
    ranks[order] = np.arange(1, len(allv) + 1)
    # 并列取平均秩
    s = allv[order]
    i = 0
    while i < len(s):
        j = i
        while j + 1 < len(s) and s[j + 1] == s[i]:
            j += 1
        if j > i:
            ranks[order[i:j + 1]] = np.mean(ranks[order[i:j + 1]])
        i = j + 1
    r_pos = ranks[:len(pos)].sum()
    return float((r_pos - len(pos) * (len(pos) + 1) / 2) / (len(pos) * len(neg)))


def roc_points(pos, neg):
    """返回 [(fpr, tpr, threshold)]，阈值从高到低。"""
    scored = sorted([(v, 1) for v in pos] + [(v, 0) for v in neg],
                    key=lambda x: -x[0])
    P, N = len(pos), len(neg)
    pts, tp, fp, prev = [(0.0, 0.0, float("inf"))], 0, 0, None
    for v, lab in scored:
        if prev is not None and v != prev:
            pts.append((fp / N, tp / P, prev))
        tp += lab
        fp += 1 - lab
        prev = v
    pts.append((fp / N, tp / P, prev if prev is not None else 0.0))
    return pts


def youden(pts):
    """最大 Youden J = TPR - FPR 的工作点。"""
    best = max(pts, key=lambda p: p[1] - p[0])
    return {"fpr": best[0], "tpr": best[1], "threshold": best[2],
            "j": best[1] - best[0]}


# ----------------------------------------------------------------- SVG
def write_svg(path, curves, title):
    W, H, M = 460, 400, 56
    pw, ph = W - M - 18, H - M - 42
    col = ["#2563eb", "#d97706", "#059669", "#9333ea"]

    def X(f): return M + f * pw
    def Y(t): return M + ph - t * ph

    o = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
         f'viewBox="0 0 {W} {H}" font-family="system-ui,sans-serif">',
         f'<rect width="{W}" height="{H}" fill="#ffffff"/>',
         f'<text x="{W/2}" y="24" text-anchor="middle" font-size="14" '
         f'font-weight="600" fill="#111">{title}</text>']
    for k in range(5):
        v = k / 4
        o.append(f'<line x1="{X(v):.1f}" y1="{Y(0):.1f}" x2="{X(v):.1f}" '
                 f'y2="{Y(1):.1f}" stroke="#e5e7eb" stroke-width="1"/>')
        o.append(f'<line x1="{X(0):.1f}" y1="{Y(v):.1f}" x2="{X(1):.1f}" '
                 f'y2="{Y(v):.1f}" stroke="#e5e7eb" stroke-width="1"/>')
        o.append(f'<text x="{X(v):.1f}" y="{Y(0)+16:.1f}" text-anchor="middle" '
                 f'font-size="10" fill="#6b7280">{v:.2f}</text>')
        o.append(f'<text x="{M-8}" y="{Y(v)+4:.1f}" text-anchor="end" '
                 f'font-size="10" fill="#6b7280">{v:.2f}</text>')
    o.append(f'<line x1="{X(0):.1f}" y1="{Y(0):.1f}" x2="{X(1):.1f}" '
             f'y2="{Y(1):.1f}" stroke="#9ca3af" stroke-width="1" '
             f'stroke-dasharray="4 3"/>')
    o.append(f'<rect x="{M}" y="{M}" width="{pw}" height="{ph}" fill="none" '
             f'stroke="#374151" stroke-width="1"/>')
    for i, (name, pts, a) in enumerate(curves):
        d = " ".join(f"{'M' if k == 0 else 'L'}{X(f):.1f},{Y(t):.1f}"
                     for k, (f, t, _) in enumerate(pts))
        c = col[i % len(col)]
        o.append(f'<path d="{d}" fill="none" stroke="{c}" stroke-width="2"/>')
        o.append(f'<rect x="{M+10}" y="{M+10+i*17}" width="11" height="3" '
                 f'fill="{c}"/>')
        o.append(f'<text x="{M+26}" y="{M+16+i*17}" font-size="11" '
                 f'fill="#111">{name}  AUC={a:.3f}</text>')
    o.append(f'<text x="{M+pw/2:.1f}" y="{H-8}" text-anchor="middle" '
             f'font-size="11" fill="#374151">假阳性率 FPR</text>')
    o.append(f'<text x="14" y="{M+ph/2:.1f}" text-anchor="middle" '
             f'font-size="11" fill="#374151" '
             f'transform="rotate(-90 14 {M+ph/2:.1f})">真阳性率 TPR</text>')
    o.append("</svg>")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(o))


# ----------------------------------------------------------------- 单会话评估
def analyze(trials, tag, out_dir, node, category):
    """对一个会话内的 trial 集合做完整评估。返回 (summary, curves) 或 None。"""
    pos_tr = [t for t in trials if t["label"] == "present"]
    neg_tr = [t for t in trials if t["label"] == "empty"]

    print("=" * 74)
    print(f"会话 {tag}：阳性 {len(pos_tr)} 条 / 阴性 {len(neg_tr)} 条")
    print("=" * 74)
    for t in pos_tr + neg_tr:
        mark = "+" if t["label"] == "present" else "-"
        note = "" if t["has_rssi"] else "  (无 RSSI)"
        print(f"    {mark} {t['trial']:52s} {len(t['rows']):3d} 窗{note}")
    if len(pos_tr) < 2 or len(neg_tr) < 2:
        print("    ★ 阳性或阴性不足 2 条，跳过（无法做留一法与阴性对照）\n")
        return None
    print()

    # 逐 trial 分布：污染的空场会在这里直接现形。
    # 8-06 的 empty_05m_03 即为实例：中位数 25.1%，是同会话 walk 的两倍，
    # 而当时基于「深度无移动物体 + 幅度 std」的门控没能拦下它。
    print("逐 trial 分布（spectrum %，用于识别被污染的空场）")
    print(f"    {'trial':<52}{'中位数':>8}{'p95':>8}{'最大':>8}")
    for t in pos_tr + neg_tr:
        v = np.array([r["spectrum"] for r in t["rows"]])
        mark = "+" if t["label"] == "present" else "-"
        warn = ""
        if t["label"] == "empty":
            pos_med = np.median([r["spectrum"] for o in pos_tr for r in o["rows"]])
            if np.median(v) > 0.5 * pos_med:
                warn = "  ★ 空场中位数超过阳性中位数的一半，疑似污染"
        print(f"  {mark} {t['trial']:<52}{np.median(v):8.2f}"
              f"{np.percentile(v, 95):8.2f}{v.max():8.2f}{warn}")
    print()

    curves, summary = [], []
    print(f"{'特征':<10}{'AUC':>8}{'阴性对照':>12}{'净可分性':>10}"
          f"{'最优TPR':>9}{'最优FPR':>9}")
    for sc in SCORES:
        use_pos = [t for t in pos_tr if sc != "rssi_abs" or t["has_rssi"]]
        use_neg = [t for t in neg_tr if sc != "rssi_abs" or t["has_rssi"]]
        if len(use_pos) < 1 or len(use_neg) < 2:
            print(f"{sc:<10}  跳过：可用 trial 不足")
            continue
        pos = [r[sc] for t in use_pos for r in t["rows"]]
        neg = [r[sc] for t in use_neg for r in t["rows"]]
        a = auc(pos, neg)
        pts = roc_points(pos, neg)
        y = youden(pts)

        # 阴性对照：空场按 trial 边界二分，一组假装阳性；取所有划分的最坏值
        ctrl = []
        for k in range(1, len(use_neg)):
            g1 = [r[sc] for t in use_neg[:k] for r in t["rows"]]
            g2 = [r[sc] for t in use_neg[k:] for r in t["rows"]]
            v = auc(g1, g2)
            if not np.isnan(v):
                ctrl.append(max(v, 1 - v))
        c = max(ctrl) if ctrl else float("nan")

        curves.append((sc, pts, a))
        summary.append({"session": tag, "feature": sc, "auc": round(a, 4),
                        "control_auc": round(c, 4), "net": round(a - c, 4),
                        "tpr": round(y["tpr"], 4), "fpr": round(y["fpr"], 4),
                        "threshold": round(y["threshold"], 6),
                        "n_win_pos": len(pos), "n_win_neg": len(neg),
                        "n_trial_pos": len(use_pos), "n_trial_neg": len(use_neg)})
        print(f"{sc:<10}{a:>8.3f}{c:>12.3f}{a - c:>10.3f}"
              f"{y['tpr']:>9.3f}{y['fpr']:>9.3f}")
    print()

    print("trial 级检出（阈值 = 其他空场 trial 窗口的第 95 百分位，留一法）")
    for sc in SCORES:
        use_pos = [t for t in pos_tr if sc != "rssi_abs" or t["has_rssi"]]
        use_neg = [t for t in neg_tr if sc != "rssi_abs" or t["has_rssi"]]
        if len(use_pos) < 1 or len(use_neg) < 2:
            continue
        thr_all = float(np.percentile(
            [r[sc] for o in use_neg for r in o["rows"]], 95))
        det = sum(int(float(np.median([r[sc] for r in t["rows"]])) > thr_all)
                  for t in use_pos)
        fa = 0
        for i, t in enumerate(use_neg):
            others = [r[sc] for o in (use_neg[:i] + use_neg[i + 1:])
                      for r in o["rows"]]
            thr = float(np.percentile(others, 95))
            fa += int(float(np.median([r[sc] for r in t["rows"]])) > thr)
        print(f"  {sc:<10} 检出 {det}/{len(use_pos)}   空场误报 {fa}/{len(use_neg)}")
    print()

    if out_dir and curves:
        write_svg(os.path.join(out_dir, f"roc_{node}_{category}_{tag}.svg"),
                  curves, f"存在性检测 ROC — {node} / {category} / {tag}")
    return summary, curves


# ----------------------------------------------------------------- 主流程
def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--results", default="~/csi_results", help="结果根目录")
    ap.add_argument("--node", default="node1", choices=("node1", "node3"))
    ap.add_argument("--category", default="human",
                    help="类别过滤，逗号分隔或 all（human/plate/foil/box/probe/bend）")
    ap.add_argument("--baseline-sec", type=float, default=4.0,
                    help="每条 trial 用作自身基线的前若干秒")
    ap.add_argument("--skip-sec", type=float, default=1.0,
                    help="基线之后丢弃的过渡秒数")
    ap.add_argument("--min-pkts", type=int, default=400, help="基线窗最少包数")
    ap.add_argument("--keep-flagged", action="store_true",
                    help="保留 HANDOFF 标注有协议缺陷的 trial")
    ap.add_argument("--pooled", action="store_true",
                    help="不按会话分组，全部混在一起（仅用于演示跨会话混杂的危害）")
    ap.add_argument("--out", default="", help="输出目录，留空则只打印")
    args = ap.parse_args()

    root = os.path.expanduser(args.results)
    wanted = None if args.category == "all" else set(
        c.strip() for c in args.category.split(","))

    trials, skipped = [], []
    for d in sorted(glob.glob(os.path.join(root, "*"))):
        if not os.path.isdir(d):
            continue
        name = os.path.basename(d)
        flag = next((v for k, v in FLAGGED.items() if k in name), None)
        if flag and not args.keep_flagged:
            skipped.append((name, flag))
            continue
        label, cat = classify(name)
        if label is None:
            skipped.append((name, "未在标签表中"))
            continue
        if wanted and cat not in wanted:
            skipped.append((name, f"类别 {cat} 不在 --category 内"))
            continue
        try:
            rows, has_rssi = load_trial(d, args.node, args.baseline_sec,
                                        args.skip_sec, args.min_pkts)
        except Exception as e:
            skipped.append((name, str(e)))
            continue
        trials.append({"trial": name, "label": label, "cat": cat,
                       "rows": rows, "has_rssi": has_rssi})

    print("=" * 74)
    print(f"存在性检测 ROC —— {args.node}，类别 {args.category}"
          f"{'（跨会话混合，仅供对照）' if args.pooled else '，按会话分组'}")
    print("=" * 74)
    print(f"每条 trial 以自身前 {args.baseline_sec:g} s 为基线，"
          f"跳过 {args.skip_sec:g} s 过渡，逐 1 s 评分")
    print(f"纳入 {len(trials)} 条，排除 {len(skipped)} 条")
    n_rssi = sum(t["has_rssi"] for t in trials)
    if n_rssi < len(trials):
        print(f"注：{len(trials) - n_rssi}/{len(trials)} 条缺 RSSI"
              f"（8-06 及更早的 aligned_csi.npz 无该键），rssi_abs 仅用子集计算")
    print()

    if not trials:
        print("★ 没有任何 trial 通过筛选。")
        for n, r in skipped:
            print(f"    {n:52s} {r}")
        return 1

    groups = {}
    for t in trials:
        groups.setdefault("pooled" if args.pooled else t["trial"][:8], []).append(t)

    od = os.path.expanduser(args.out) if args.out else ""
    if od:
        os.makedirs(od, exist_ok=True)

    summary, curves, used = [], [], 0
    for tag in sorted(groups):
        r = analyze(groups[tag], tag, od, args.node, args.category)
        if r:
            summary.extend(r[0])
            curves = r[1]
            used += 1

    if not used:
        print("★ 没有任何会话同时具备 ≥2 条阳性与 ≥2 条阴性 trial。")
        print("  这本身是一个结论：现有数据不足以在单会话内做存在性 ROC。")
        print("  可尝试放宽 --category，或用 --keep-flagged 纳入被标注的 trial。")
        return 1

    if od and summary:
        with open(os.path.join(od, f"roc_summary_{args.node}_{args.category}.csv"),
                  "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(summary[0].keys()))
            w.writeheader()
            w.writerows(summary)
        with open(os.path.join(od, f"roc_points_{args.node}_{args.category}.csv"),
                  "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["feature", "fpr", "tpr", "threshold"])
            for name, pts, _ in curves:
                for fpr, tpr, thr in pts:
                    w.writerow([name, f"{fpr:.6f}", f"{tpr:.6f}", f"{thr:.6f}"])
        print(f"已写出：{od}/ 下的 roc_summary / roc_points / roc_*.svg")
    return 0


if __name__ == "__main__":
    sys.exit(main())
