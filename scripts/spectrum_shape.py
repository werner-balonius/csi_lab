#!/usr/bin/env python3
"""归一化频谱形状分析：对 AGC 免疫的遮挡/存在性特征。

背景见 docs/experiments/20260820_link_integrity.md。

要点：
  1. AX210 的 AGC 会补偿遮挡造成的功率损失（实测 +13.88 dB），
     因此 |H| 的绝对幅度不可用作遮挡特征。
  2. AGC 是宽带增益，不改变频谱形状。归一化后（除以自身均值）即可消除其影响。
  3. CSI 时序高度自相关，置换检验会给出虚假显著性
     （空场对照实测 p=0.0000）。零分布必须用空场数据跑同一程序获得。

用法：
    python3 spectrum_shape.py TRIAL [TRIAL ...] [--empty EMPTY ...]

支持两种输入：
    - Mac 侧 aligned/aligned_csi.npz  （键 node1_csi_data，已剔导频）
    - 节点侧 *_target_csi.npz         （键 target_csi，含 242 子载波，需剔导频）
"""
import argparse
import glob
import os
import sys

import numpy as np

PILOT_IDX = [6, 32, 74, 100, 141, 167, 209, 235]


def load_amplitude(path, node="node1"):
    """返回 (N, n_sub, n_rx, n_tx) 幅度数组，导频已剔除。"""
    aligned = os.path.join(path, "aligned", "aligned_csi.npz")
    if os.path.exists(aligned):
        d = np.load(aligned)
        key = f"{node}_csi_data"
        if key not in d.files:
            raise KeyError(f"{aligned} 中没有 {key}")
        return np.abs(d[key])

    hits = glob.glob(os.path.join(path, f"{node}_*_target_csi.npz"))
    if not hits:
        raise FileNotFoundError(f"{path} 下找不到 aligned_csi.npz 或 {node}_*_target_csi.npz")
    amp = np.abs(np.load(hits[0])["target_csi"])
    if amp.shape[1] == 242:
        mask = np.ones(242, dtype=bool)
        mask[PILOT_IDX] = False
        amp = amp[:, mask]
    return amp


def normalized_spectrum(amp):
    """逐子载波平均后归一化。AGC 的宽带增益在此步被消除。"""
    s = amp.mean(axis=tuple(range(amp.ndim))[1:] if amp.ndim > 1 else 0)
    if amp.ndim > 2:
        s = amp.mean(axis=(0,) + tuple(range(2, amp.ndim)))
    return s / s.mean()


def windows(amp, n_win):
    w = max(1, amp.shape[0] // n_win)
    return np.array([normalized_spectrum(amp[i:i + w])
                     for i in range(0, amp.shape[0] - w + 1, w)])


def l2(a, b):
    """归一化频谱间的 L2 距离，以百分比表示。"""
    return float(np.linalg.norm(a - b) / np.sqrt(len(a)) * 100)


def variation_energy(seq):
    """时序变化能量：各窗与全程均值的平均 L2 偏差。

    不依赖切点、不依赖协议标注，是最稳健的单 trial 指标。
    """
    ref = seq.mean(axis=0)
    return float(np.mean([l2(s, ref) for s in seq]))


def best_split(seq, margin=5):
    """全扫描最优切点及其效应量。

    注意：这是"数据挖掘"式指标，空场上也能挖出非零值。
    必须与空场对照比较才有意义。
    """
    best_val, best_k = 0.0, 0
    for k in range(margin, len(seq) - margin):
        v = l2(seq[:k].mean(axis=0), seq[k:].mean(axis=0))
        if v > best_val:
            best_val, best_k = v, k
    return best_val, best_k


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("trials", nargs="+", help="待分析 trial 目录")
    ap.add_argument("--empty", nargs="*", default=[],
                    help="空场 trial 目录，用作零分布")
    ap.add_argument("--nodes", nargs="*", default=["node1", "node3"])
    ap.add_argument("--windows", type=int, default=45, help="分窗数（默认 45，约 1s/窗）")
    args = ap.parse_args()

    null = {}
    if args.empty:
        print("=== 空场零分布 ===")
        for node in args.nodes:
            vals, splits = [], []
            for t in args.empty:
                try:
                    seq = windows(load_amplitude(t, node), args.windows)
                except (FileNotFoundError, KeyError) as exc:
                    print(f"  跳过 {os.path.basename(t)} [{node}]: {exc}", file=sys.stderr)
                    continue
                vals.append(variation_energy(seq))
                splits.append(best_split(seq)[0])
            if vals:
                null[node] = (float(np.mean(vals)), float(np.mean(splits)))
                print(f"  {node}: 变化能量 {null[node][0]:.2f}%  "
                      f"最优切点效应 {null[node][1]:.2f}%  (n={len(vals)})")
        print()

    print("=== 待测 trial ===")
    header = f"  {'trial':<38s}" + "".join(f"{n:>10s}" for n in args.nodes)
    print(header)
    for t in args.trials:
        cells = []
        for node in args.nodes:
            try:
                seq = windows(load_amplitude(t, node), args.windows)
            except (FileNotFoundError, KeyError):
                cells.append("      n/a")
                continue
            ve = variation_energy(seq)
            if node in null and null[node][0] > 0:
                cells.append(f"{ve:7.2f}% {ve / null[node][0]:.1f}x")
            else:
                cells.append(f"{ve:9.2f}%")
        print(f"  {os.path.basename(t.rstrip('/'))[:38]:<38s}" + "".join(f"{c:>10s}" for c in cells))

    if null:
        print()
        print("  倍数 = 相对空场变化能量。经验判据：>2.5x 为有效响应。")
    print()
    print("  警告：不要对此类数据使用置换检验（时序自相关会造成虚假显著）。")


if __name__ == "__main__":
    main()
