#!/usr/bin/env python3
"""多普勒特征：存在性检测、径向速度回归、位置回归。

动机：
    幅度 (+9.8%)、频谱 (+13.5%)、相位 (+14.2%) 三轮位置回归均判定不足，
    且组合无增益（+15.2%），提示瓶颈不在特征表示。
    多普勒是唯一尚未尝试的方向，且测量的是**运动**而非瞬时位置——
    可能与前三者携带不同信息。

物理可行性（本平台实测）：
    包速率 138.1 Hz → Nyquist 69.0 Hz
    λ = 12.44 cm，径向速度 v 对应 fd ≤ 2v/λ
    人体步速 0.9–1.3 m/s → fd 14–21 Hz，有 3 倍余量，不混叠

方法说明：
    多普勒谱用**归一化幅度**的时间变化计算，而非相位。
    原因：AX210 相位受 CFO/SFO 污染（原始抖动 ±101.5°），
    虽可用相邻子载波差分校准，但该操作会同时抑制多普勒信号。
    幅度先除以时间均值以消除 AGC 的宽带增益。

    使用绝对能量而非能量比值：比值会被低频分母主导，
    实测给出与物理相反的结论（空场 23.2 > 有人 3.0-7.2）。

用法：
    python3 doppler_position.py [--pca 32]
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
FS = 138.1          # 实测包速率
WIN_S = 2.0         # STFT 窗长
BAND = (2.0, 25.0)  # 人体运动多普勒带

TRIALS = {
    "walk_link2_05m_pilot_01": ("20260811_161726", "20260811_161724_walk_link2_05m_pilot_01_cam"),
    "walk_link2_05m_pilot_02": ("20260811_162117", "20260811_162114_walk_link2_05m_pilot_02_cam"),
    "walk_link2_05m_pilot_03": ("20260811_162523", "20260811_162520_walk_link2_05m_pilot_03_cam"),
}
EMPTY = ["20260811_160839", "20260811_161230"]


def load_csi(prefix, node="node1"):
    hits = glob.glob(os.path.join(ARCHIVE, node, f"{prefix}_*", f"{node}_*_target_csi.npz"))
    if not hits:
        hits = sorted(glob.glob(os.path.join(ARCHIVE, node, f"{prefix[:13]}*",
                                             f"{node}_*_target_csi.npz")))
    if not hits:
        raise FileNotFoundError(f"{node}/{prefix}")
    c = np.load(hits[0])["target_csi"]
    if c.shape[1] == 242:
        mask = np.ones(242, dtype=bool)
        mask[PILOT_IDX] = False
        c = c[:, mask]
    return c


def doppler_frames(c, duration_s, hop_s=0.1, ant=0):
    """滑窗多普勒谱。返回 (帧, 频带能量向量) 与窗中心时刻。

    幅度归一化消除 AGC；每窗去均值后做加窗 FFT。
    """
    a = np.abs(c[:, :, ant, 0]).astype(np.float64)
    a /= a.mean(axis=0, keepdims=True) + 1e-12
    n = len(a)
    fs = n / duration_s
    W = int(WIN_S * fs)
    hop = max(1, int(hop_s * fs))
    win = np.hanning(W)[:, None]
    freqs = np.fft.rfftfreq(W, 1.0 / fs)
    sel = (freqs >= BAND[0]) & (freqs <= BAND[1])
    feats, centers = [], []
    for i in range(0, n - W, hop):
        seg = a[i:i + W]
        seg = seg - seg.mean(0)
        P = np.abs(np.fft.rfft(seg * win, axis=0)) ** 2   # (F, n_sub)
        # 跨子载波取中位，抑制个别子载波的异常
        spec = np.median(P[sel], axis=1)
        feats.append(np.log1p(spec))
        centers.append((i + W / 2) / fs)
    return np.array(feats), np.array(centers)


def band_energy(c, duration_s, ant=0):
    """每窗的总多普勒能量（存在性检测用）。"""
    f, t = doppler_frames(c, duration_s, hop_s=1.0, ant=ant)
    return np.expm1(f).sum(axis=1), t


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
    return {int(f): (float(x), float(z)) for f, x, z in
            zip(d["frame"][ok], d["X_m"][ok], d["Z_m"][ok])}


def build(features_path, trial, prefix, cam):
    slope, intercept, tmax = frame_map(features_path, trial)
    track = load_track(os.path.join(CAM_ROOT, cam))
    f1, t1 = doppler_frames(load_csi(prefix, "node1"), tmax)
    f3, _ = doppler_frames(load_csi(prefix, "node3"), tmax)
    n = min(len(f1), len(f3))
    X, pos, tt = [], [], []
    for i in range(n):
        fr = int(round(slope * t1[i] + intercept))
        if fr not in track:
            continue
        X.append(np.concatenate([f1[i], f3[i]]))
        pos.append(track[fr])
        tt.append(t1[i])
    pos = np.array(pos)
    return np.array(X), pos[:, 0], np.array(tt)


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

    # ---- 存在性检测 ----
    print("=== 存在性：多普勒频带能量（2–25 Hz）===")
    empty_vals = []
    for p in EMPTY:
        try:
            e, _ = band_energy(load_csi(p), 50.0)
        except FileNotFoundError:
            continue
        empty_vals.append(np.median(e))
        print(f"  空场 {p[-6:]}  中位 {np.median(e):10.1f}")
    base = float(np.median(empty_vals)) if empty_vals else float("nan")
    for trial, (prefix, _) in TRIALS.items():
        e, _ = band_energy(load_csi(prefix), 50.0)
        print(f"  {trial[-8:]}     中位 {np.median(e):10.1f}   {np.median(e)/base:.2f}× 空场")
    print()

    # ---- 位置回归 ----
    data = {}
    for trial, (prefix, cam) in TRIALS.items():
        try:
            X, gx, t = build(args.features, trial, prefix, cam)
        except (FileNotFoundError, ValueError) as exc:
            print(f"跳过 {trial}: {exc}")
            continue
        if len(X) >= 30:
            data[trial] = (X, gx, t)
    if len(data) < 2:
        sys.exit("可用 trial 不足")
    names = list(data)
    print(f"配对样本：{ {n: len(data[n][0]) for n in names} }，特征维 {data[names[0]][0].shape[1]}")
    print()

    def project(train_names, k):
        M = np.vstack([data[n][0] for n in train_names])
        mu = M.mean(0)
        _, _, Vt = np.linalg.svd(M - mu, full_matrices=False)
        P = Vt[:k].T
        return lambda X: (X - mu) @ P

    def loo(get_x, label, use_pca=False):
        rmse, base_ = [], []
        for ho in names:
            tr = [x for x in names if x != ho]
            proj = project(tr, args.pca) if use_pca else None
            Xtr = np.vstack([(proj(data[x][0]) if use_pca else get_x(x)) for x in tr])
            ytr = np.concatenate([data[x][1] for x in tr])
            Xte = proj(data[ho][0]) if use_pca else get_x(ho)
            pred = fit_predict(Xtr, ytr, Xte, args.ridge)
            yte = data[ho][1]
            rmse.append(np.sqrt(((pred - yte) ** 2).mean()))
            base_.append(np.sqrt(((ytr.mean() - yte) ** 2).mean()))
        m, b = float(np.mean(rmse)), float(np.mean(base_))
        print(f"  {label:<30s} RMSE={m:.3f}m  基线={b:.3f}m  改善={(1-m/b)*100:+5.1f}%")

    print(f"=== L2 留一 trial 回归（多普勒 PCA-{args.pca}）===")
    loo(None, f"多普勒谱 ({args.pca}维)", use_pca=True)
    loo(lambda n: sine_basis(data[n][2]), "仅时间正弦基（对照）")
    rng = np.random.default_rng(0)
    loo(lambda n: data[n][0][rng.permutation(len(data[n][0]))][:, :args.pca], "多普勒打乱（零对照）")
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
    print("=== 四种特征对比（L3 增量）===")
    print("  幅度统计量 10 维      +9.8%")
    print("  归一化频谱 PCA-32    +13.5%")
    print("  校准相位 PCA-32      +14.2%")
    print(f"  多普勒谱 PCA-{args.pca:<3d}      {overall:+.1f}%")


if __name__ == "__main__":
    main()
