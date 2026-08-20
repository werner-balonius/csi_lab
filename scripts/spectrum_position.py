#!/usr/bin/env python3
"""用归一化频谱形状（对 AGC 免疫）重做位置可分性检验。

背景：
    position_separability.py 使用的 10 维特征中 8 维基于 |H| 绝对幅度，
    而 20260820_link_integrity.md 证实 AGC 会补偿约 13.88 dB，抹掉幅度信息。
    本脚本改用逐子载波归一化频谱，理论上对 AGC 免疫。

数据来源：
    8-11 的 aligned_csi.npz 不在本地（当时在队友机器上生成），
    因此直接读节点侧 *_target_csi.npz，并用特征表标定的
    「帧号 = 30 × t + offset」关系建立时间轴。

    该线性关系由特征表实测得到（斜率精确为 30.000），
    见 §复现。包数差异（约 1%，对齐时丢弃的未匹配包）按比例吸收。

三层检验沿用 position_separability.py 的设计，L3 为关键判据。

用法：
    python3 spectrum_position.py [--pca 8]
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
FPS = 30.0


def load_node_csi(prefix, node):
    """节点侧原始 CSI 幅度，导频已剔除。"""
    pat = os.path.join(ARCHIVE, node, f"{prefix}_*", f"{node}_*_target_csi.npz")
    hits = glob.glob(pat)
    if not hits:
        # node1 与 node3 目录时间戳可能差 1-3 秒
        base = prefix[:13]
        hits = sorted(glob.glob(os.path.join(ARCHIVE, node, f"{base}*", f"{node}_*_target_csi.npz")))
    if not hits:
        raise FileNotFoundError(f"找不到 {node} 的 {prefix}")
    amp = np.abs(np.load(hits[0])["target_csi"])
    if amp.shape[1] == 242:
        mask = np.ones(242, dtype=bool)
        mask[PILOT_IDX] = False
        amp = amp[:, mask]
    return amp


def frame_offset(features_path, trial):
    """从特征表反推 帧号 = FPS * t + offset。"""
    ts, fr = [], []
    with gzip.open(features_path, "rt") as fh:
        for r in csv.DictReader(fh):
            if r["trial"] != trial or not r["depth_frame_median"]:
                continue
            ts.append(float(r["time_s"]))
            fr.append(float(r["depth_frame_median"]))
    if len(ts) < 10:
        raise ValueError(f"{trial} 特征表样本不足")
    slope, intercept = np.polyfit(np.array(ts), np.array(fr), 1)
    return slope, intercept, max(ts)


def spectrum_bins(amp, duration_s, bin_s=BIN_S):
    """按时间分箱，每箱输出归一化频谱（除以自身均值，消除 AGC 宽带增益）。"""
    n_pkt = amp.shape[0]
    t = np.arange(n_pkt) / n_pkt * duration_s
    idx = np.floor(t / bin_s).astype(int)
    out, centers = [], []
    for b in np.unique(idx):
        m = idx == b
        if m.sum() < 3:
            continue
        s = amp[m].mean(axis=(0,) + tuple(range(2, amp.ndim)))
        out.append(s / s.mean())
        centers.append(float(t[m].mean()))
    return np.array(out), np.array(centers)


def load_track(cam_dir):
    path = os.path.join(cam_dir, "torso_track.csv")
    if not os.path.exists(path):
        raise FileNotFoundError(f"缺 {path}，先跑 depth_to_label.py --stride 1")
    d = np.genfromtxt(path, delimiter=",", names=True)
    ok = (d["blob_px"] >= 10000) & np.isfinite(d["X_m"])
    return {int(f): float(x) for f, x in zip(d["frame"][ok], d["X_m"][ok])}


def build(features_path, trial, prefix, cam):
    slope, intercept, tmax = frame_offset(features_path, trial)
    track = load_track(os.path.join(CAM_ROOT, cam))
    feats, targets, times = [], [], []
    for node in ("node1", "node3"):
        pass  # 两节点分别取谱后拼接
    sp1, c1 = spectrum_bins(load_node_csi(prefix, "node1"), tmax)
    sp3, c3 = spectrum_bins(load_node_csi(prefix, "node3"), tmax)
    n = min(len(sp1), len(sp3))
    for i in range(n):
        fr = int(round(slope * c1[i] + intercept))
        if fr not in track:
            continue
        feats.append(np.concatenate([sp1[i], sp3[i]]))
        targets.append(track[fr])
        times.append(c1[i])
    return np.array(feats), np.array(targets), np.array(times)


def fit_predict(Xtr, ytr, Xte, ridge=1.0):
    mu, sd = Xtr.mean(0), Xtr.std(0) + 1e-9
    A = np.c_[(Xtr - mu) / sd, np.ones(len(Xtr))]
    B = np.c_[(Xte - mu) / sd, np.ones(len(Xte))]
    G = A.T @ A + ridge * np.eye(A.shape[1])
    w = np.linalg.solve(G, A.T @ ytr)
    return B @ w


def sine_basis(times, period_s=8.0):
    return np.c_[np.sin(2 * np.pi * times / period_s), np.cos(2 * np.pi * times / period_s)]


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--features", default="data/processed/20260811_pilot/pilot_100ms_features.csv.gz")
    ap.add_argument("--pca", type=int, default=8, help="频谱降维维数（0 = 不降维）")
    ap.add_argument("--ridge", type=float, default=10.0)
    args = ap.parse_args()

    if not os.path.exists(args.features):
        sys.exit(f"找不到 {args.features}")

    data = {}
    for trial, (prefix, cam) in TRIALS.items():
        try:
            X, y, t = build(args.features, trial, prefix, cam)
        except (FileNotFoundError, ValueError) as exc:
            print(f"跳过 {trial}: {exc}")
            continue
        if len(X) >= 30:
            data[trial] = (X, y, t)
            print(f"{trial}: n={len(X)}  特征维={X.shape[1]}  X范围 {y.min():+.2f}~{y.max():+.2f}")
    if len(data) < 2:
        sys.exit("可用 trial 不足")
    names = list(data)
    print()

    # PCA 在训练集上拟合，避免泄漏
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
            proj = project(tr, args.pca) if use_pca else (lambda X: X)
            Xtr = np.vstack([proj(data[x][0]) if use_pca else get_x(x) for x in tr])
            ytr = np.concatenate([data[x][1] for x in tr])
            Xte = proj(data[ho][0]) if use_pca else get_x(ho)
            pred = fit_predict(Xtr, ytr, Xte, args.ridge)
            yte = data[ho][1]
            rmse.append(np.sqrt(((pred - yte) ** 2).mean()))
            base.append(np.sqrt(((ytr.mean() - yte) ** 2).mean()))
        m, b = float(np.mean(rmse)), float(np.mean(base))
        print(f"  {label:<30s} RMSE={m:.3f}m  基线={b:.3f}m  改善={(1-m/b)*100:+5.1f}%")
        return m

    print(f"=== L2 留一 trial 回归（频谱 PCA-{args.pca}, ridge={args.ridge}）===")
    loo(None, f"归一化频谱 ({args.pca}维)", use_pca=True)
    loo(lambda n: sine_basis(data[n][2]), "仅时间正弦基（对照）")
    rng = np.random.default_rng(0)
    loo(lambda n: data[n][0][rng.permutation(len(data[n][0]))][:, :args.pca], "频谱打乱（零对照）")
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
        pred_res = fit_predict(Ctr, res_tr, proj(data[ho][0]), args.ridge)
        b = np.sqrt((res_te ** 2).mean())
        a = np.sqrt(((res_te - pred_res) ** 2).mean())
        gains.append((b, a))
        print(f"  留出 {ho:<26s} {b:.3f}m → {a:.3f}m  改善 {(1-a/b)*100:+5.1f}%")
    g = np.array(gains)
    overall = (1 - g[:, 1].mean() / g[:, 0].mean()) * 100
    print(f"  平均增量改善：{overall:+.1f}%")
    print()
    print("=== 对比 ===")
    print("  幅度统计量特征（position_separability.py）：+9.8%")
    print(f"  归一化频谱形状（本脚本）：{overall:+.1f}%")


if __name__ == "__main__":
    main()
