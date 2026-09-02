#!/usr/bin/env python3
"""用深度流量化每条 motion trial 的实际肢体运动幅度（米制）。

--------------------------------------------------------------------------
为什么必须用米制而不是像素
--------------------------------------------------------------------------
相机固定在 node3 上。4 m 等边三角形中，M3 距相机 2.00 m，M1 距相机 3.46 m
（中线长 = (√3/2)·4）。人在 M1 时成像面积约为 M3 的 (2.00/3.46)² = 0.33 倍，
深度噪声也随距离增大。任何以像素或原始深度差为单位的幅度指标都会把这个
成像几何差异误判为运动幅度差异。

因此本脚本一律经针孔反投影换算到米：
    宽度_m = (x_max - x_min) · z / fx

--------------------------------------------------------------------------
指标
--------------------------------------------------------------------------
  width_std_m   前景轮廓米制宽度的时间标准差 —— 摆臂幅度的直接代理
  width_p95_m   宽度的 95 分位减中位数 —— 最大展臂幅度
  area_std_frac 轮廓米制面积的变异系数 —— 整体动作幅度
  depth_rms_mm  前景内相邻帧深度变化的 RMS —— 深度域运动能量
                （随距离增大而噪声上升，仅作参考，不用于跨站位比较）

--------------------------------------------------------------------------
窗口
--------------------------------------------------------------------------
只取事件平台 13-21 s，与 CSI 侧口径一致；帧号由 aligned_csi.npz 的
depth_frame_index 与 system_ns 换算，不用 fps 外推。

用法:
    ~/csienv/bin/python scripts/depth_motion_amplitude.py --results ~/csi_results
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys

import numpy as np

FG_THRESH_MM = 200.0
DEPTH_MIN_MM, DEPTH_MAX_MM = 500.0, 6000.0
MIN_BLOB_PX = 300
EV = (13.0, 21.0)
# 人体合理性范围（米制）。超出即判为掩码未框住人，该帧丢弃。
# 初版遗漏最大连通域步骤，噪声散布全图，M1 处量到 8.3 m「轮廓宽」，
# 且「摆臂幅度比 2.21×」与「距相机比 2.30×」几乎相等 —— 那只是掩码
# 被距离 z 线性放大的产物。
PERSON_W_M = (0.25, 1.60)
PERSON_H_M = (1.00, 2.30)


def largest_blob(mask):
    from scipy import ndimage
    lab, n = ndimage.label(mask)
    if n == 0:
        return np.zeros_like(mask)
    sizes = ndimage.sum(mask, lab, range(1, n + 1))
    return lab == (int(np.argmax(sizes)) + 1)


def open_depth(d):
    hits = glob.glob(os.path.join(d, "*_cam.json"))
    if not hits:
        return None, None
    meta = json.load(open(hits[0]))
    dd = meta["depth"]
    path = os.path.join(d, os.path.basename(dd["file"]))
    if not os.path.exists(path):
        return None, None
    dt = "<u2" if dd.get("byte_order", "little") == "little" else ">u2"
    vol = np.memmap(path, dtype=dt, mode="r").reshape(-1, dd["height"], dd["width"])
    return vol, meta["calibration"]


def event_frames(d):
    """由 aligned_csi.npz 求事件窗对应的深度帧号，不用 fps 外推。"""
    p = os.path.join(d, "aligned", "aligned_csi.npz")
    with np.load(p, allow_pickle=False) as z:
        if "depth_frame_index" not in z.files:
            return None
        fi = z["depth_frame_index"].astype(np.int64)
        ns = z["node3_system_ns"].astype(np.int64).reshape(-1)
    n = min(len(fi), len(ns))
    t = (ns[:n] - ns[0]) / 1e9
    sel = (t >= EV[0]) & (t < EV[1]) & (fi[:n] >= 0)
    return np.unique(fi[:n][sel])


def analyse(d):
    vol, cal = open_depth(d)
    if vol is None:
        return None
    frames = event_frames(d)
    if frames is None or len(frames) < 60:
        return None
    frames = frames[frames < len(vol)]
    fx, fy, cx, cy = cal["fx"], cal["fy"], cal["cx"], cal["cy"]

    # 背景：全片时间中位数，人只占少数帧的少数像素
    step = max(1, len(vol) // 120)
    sub = np.array(vol[::step], dtype=np.float32)
    sub[sub == 0] = np.nan
    bg = np.nanmedian(sub, axis=0)

    widths, areas, zs, prev, dz = [], [], [], None, []
    rejected = [0]
    for i in frames:
        f = np.array(vol[i], dtype=np.float32)
        f[f == 0] = np.nan
        mask = (bg - f > FG_THRESH_MM) & (f > DEPTH_MIN_MM) & (f < DEPTH_MAX_MM)
        mask = np.nan_to_num(mask, nan=False).astype(bool)
        if mask.sum() < MIN_BLOB_PX:
            prev = None
            continue
        mask = largest_blob(mask)
        if mask.sum() < MIN_BLOB_PX:
            prev = None
            continue
        ys, xs = np.nonzero(mask)
        z = float(np.nanmedian(f[ys, xs]))
        w_m = (xs.max() - xs.min()) * z / fx / 1000.0
        h_m = (ys.max() - ys.min()) * z / fy / 1000.0
        if not (PERSON_W_M[0] <= w_m <= PERSON_W_M[1]
                and PERSON_H_M[0] <= h_m <= PERSON_H_M[1]):
            rejected[0] += 1
            prev = None
            continue
        widths.append(w_m)
        areas.append(mask.sum() * (z / fx) * (z / fy) / 1e6)
        zs.append(z / 1000.0)
        if prev is not None:
            common = mask & prev[1]
            if common.sum() > MIN_BLOB_PX:
                dz.append(float(np.sqrt(np.nanmean((f[common] - prev[0][common]) ** 2))))
        prev = (f, mask)

    if len(widths) < 40:
        return None
    w, a = np.array(widths), np.array(areas)
    return dict(n_frames=len(w), rejected=rejected[0],
                rej_frac=rejected[0] / max(1, rejected[0] + len(w)),
                dist_m=float(np.median(zs)),
                width_med_m=float(np.median(w)),
                width_std_m=float(np.std(w)),
                width_p95_m=float(np.percentile(w, 95) - np.median(w)),
                area_med_m2=float(np.median(a)),
                area_std_frac=float(np.std(a) / (np.median(a) + 1e-9)),
                depth_rms_mm=float(np.median(dz)) if dz else float("nan"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", default=os.path.expanduser("~/csi_results"))
    args = ap.parse_args()

    ds = sorted(d for d in glob.glob(os.path.join(args.results, "2026090*eq4m_posM[13]_motion_r*")))
    print(f"motion trial {len(ds)} 条\n")
    print(f"  {'trial':30s} {'站位':4s} {'帧':>4s} {'弃%':>5s} {'距相机m':>7s} {'轮廓宽m':>7s} "
          f"{'宽std_m':>8s} {'宽p95_m':>8s} {'面积m2':>7s} {'面积CV':>7s}")
    rows = []
    for d in ds:
        b = os.path.basename(d)
        pos = "M1" if "posM1" in b else "M3"
        r = analyse(d)
        if r is None:
            print(f"  {b.split('1t2r_cam_')[-1]:30s} {pos}   跳过（深度不足）")
            continue
        rows.append((pos, r))
        print(f"  {b.split('1t2r_cam_')[-1]:30s} {pos:4s} {r['n_frames']:4d} "
              f"{r['rej_frac']*100:5.1f} {r['dist_m']:7.2f} "
              f"{r['width_med_m']:7.3f} {r['width_std_m']:8.4f} {r['width_p95_m']:8.4f} "
              f"{r['area_med_m2']:7.3f} {r['area_std_frac']:7.4f}")

    print("\n=== 按站位汇总（幅度是否存在系统差异） ===")
    for key, label, unit in (("width_std_m", "摆臂幅度(宽度时间std)", "m"),
                             ("width_p95_m", "最大展臂(p95-中位)", "m"),
                             ("area_std_frac", "整体动作幅度(面积CV)", ""),
                             ("dist_m", "距相机(几何校验)", "m")):
        a = np.array([r[key] for p, r in rows if p == "M1"])
        b = np.array([r[key] for p, r in rows if p == "M3"])
        if len(a) < 2 or len(b) < 2:
            continue
        ov = not (a.min() > b.max() or b.min() > a.max())
        print(f"  {label:24s} M1 {np.median(a):7.4f}{unit}  M3 {np.median(b):7.4f}{unit}  "
              f"比 {np.median(a)/(np.median(b)+1e-12):5.2f}×  "
              f"区间{'重叠' if ov else '不重叠 ←'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
