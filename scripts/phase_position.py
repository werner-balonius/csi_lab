#!/usr/bin/env python3
"""用校准后的 CSI 相位重做位置可分性检验。

动机：
    幅度与频谱形状两轮检验（L3 增量 9.8% / 13.5%）均判定不足。
    相位对位置的理论敏感度高一个量级：λ = 12.4 cm，
    人移动 6 cm 即产生 π 相移，而幅度需移动几十厘米才有可测变化。

相位校准（必须，否则相位不可用）：
    AX210 原始相位受 CFO/SFO/时戳抖动污染，实测相邻包间抖动 ±102°。
    三种方案实测对比（相邻包相位差 std）：

        原始              1.7723 rad (101.5°)
        跨天线共轭积       1.3627 rad ( 78.1°)   两天线噪声非共模
        线性去斜率         0.6235 rad ( 35.7°)
        相邻子载波差分     0.0699 rad (  4.0°)   ← 采用，改善 25 倍

    相邻子载波差分 z[k] = H[k+1] · conj(H[k])，消除所有与子载波无关的
    公共相位项（CFO、采样时钟偏移、包时戳抖动），保留频率选择性结构。

三层检验沿用 position_separability.py 设计，L3 为关键判据。

用法：
    python3 phase_position.py [--pca 32]
"""
import argparse
import csv
import glob
import gzip
import os
import sys

import numpy as np

PILOT_IDX = [6, 32, 74, 100, 141, 167, 209, 235]
ARCHIVE = os.path.expanduser("~/csi_archive/20260811_pilot")
CAM_ROOT = os.path.join(ARCHIVE, "node3")

TRIALS = {
    "walk_link2_05m_pilot_01": ("20260811_161726", "20260811_161724_walk_link2_05m_pilot_01_cam"),
    "walk_link2_05m_pilot_02": ("20260811_162117", "20260811_162114_walk_link2_05m_pilot_02_cam"),
    "walk_link2_05m_pilot_03": ("20260811_162523", "20260811_162520_walk_link2_05m_pilot_03_cam"),
}
BIN_S = 0.1


def load_csi(prefix, node):
    hits = glob.glob(os.path.join(ARCHIVE, node, f"{prefix}_*", f"{node}_*_target_csi.npz"))
    if not hits:
        base = prefix[:13]
        hits = sorted(glob.glob(os.path.join(ARCHIVE, node, f"{base}*", f"{node}_*_target_csi.npz")))
    if not hits:
        raise FileNotFoundError(f"{node}/{prefix}")
    c = np.load(hits[0])["target_csi"]
    if c.shape[1] == 242:
        mask = np.ones(242, dtype=bool)
        mask[PILOT_IDX] = False
        c = c[:, mask]
    return c


def csd(c):
    """相邻子载波共轭差分，消除公共相位项。返回 (N, n_sub-1, n_rx) 复数。"""
    return c[:, 1:, :, 0] * np.conj(c[:, :-1, :, 0])


def phase_bins(c, duration_s, bin_s=BIN_S):
    """按时间分箱，每箱输出相位特征。

    箱内对复数取平均后再取角度（相干平均），比先取角度再平均更稳健，
    可避免 ±π 环绕造成的伪影。
    """
    z = csd(c)
    n = z.shape[0]
    t = np.arange(n) / n * duration_s
    idx = np.floor(t / bin_s).astype(int)
    feats, centers = [], []
    for b in np.unique(idx):
        m = idx == b
        if m.sum() < 3:
            continue
        zc = z[m].mean(axis=0)              # 相干平均 (n_sub-1, n_rx)
        feats.append(np.angle(zc).ravel())
        centers.append(float(t[m].mean()))
    return np.array(feats), np.array(centers)


def frame_map(features_path, trial):
    ts, fr = [], []
    with gzip.open(features_path, "rt") as fh:
        for r in csv.DictReader(fh):
            if r["trial"] == trial and r["depth_frame_median"]:
                ts.append(float(r["time_s"]))
                fr.append(float(r["depth_frame_median"]))
    if len(ts) < 10:
        raise ValueError(f"{trial} 样本不足")
    slope, intercept = np.polyfit(np.array(ts), np.array(fr), 1)
    return slope, intercept, max(ts)


def load_track(cam_dir):
    path = os.path.join(cam_dir, "torso_track.csv")
    if not os.path.exists(path):
        raise FileNotFoundError(f"缺 {path}")
    d = np.genfromtxt(path, delimiter=",", names=True)
    ok = (d["blob_px"] >= 10000) & np.isfinite(d["X_m"])
    return {int(f): float(x) for f, x in zip(d["frame"][ok], d["X_m"][ok])}


def build(features_path, trial, prefix, cam):
    slope, intercept, tmax = frame_map(features_path, trial)
    track = load_track(os.path.join(CAM_ROOT, cam))
    f1, c1 = phase_bins(load_csi(prefix, "node1"), tmax)
    f3, c3 = phase_bins(load_csi(prefix, "node3"), tmax)
    n = min(len(f1), len(f3))
    X, y, t = [], [], []
    for i in range(n):
        fr = int(round(slope * c1[i] + intercept))
        if fr not in track:
            continue
        X.append(np.concatenate([f1[i], f3[i]]))
        y.append(track[fr])
        t.append(c1[i])
    return np.array(X), np.array(y), np.array(t)


def fit_predict(Xtr, ytr, Xte, ridge=10.0):
    mu, sd = Xtr.mean(0), Xtr.std(0) + 1e-9
    A = np.c_[(Xtr - mu) / sd, np.ones(len(Xtr))]
    B = np.c_[(Xte - mu) / sd, np.ones(len(Xte))]
    w = np.linalg.solve(A.T @ A + ridge * np.eye(A.shape[1]), A.T @ ytr)
    return B @ w


def sine_basis(t, period_s=8.0):
    return np.c_[np.sin(2 * np.pi * t / period_s), np.cos(2 * np.pi * t / period_s)]


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--features", default="data/processed/20260811_pilot/pilot_100ms_features.csv.gz")
    ap.add_argument("--pca", type=int, default=32)
    ap.add_argument("--ridge", type=float, default=10.0)
    args = ap.parse_args()

    data = {}
    for trial, (prefix, cam) in TRIALS.items():
        try:
            X, y, t = build(args.features, trial, prefix, cam)
        except (FileNotFoundError, ValueError) as exc:
            print(f"跳过 {trial}: {exc}")
            continue
        if len(X) >= 30:
            data[trial] = (X, y, t)
            print(f"{trial}: n={len(X)} 维={X.shape[1]}")
    if len(data) < 2:
        sys.exit("可用 trial 不足")
    names = list(data)
    print()

    def project(train_names, k):
        if k <= 0:
            return lambda X: X
        M = np.vstack([data[n][0] for n in train_names])
        mu = M.mean(0)
        _, _, Vt = np.linalg.svd(M - mu, full_matrices=False)
        P = Vt[:k].T
        return lambda X: (X - mu) @ P

    def loo(get_x, label, use_pca=False):
        rmse, base = [], []
        for ho in names:
            tr = [x for x in names if x != ho]
            proj = project(tr, args.pca) if use_pca else None
            Xtr = np.vstack([(proj(data[x][0]) if use_pca else get_x(x)) for x in tr])
            ytr = np.concatenate([data[x][1] for x in tr])
            Xte = proj(data[ho][0]) if use_pca else get_x(ho)
            pred = fit_predict(Xtr, ytr, Xte, args.ridge)
            yte = data[ho][1]
            rmse.append(np.sqrt(((pred - yte) ** 2).mean()))
            base.append(np.sqrt(((ytr.mean() - yte) ** 2).mean()))
        m, b = float(np.mean(rmse)), float(np.mean(base))
        print(f"  {label:<30s} RMSE={m:.3f}m  基线={b:.3f}m  改善={(1-m/b)*100:+5.1f}%")
        return m

    print(f"=== L2 留一 trial 回归（相位 PCA-{args.pca}）===")
    loo(None, f"校准相位 ({args.pca}维)", use_pca=True)
    loo(lambda n: sine_basis(data[n][2]), "仅时间正弦基（对照）")
    rng = np.random.default_rng(0)
    loo(lambda n: data[n][0][rng.permutation(len(data[n][0]))][:, :args.pca], "相位打乱（零对照）")
    print()

    print("=== L3 时间残差检验（关键）===")
    gains = []
    for ho in names:
        tr = [x for x in names if x != ho]
        proj = project(tr, args.pca)
        Str = np.vstack([sine_basis(data[x][2]) for x in tr])
        ytr = np.concatenate([data[x][1] for x in tr])
        res_tr = ytr - fit_predict(Str, ytr, Str, args.ridge)
        yte = data[ho][1]
        res_te = yte - fit_predict(Str, ytr, sine_basis(data[ho][2]), args.ridge)
        Ctr = np.vstack([proj(data[x][0]) for x in tr])
        pred = fit_predict(Ctr, res_tr, proj(data[ho][0]), args.ridge)
        b = np.sqrt((res_te ** 2).mean())
        a = np.sqrt(((res_te - pred) ** 2).mean())
        gains.append((b, a))
        print(f"  留出 {ho:<26s} {b:.3f}m → {a:.3f}m  改善 {(1-a/b)*100:+5.1f}%")
    g = np.array(gains)
    overall = (1 - g[:, 1].mean() / g[:, 0].mean()) * 100
    print(f"  平均增量改善：{overall:+.1f}%")
    print()
    print("=== 三种特征对比（L3 增量）===")
    print("  幅度统计量 10 维      +9.8%")
    print("  归一化频谱 PCA-32    +13.5%")
    print(f"  校准相位 PCA-{args.pca:<3d}      {overall:+.1f}%")


if __name__ == "__main__":
    main()
