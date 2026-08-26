#!/usr/bin/env python3
"""lag 容忍度：判定对齐优化的收益上限。

动机
----
Zhuo Chen 在 V4 文档 §7 提出施加 0/±33/±66/±100/±200 ms 额外 lag，
观察性能下降，以判断任务能容忍多大同步误差。

**但按该方案直接做，在本数据上注定得到平坦曲线**（见 §功效分析），
而平坦曲线有两种解释——"模型不用精细时间信息"或"实验没有功效"——
两者无法区分。这正是 8-20 置换检验踩过的坑：
得到阴性结果，却不知道是真阴性还是没测出来。

功效分析（先算再做）
--------------------
两条曲线决定了实验能不能做：

1. **oracle 灵敏度** —— 即使预测器完美，施加 lag 的理论涨幅
   （由标签自身的时间自相关决定，与模型无关）：

       lag  33ms -> 标签自身 RMSE 0.122m -> 相对 0.53m 残差涨  2.6%
       lag  66ms ->                0.145m ->                  3.7%
       lag 200ms ->                0.251m ->                 10.7%
       lag 500ms ->                0.509m ->                 38.7%

2. **噪声底** —— 三条留出 trial 的残差 0.726/0.652/1.146 m，
   变异系数 **31.7%**。

对照可见：±33/±66 ms 的理论效应（2.6–3.7%）比噪声底小一个数量级，
**不可测**。只有 lag >= 500 ms 才明显超过噪声。

改进设计
--------
不问"性能掉多少"，改问 **"最优 lag 落在哪"**：

    扫描 lag ∈ [-1000, +1000] ms，找 RMSE 曲线的最小值位置 lag*

    lag* ≈ 0            -> 当前对齐已接近最优，M0-M3 无收益
    lag* 显著偏离 0      -> 存在系统性时间偏移，值得修正

**为什么更稳健**：极值位置不依赖绝对涨幅。三条 trial 的残差水平
即使差异很大（0.65 vs 1.15 m），只要各自曲线最低点都落在 0 附近，
结论就成立。噪声影响曲线高度，不影响极值位置。

三层判据
--------
    L1 曲线是否有明确极值   边缘(±1000ms)比中心高 >30%
    L2 最优 lag 是否一致    三条 trial 落在同一侧
    L3 极值是否显著偏 0     |lag*| > 66ms 且三条同号

零对照（关键）
--------------
把 CSI 特征按样本打乱后重跑。若打乱后仍出现"最优 lag"，
说明极值来自标签自相关而非真实对齐——这是本设计最大的风险点，
必须检验。

用法
----
    python3 scripts/lag_tolerance.py                 # 默认频谱特征
    python3 scripts/lag_tolerance.py --feature phase
    python3 scripts/lag_tolerance.py --max-lag 1000 --step 33
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
BIN_S = 0.1
FPS = 30.0

TRIALS = {
    "walk_link2_05m_pilot_01": ("20260811_161726", "20260811_161724_walk_link2_05m_pilot_01_cam"),
    "walk_link2_05m_pilot_02": ("20260811_162117", "20260811_162114_walk_link2_05m_pilot_02_cam"),
    "walk_link2_05m_pilot_03": ("20260811_162523", "20260811_162520_walk_link2_05m_pilot_03_cam"),
}

# numpy 2.0 + macOS Accelerate BLAS 对正常 matmul 误报，已验证结果有限
if np.__config__.CONFIG.get("Build Dependencies", {}).get(
        "blas", {}).get("name") == "accelerate":
    np.seterr(divide="ignore", over="ignore", invalid="ignore")


def load_csi(prefix, node):
    pat = os.path.join(ARCHIVE, node, f"{prefix}_*", f"{node}_*_target_csi.npz")
    hits = glob.glob(pat) or sorted(glob.glob(
        os.path.join(ARCHIVE, node, f"{prefix[:13]}*", f"{node}_*_target_csi.npz")))
    if not hits:
        raise FileNotFoundError(f"{node}/{prefix}")
    c = np.load(hits[0])["target_csi"]
    if c.shape[1] == 242:
        m = np.ones(242, dtype=bool)
        m[PILOT_IDX] = False
        c = c[:, m]
    return c


def frame_map(features_path, trial):
    """从特征表反推 帧号 = slope*t + intercept，并给出真实时长。"""
    ts, fr = [], []
    with gzip.open(features_path, "rt") as fh:
        for r in csv.DictReader(fh):
            if r["trial"] == trial and r["depth_frame_median"]:
                ts.append(float(r["time_s"]))
                fr.append(float(r["depth_frame_median"]))
    if len(ts) < 10:
        raise ValueError(f"{trial} 样本不足")
    slope, intercept = np.polyfit(np.array(ts), np.array(fr), 1)
    # time_s 为分箱左边缘，时长需补一个分箱宽度
    return slope, intercept, max(ts) - min(ts) + BIN_S


def spectrum_bins(c, duration_s):
    """归一化频谱（除以自身均值，消除 AGC 宽带增益）。"""
    amp = np.abs(c)
    n = amp.shape[0]
    t = np.arange(n) / n * duration_s
    idx = np.floor(t / BIN_S).astype(int)
    out, ctr = [], []
    for b in np.unique(idx):
        m = idx == b
        if m.sum() < 3:
            continue
        s = amp[m].mean(axis=(0,) + tuple(range(2, amp.ndim)))
        out.append(s / (s.mean() + 1e-12))
        ctr.append(float(t[m].mean()))
    return np.array(out), np.array(ctr)


def phase_bins(c, duration_s):
    """相邻子载波差分相位（消除 SFO），复数相干平均后取角。"""
    z = c[:, 1:, :, 0] * np.conj(c[:, :-1, :, 0])
    n = z.shape[0]
    t = np.arange(n) / n * duration_s
    idx = np.floor(t / BIN_S).astype(int)
    out, ctr = [], []
    for b in np.unique(idx):
        m = idx == b
        if m.sum() < 3:
            continue
        zz = z[m].mean(axis=0)                      # 相干平均
        out.append(np.angle(zz).ravel())
        ctr.append(float(t[m].mean()))
    return np.array(out), np.array(ctr)


def load_track(cam_dir):
    p = os.path.join(cam_dir, "torso_track.csv")
    if not os.path.exists(p):
        raise FileNotFoundError(f"缺 {p}")
    d = np.genfromtxt(p, delimiter=",", names=True)
    ok = (d["blob_px"] >= 10000) & np.isfinite(d["X_m"])
    return {int(f): float(x) for f, x in zip(d["frame"][ok], d["X_m"][ok])}


def build(features_path, trial, prefix, cam, kind):
    """返回 (特征, 分箱中心时刻, 帧映射参数, 轨迹字典)。

    特征与标签**不在此处配对** —— 配对在扫描 lag 时逐次进行。
    """
    slope, intercept, dur = frame_map(features_path, trial)
    track = load_track(os.path.join(CAM_ROOT, cam))
    fn = {"spectrum": spectrum_bins, "phase": phase_bins}[kind]
    f1, c1 = fn(load_csi(prefix, "node1"), dur)
    f3, _ = fn(load_csi(prefix, "node3"), dur)
    n = min(len(f1), len(f3))
    X = np.hstack([f1[:n], f3[:n]])
    return X, c1[:n], (slope, intercept), track


def pair(X, ctr, fmap, track, lag_s):
    """按给定 lag 配对特征与标签。lag>0 表示标签取更晚的帧。"""
    slope, intercept = fmap
    xs, ys = [], []
    for i, t in enumerate(ctr):
        fr = int(round(slope * (t + lag_s) + intercept))
        if fr in track:
            xs.append(X[i])
            ys.append(track[fr])
    if not xs:
        return np.empty((0, X.shape[1])), np.empty(0)
    return np.array(xs), np.array(ys)


def pca_fit(M, k):
    mu = M.mean(0)
    _, _, Vt = np.linalg.svd(M - mu, full_matrices=False)
    P = Vt[:k].T
    return lambda Z: (Z - mu) @ P


def ridge_pred(Xtr, ytr, Xte, ridge):
    mu, sd = Xtr.mean(0), Xtr.std(0) + 1e-9
    A = np.c_[(Xtr - mu) / sd, np.ones(len(Xtr))]
    B = np.c_[(Xte - mu) / sd, np.ones(len(Xte))]
    w = np.linalg.solve(A.T @ A + ridge * np.eye(A.shape[1]), A.T @ ytr)
    return B @ w


def loo_rmse(data, lag_s, pca_k, ridge, shuffle_rng=None):
    """留一 trial 交叉验证，返回每条 trial 的 RMSE。"""
    names = list(data)
    out = {}
    for ho in names:
        tr = [n for n in names if n != ho]
        parts = []
        for n in tr:
            X, ctr, fm, tk = data[n]
            xs, ys = pair(X, ctr, fm, tk, lag_s)
            if shuffle_rng is not None and len(xs):
                xs = xs[shuffle_rng.permutation(len(xs))]
            parts.append((xs, ys))
        Xtr = np.vstack([p[0] for p in parts])
        ytr = np.concatenate([p[1] for p in parts])
        X, ctr, fm, tk = data[ho]
        Xte, yte = pair(X, ctr, fm, tk, lag_s)
        if shuffle_rng is not None and len(Xte):
            Xte = Xte[shuffle_rng.permutation(len(Xte))]
        if len(Xtr) < pca_k + 5 or len(Xte) < 5:
            out[ho] = np.nan
            continue
        proj = pca_fit(Xtr, pca_k)
        pred = ridge_pred(proj(Xtr), ytr, proj(Xte), ridge)
        out[ho] = float(np.sqrt(np.mean((pred - yte) ** 2)))
    return out


def oracle_curve(data, lags_ms):
    """标签自一致性：施加 lag 后标签自身的 RMSE（与模型无关）。"""
    rows = {}
    for n, (_, _, fm, tk) in data.items():
        slope = fm[0]
        vals = []
        for L in lags_ms:
            sh = int(round(L / 1000.0 * slope))
            ds = [tk[f + sh] - tk[f] for f in tk if (f + sh) in tk]
            vals.append(np.sqrt(np.mean(np.square(ds))) if ds else np.nan)
        rows[n] = np.array(vals)
    return rows


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--features",
                    default="data/processed/20260811_pilot/pilot_100ms_features.csv.gz")
    ap.add_argument("--feature", choices=["spectrum", "phase"], default="spectrum")
    ap.add_argument("--max-lag", type=float, default=1000.0, help="ms")
    ap.add_argument("--step", type=float, default=33.0, help="ms")
    ap.add_argument("--pca", type=int, default=32)
    ap.add_argument("--ridge", type=float, default=10.0)
    ap.add_argument("--seed", type=int, default=20260824)
    args = ap.parse_args()

    data = {}
    for trial, (prefix, cam) in TRIALS.items():
        try:
            data[trial] = build(args.features, trial, prefix, cam, args.feature)
        except (FileNotFoundError, ValueError) as e:
            print(f"跳过 {trial}: {e}")
    if len(data) < 3:
        sys.exit("需要 3 条 trial")

    names = list(data)
    print(f"特征：{args.feature}  维度 {data[names[0]][0].shape[1]}  "
          f"PCA-{args.pca}  ridge={args.ridge}")
    print(f"扫描 lag ∈ [{-args.max_lag:.0f}, {args.max_lag:.0f}] ms  "
          f"步长 {args.step:.0f} ms")
    print()

    lags = np.arange(-args.max_lag, args.max_lag + 1e-9, args.step)

    # ---- 功效分析：oracle 曲线 ----
    print("=== 功效分析：标签自一致性（oracle 上限）===")
    key = [33, 66, 100, 200, 500, 1000]
    orc = oracle_curve(data, key)
    base_res = 0.53
    print(f"{'lag(ms)':>8s}{'标签RMSE':>10s}{'理论涨幅':>10s}")
    for i, L in enumerate(key):
        v = np.nanmean([orc[n][i] for n in names])
        infl = (np.sqrt(base_res ** 2 + v ** 2) / base_res - 1) * 100
        print(f"{L:8d}{v:10.3f}{infl:9.1f}%")
    print()

    # ---- 主扫描 ----
    print("=== lag 扫描（留一 trial 交叉验证）===")
    curves = {n: [] for n in names}
    for L in lags:
        r = loo_rmse(data, L / 1000.0, args.pca, args.ridge)
        for n in names:
            curves[n].append(r[n])
    for n in names:
        curves[n] = np.array(curves[n])

    print(f"{'lag(ms)':>8s}", end="")
    for n in names:
        print(f"{n[-8:]:>10s}", end="")
    print(f"{'平均':>9s}")
    mean_c = np.nanmean([curves[n] for n in names], axis=0)
    # 均匀取样约 12 行，保证任意扫描范围/步长都能显示
    show = sorted(set(np.linspace(0, len(lags) - 1, 13).round().astype(int)))
    for i in show:
        print(f"{lags[i]:8.0f}", end="")
        for n in names:
            print(f"{curves[n][i]:10.3f}", end="")
        print(f"{mean_c[i]:9.3f}")
    print()

    # ---- 三层判据 ----
    print("=== 三层判据 ===")
    i0 = int(np.argmin(np.abs(lags)))
    edge = np.nanmean([mean_c[0], mean_c[-1]])
    rise = (edge / mean_c[i0] - 1) * 100
    l1 = rise > 30
    print(f"L1 曲线有明确极值：边缘/中心 = {edge:.3f}/{mean_c[i0]:.3f} "
          f"= +{rise:.1f}%   {'通过' if l1 else '不通过 ✗'}")

    opt = {n: lags[int(np.nanargmin(curves[n]))] for n in names}
    # 只统计明显偏离 0 的（超过 1 个相机帧 33ms），避免把 ±2ms 当方向
    signs = {np.sign(v) for v in opt.values() if abs(v) > 33}
    l2 = len(signs) <= 1
    print(f"L2 最优 lag 一致性：" +
          "  ".join(f"{n[-8:]}={opt[n]:+.0f}ms" for n in names) +
          f"   {'一致' if l2 else '不一致 ✗'}")

    opt_mean = lags[int(np.nanargmin(mean_c))]
    l3 = abs(opt_mean) > 66 and l2
    print(f"L3 极值显著偏 0：平均曲线 lag* = {opt_mean:+.0f} ms   "
          f"{'显著偏移' if l3 else '接近 0'}")
    print()

    # ---- 零对照 ----
    print("=== 零对照：CSI 特征打乱 ===")
    rng = np.random.default_rng(args.seed)
    zc = []
    for L in lags:
        r = loo_rmse(data, L / 1000.0, args.pca, args.ridge, shuffle_rng=rng)
        zc.append(np.nanmean([r[n] for n in names]))
    zc = np.array(zc)
    zopt = lags[int(np.nanargmin(zc))]
    zrise = (np.nanmean([zc[0], zc[-1]]) / zc[int(np.argmin(np.abs(lags)))] - 1) * 100
    print(f"  打乱后 lag* = {zopt:+.0f} ms，边缘涨幅 {zrise:+.1f}%")
    leak = abs(zrise) > 15
    if leak:
        print("  ⚠ 打乱后仍有曲率，极值可能来自标签自相关而非对齐")
    else:
        print("  ✓ 打乱后曲线平坦，主扫描的曲率来自真实对齐")
    print()

    # ---- 结论 ----
    print("=== 结论 ===")
    if not l1:
        print("  曲线无明确极值 -> **本实验在当前数据上无统计功效**")
        print("  不能据此判断对齐是否最优。诚实报告，不下结论。")
        print("  原因见上方功效分析：±33/66ms 的理论效应远小于 trial 间离散度。")
    elif l3:
        print(f"  存在系统性时间偏移 lag* = {opt_mean:+.0f} ms")
        print("  -> 值得修正对齐，M0-M3 有潜在收益")
    else:
        print("  最优 lag 接近 0，当前对齐已近最优")
        print("  -> **M0-M3 收益上限有限，建议暂缓**")
    return 0


if __name__ == "__main__":
    sys.exit(main())
