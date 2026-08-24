#!/usr/bin/env python3
"""空场基线稳定性检查 —— 判据实验的开跑门槛。

为什么需要这个脚本
------------------
判据实验比较的是不同站位之间的频谱差异，量级可能只有几个百分点。
若空场（无人）时基线本身就在漂移，漂移会淹没信号，
采完 21 条也无法解释结果。

**必须在正式采集前跑通，不通过就不要开跑。**

实测参考值（2026-08-20 / 08-24）
--------------------------------
    同次开机 1m 链路零基线      0.22%   ← 理想
    同次开机 5m 链路零基线      2.68%
    跨重启 node3               12.77%  ← 不可用
    移动节点后未收敛（8-24）   13.02%  ← 不可用

判据
----
    < 1%    通过，可以开跑
    1-2%    边缘，建议再等一轮预热
    > 2%    不通过，须排查

诊断维度
--------
本脚本不只给一个数，还会区分**漂移**与**噪声**：

    trial 间 L2      两条空场之间的差异
    trial 内前后半   单条 trial 内部是否已经在变
    分段单调性       4 等分后相对第 1 段的差异是否单调递增

若 trial 内部就不稳，说明 RF 未收敛或场地有活动，
此时 trial 间的数值没有参考意义。

用法
----
    python3 scripts/baseline_check.py <trial_dir_1> <trial_dir_2> [...]

    # 例：
    python3 scripts/baseline_check.py ~/csi_results/*g_baseline_probe_*

注意
----
使用**归一化频谱**（每天线除以自身均值），以消除 AGC 宽带增益。
不使用 |H| 绝对幅度——AX210 的 AGC 会补偿约 13.88 dB，
使绝对幅度失去物理意义（见 docs/experiments/20260820_link_integrity.md）。
"""
import argparse
import glob
import json
import os
import sys

import numpy as np

PASS = 1.0      # %
MARGINAL = 2.0  # %


def load_amp(trial_dir, node):
    """读对齐后的 CSI 幅度，返回 (N, 234, n_ant)。"""
    p = os.path.join(trial_dir, "aligned", "aligned_csi.npz")
    if not os.path.exists(p):
        raise FileNotFoundError(f"缺 {p}（该 trial 未完成对齐？）")
    c = np.load(p)[f"{node}_csi_data"]
    return np.abs(c[:, :, :, 0]).astype(np.float64)


def norm_spec(a):
    """时间平均后按天线归一化，消除 AGC 宽带增益。"""
    s = a.mean(axis=0)
    return s / (s.mean(axis=0, keepdims=True) + 1e-12)


def rel_l2(x, y):
    """相对 L2 差异（%）。"""
    mid = (x + y) / 2.0
    return float(np.linalg.norm(x - y) / (np.linalg.norm(mid) + 1e-12) * 100.0)


def verdict(v):
    if v < PASS:
        return "通过"
    if v < MARGINAL:
        return "边缘"
    return "不通过"


def check_within(a, label, indent="    "):
    """单条 trial 内部稳定性。"""
    n = len(a) // 2
    half = rel_l2(norm_spec(a[:n]).ravel(), norm_spec(a[n:]).ravel())
    q = len(a) // 4
    segs = [norm_spec(a[k * q:(k + 1) * q]).ravel() for k in range(4)]
    ds = [rel_l2(segs[0], s) for s in segs]
    mono = all(ds[i] <= ds[i + 1] + 0.5 for i in range(3))
    print(f"{indent}{label}  前后半 {half:6.3f}%   "
          f"分段 " + " ".join(f"{v:5.2f}%" for v in ds) +
          ("   单调漂移 ⚠" if mono and ds[-1] > 1.0 else ""))
    return half


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("trials", nargs="+", help="空场 trial 目录（≥2 个）")
    ap.add_argument("--nodes", default="node1,node3")
    args = ap.parse_args()

    dirs = []
    for t in args.trials:
        dirs.extend(sorted(glob.glob(os.path.expanduser(t))))
    dirs = [d for d in dirs if os.path.isdir(d)]
    if len(dirs) < 2:
        sys.exit("至少需要 2 个空场 trial 目录")

    nodes = args.nodes.split(",")
    print("=" * 66)
    print("空场基线稳定性检查")
    print("=" * 66)
    for d in dirs:
        print(f"  {os.path.basename(d)}")
    print()

    worst = 0.0
    for node in nodes:
        print(f"--- {node} ---")
        try:
            amps = [load_amp(d, node) for d in dirs]
        except (FileNotFoundError, KeyError) as e:
            print(f"    跳过：{e}")
            continue

        print("  trial 内稳定性（若此项就大，trial 间数值无意义）：")
        for d, a in zip(dirs, amps):
            check_within(a, os.path.basename(d)[9:24].ljust(18))

        print("  trial 间差异：")
        specs = [norm_spec(a) for a in amps]
        n_ant = specs[0].shape[1]
        for i in range(len(specs)):
            for j in range(i + 1, len(specs)):
                for ant in range(n_ant):
                    v = rel_l2(specs[i][:, ant], specs[j][:, ant])
                    print(f"    #{i+1}vs#{j+1} ant{ant}: {v:7.3f}%   {verdict(v)}")
                v = rel_l2(specs[i].ravel(), specs[j].ravel())
                worst = max(worst, v)
                print(f"    #{i+1}vs#{j+1} 合计 : {v:7.3f}%   {verdict(v)}")
        print()

    print("=" * 66)
    print(f"最差 trial 间差异：{worst:.3f}%  ->  {verdict(worst)}")
    print()
    print("参考：同次开机 1m 链路 0.22% | 5m 链路 2.68%")
    print("      跨重启 12.77% | 移动节点未收敛 13.02%")
    print()
    if worst < PASS:
        print("结论：基线稳定，可以开始正式采集 ✓")
        return 0
    if worst < MARGINAL:
        print("结论：边缘。建议再预热 20-30 分钟后重测。")
        return 1
    print("结论：不通过，不要开跑 ✗")
    print()
    print("排查顺序：")
    print("  1. 是否有节点刚重启或刚被移动？-> 静置 20-30 分钟后重测")
    print("  2. trial 内部是否也在漂？-> 是则为 RF 未收敛或场地有活动")
    print("  3. 场地内是否有人走动、风扇/空调等周期性运动物体？")
    print("  4. 是否有其他 2.4GHz 设备干扰？")
    return 2


if __name__ == "__main__":
    sys.exit(main())
