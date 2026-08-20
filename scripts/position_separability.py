#!/usr/bin/env python3
"""检验 CSI 特征能否编码人体位置（位置可分性）。

这是课题的核心问题：CSI 不只要能检测「有没有人」（已证实），
还要能反映「人在哪」，否则无法做位置/姿态回归。

配对方式：
    data/processed/20260811_pilot/pilot_100ms_features.csv.gz 中的
    depth_frame_median 字段已把每个 100ms CSI 分箱映射到深度帧号，
    据此与 depth_to_label.py 产出的 torso_track.csv 关联。

三层检验，逐层收紧：

  L1 单 trial 相关   —— 最弱。CSI 自相关强，易出虚假高相关。
  L2 留一 trial 回归 —— 排除自相关，但**无法排除协议周期性**。
  L3 时间残差检验    —— 关键。先用时间正弦基拟合位置，再看 CSI
                        能否解释剩余残差。这才是 CSI 的真实增量。

L3 的必要性：受试者按固定节拍往返走动，三条 trial 周期分别为
8.15 / 7.85 / 8.10 s，几乎相同。因此「时间」本身就能预测位置——
实测仅用时间正弦基即可达到 30.1% 改善，与 CSI 的 30.7% 几乎相同。
**若不做 L3，会把协议周期性误判为 CSI 的位置编码能力。**

用法：
    python3 position_separability.py [--archive DIR]
"""
import argparse
import csv
import gzip
import os
import sys

import numpy as np

FEATURES = [
    "node1_rssi_mean_dbm", "node3_rssi_mean_dbm",
    "node1_amp_ant0_mean", "node1_amp_ant1_mean",
    "node3_amp_ant0_mean", "node3_amp_ant1_mean",
    "node1_change_ant0", "node1_change_ant1",
    "node3_change_ant0", "node3_change_ant1",
]

TRIALS = {
    "walk_link2_05m_pilot_01": "20260811_161724_walk_link2_05m_pilot_01_cam",
    "walk_link2_05m_pilot_02": "20260811_162114_walk_link2_05m_pilot_02_cam",
    "walk_link2_05m_pilot_03": "20260811_162520_walk_link2_05m_pilot_03_cam",
}


def load_features(path, trial):
    out = []
    with gzip.open(path, "rt") as fh:
        for row in csv.DictReader(fh):
            if row["trial"] == trial:
                out.append(row)
    return out


def load_track(cam_dir):
    """深度帧号 -> (X, Z)。只保留人体帧。"""
    csv_path = os.path.join(cam_dir, "torso_track.csv")
    if not os.path.exists(csv_path):
        raise FileNotFoundError(f"缺 {csv_path}，先跑 depth_to_label.py --stride 1")
    d = np.genfromtxt(csv_path, delimiter=",", names=True)
    ok = (d["blob_px"] >= 10000) & np.isfinite(d["X_m"])
    return {int(f): (x, z) for f, x, z in
            zip(d["frame"][ok], d["X_m"][ok], d["Z_m"][ok])}


def pair(rows, track):
    X, Y, Z = [], [], []
    for r in rows:
        try:
            fr = int(float(r["depth_frame_median"]))
        except (ValueError, TypeError):
            continue
        if fr not in track:
            continue
        try:
            vec = [float(r[k]) for k in FEATURES]
        except (ValueError, TypeError):
            continue
        if not np.all(np.isfinite(vec)):
            continue
        X.append(vec)
        Y.append(track[fr][0])
        Z.append(track[fr][1])
    return np.array(X), np.array(Y), np.array(Z)


def best_abs_corr(feat, target):
    """各特征与目标的最大 |Pearson r|。"""
    best, name = 0.0, None
    for j in range(feat.shape[1]):
        col = feat[:, j]
        if col.std() < 1e-12:
            continue
        r = abs(np.corrcoef(col, target)[0, 1])
        if np.isfinite(r) and r > best:
            best, name = r, FEATURES[j]
    return best, name


def shift_null(feat, target, n=200):
    """循环移位标签作零分布：保留标签时序结构，破坏与 CSI 的对应。"""
    out = []
    L = len(target)
    step = max(1, L // n)
    for s in range(step, L - step, step):
        r, _ = best_abs_corr(feat, np.roll(target, s))
        out.append(r)
    return np.array(out)


def fit_predict(Xtr, ytr, Xte):
    """标准化 + 最小二乘。"""
    mu, sd = Xtr.mean(0), Xtr.std(0) + 1e-9
    A = np.c_[(Xtr - mu) / sd, np.ones(len(Xtr))]
    w = np.linalg.lstsq(A, ytr, rcond=None)[0]
    return np.c_[(Xte - mu) / sd, np.ones(len(Xte))] @ w


def sine_basis(n_samples, period_s=8.0, bin_s=0.1):
    """时间正弦基：捕捉往返走动的协议周期性。"""
    t = np.arange(n_samples) * bin_s
    return np.c_[np.sin(2 * np.pi * t / period_s), np.cos(2 * np.pi * t / period_s)]


def dominant_period(y, bin_s=0.1):
    freqs = np.fft.rfftfreq(len(y), bin_s)
    power = np.abs(np.fft.rfft(y - y.mean()))
    k = int(power[1:].argmax()) + 1
    return 1.0 / freqs[k] if freqs[k] > 0 else float("inf")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--features", default="data/processed/20260811_pilot/pilot_100ms_features.csv.gz")
    ap.add_argument("--archive", default=os.path.expanduser("~/csi_archive/20260811_pilot/node3"))
    args = ap.parse_args()

    if not os.path.exists(args.features):
        sys.exit(f"找不到 {args.features}")

    data = {}
    for trial, cam in TRIALS.items():
        try:
            track = load_track(os.path.join(args.archive, cam))
        except FileNotFoundError as exc:
            print(f"跳过 {trial}: {exc}")
            continue
        feat, gx, gz = pair(load_features(args.features, trial), track)
        if len(feat) >= 30:
            data[trial] = (feat, gx, gz)
    if len(data) < 2:
        sys.exit("可用 trial 不足 2 条，无法做留一验证")
    names = list(data)

    print("=== 走动周期（协议规律性检查）===")
    for n in names:
        print(f"  {n:<26s} 主周期 {dominant_period(data[n][1]):.2f} s")
    print("  周期相近说明「时间」本身即可预测位置，必须做 L3。")
    print()

    print("=== L1 单 trial 相关（最弱证据）===")
    for n in names:
        r, name = best_abs_corr(data[n][0], data[n][1])
        p95 = np.percentile(shift_null(data[n][0], data[n][1]), 95)
        print(f"  {n:<26s} r={r:.3f}  移位零分布p95={p95:.3f}  {'超出' if r > p95 else '未超出'}  ({name})")
    print()

    def loo(get_x, label):
        rmse, base = [], []
        for ho in names:
            tr = [x for x in names if x != ho]
            Xtr = np.vstack([get_x(x) for x in tr])
            ytr = np.concatenate([data[x][1] for x in tr])
            pred = fit_predict(Xtr, ytr, get_x(ho))
            yte = data[ho][1]
            rmse.append(np.sqrt(((pred - yte) ** 2).mean()))
            base.append(np.sqrt(((ytr.mean() - yte) ** 2).mean()))
        m, b = float(np.mean(rmse)), float(np.mean(base))
        print(f"  {label:<26s} RMSE={m:.3f}m  常数基线={b:.3f}m  改善={(1-m/b)*100:+5.1f}%")
        return m

    print("=== L2 留一 trial 回归（排除自相关，但未排除周期性）===")
    r_csi = loo(lambda n: data[n][0], "CSI 特征")
    r_sin = loo(lambda n: sine_basis(len(data[n][1])), "仅时间正弦基（对照）")
    rng = np.random.default_rng(0)
    loo(lambda n: data[n][0][rng.permutation(len(data[n][0]))], "CSI 打乱（零对照）")
    print()
    if r_sin <= r_csi * 1.05:
        print("  ⚠ 时间正弦基与 CSI 表现相当 —— L2 的改善主要来自协议周期性，非位置编码。")
        print()

    print("=== L3 时间残差检验（关键）===")
    gains = []
    for ho in names:
        tr = [x for x in names if x != ho]
        Str = np.vstack([sine_basis(len(data[x][1])) for x in tr])
        ytr = np.concatenate([data[x][1] for x in tr])
        res_tr = ytr - fit_predict(Str, ytr, Str)
        yte = data[ho][1]
        res_te = yte - fit_predict(Str, ytr, sine_basis(len(yte)))
        Ctr = np.vstack([data[x][0] for x in tr])
        pred_res = fit_predict(Ctr, res_tr, data[ho][0])
        b = np.sqrt((res_te ** 2).mean())
        a = np.sqrt(((res_te - pred_res) ** 2).mean())
        gains.append((b, a))
        print(f"  留出 {ho:<24s} 时间后残差 {b:.3f}m → CSI 后 {a:.3f}m  改善 {(1-a/b)*100:+5.1f}%")
    g = np.array(gains)
    overall = (1 - g[:, 1].mean() / g[:, 0].mean()) * 100
    print(f"  平均增量改善：{overall:+.1f}%")
    print()
    print("=== 结论 ===")
    if overall < 15:
        print(f"  CSI 在扣除协议周期性后仅贡献 {overall:.1f}% 增量。")
        print("  当前特征集**不足以支撑位置回归**。")
    else:
        print(f"  CSI 提供 {overall:.1f}% 增量改善，存在位置信息。")
    print("  注意：本特征集仅含 RSSI 与幅度统计量，而 |H| 绝对幅度受 AGC 污染")
    print("  （见 docs/experiments/20260820_link_integrity.md）。")
    print("  结论仅适用于该特征集，需用归一化频谱形状重做后才能下最终判断。")


if __name__ == "__main__":
    main()
