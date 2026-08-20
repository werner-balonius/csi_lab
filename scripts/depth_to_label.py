#!/usr/bin/env python3
"""从 OAK-D 深度序列提取人体躯干 (X, Y, Z) 坐标。

产出相机坐标系下的躯干中心轨迹，是 CSI 监督学习所需的标签。
详见 docs/experiments/20260820_depth_label_feasibility.md。

方法：
  1. 用时间中位数构建静态背景模型
  2. 前景 = 比背景近 FG_THRESH_MM 以上的像素
  3. 取最大连通域（避免噪声与多目标干扰）
  4. 用相机内参反投影到真实三维坐标
  5. 躯干 = 该连通域高度方向的中段（排除头脚）

依赖：numpy + scipy。不需要 PicoScenes toolbox 或 depthai。

用法：
    python3 depth_to_label.py CAM_DIR [-o out.csv] [--stride 1]
"""
import argparse
import glob
import json
import os
import sys

import numpy as np

FG_THRESH_MM = 300.0     # 前景判定：比背景近多少
MIN_BLOB_PX = 800        # 最小连通域面积（保留弱检出供诊断）
PERSON_BLOB_PX = 10000   # 人体判定阈值：实测空场 3%，有人 34-37%
DEPTH_MIN_MM = 500.0
DEPTH_MAX_MM = 8000.0
TORSO_LO, TORSO_HI = 0.25, 0.70   # 躯干在人体高度方向的取段（0=顶部）


def load_meta(cam_dir):
    hits = glob.glob(os.path.join(cam_dir, "*_cam.json"))
    if not hits:
        raise FileNotFoundError(f"{cam_dir} 下没有 *_cam.json")
    with open(hits[0]) as fh:
        return json.load(fh), hits[0]


def open_depth(cam_dir, meta):
    d = meta["depth"]
    path = os.path.join(cam_dir, os.path.basename(d["file"]))
    if not os.path.exists(path):
        raise FileNotFoundError(path)
    dtype = "<u2" if d.get("byte_order", "little") == "little" else ">u2"
    mm = np.memmap(path, dtype=dtype, mode="r")
    return mm.reshape(-1, d["height"], d["width"])


def build_background(vol, n_sample=150):
    """时间中位数背景。人只在少数帧占据少数像素，中位数对其鲁棒。"""
    step = max(1, len(vol) // n_sample)
    sub = np.array(vol[::step], dtype=np.float32)
    sub[sub == 0] = np.nan
    return np.nanmedian(sub, axis=0)


def largest_blob(mask):
    """最大连通域。优先用 scipy，缺失时退化为全掩码。"""
    try:
        from scipy import ndimage
    except ImportError:
        return mask
    lab, n = ndimage.label(mask)
    if n == 0:
        return np.zeros_like(mask)
    sizes = ndimage.sum(mask, lab, range(1, n + 1))
    return lab == (int(np.argmax(sizes)) + 1)


def extract(cam_dir, stride=1):
    meta, meta_path = load_meta(cam_dir)
    vol = open_depth(cam_dir, meta)
    cal = meta["calibration"]
    fx, fy, cx, cy = cal["fx"], cal["fy"], cal["cx"], cal["cy"]
    ts = meta["depth"].get("timestamp_ns_monotonic")

    bg = build_background(vol)
    rows = []
    for i in range(0, len(vol), stride):
        f = np.array(vol[i], dtype=np.float32)
        f[f == 0] = np.nan
        mask = (bg - f > FG_THRESH_MM) & (f > DEPTH_MIN_MM) & (f < DEPTH_MAX_MM)
        mask = np.nan_to_num(mask, nan=False).astype(bool)
        if mask.sum() < MIN_BLOB_PX:
            rows.append((i, ts[i] if ts else -1, 0, *[np.nan] * 4))
            continue
        blob = largest_blob(mask)
        if blob.sum() < MIN_BLOB_PX:
            rows.append((i, ts[i] if ts else -1, 0, *[np.nan] * 4))
            continue

        ys, xs = np.nonzero(blob)
        # 躯干：沿图像纵向取中段，排除头顶与足部
        lo, hi = np.quantile(ys, [TORSO_LO, TORSO_HI])
        sel = (ys >= lo) & (ys <= hi)
        if sel.sum() < MIN_BLOB_PX // 4:
            sel = np.ones_like(ys, dtype=bool)
        ys_t, xs_t = ys[sel], xs[sel]
        z = np.nanmedian(f[ys_t, xs_t])           # mm
        # 针孔反投影
        X = np.nanmedian((xs_t - cx) * z / fx)
        Y = np.nanmedian((ys_t - cy) * z / fy)
        rows.append((i, ts[i] if ts else -1, int(blob.sum()),
                     X / 1000.0, Y / 1000.0, z / 1000.0, float(blob.sum()) / blob.size))
    return rows, meta, meta_path


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cam_dir")
    ap.add_argument("-o", "--out", default=None)
    ap.add_argument("--stride", type=int, default=1)
    args = ap.parse_args()

    rows, meta, _ = extract(args.cam_dir, args.stride)
    detected = [r for r in rows if r[2] > 0]
    person = [r for r in rows if r[2] >= PERSON_BLOB_PX]
    ratio = len(person) / max(1, len(rows)) * 100
    print(f"帧数 {len(rows)}   前景检出 {len(detected)} ({len(detected)/max(1,len(rows))*100:.1f}%)")
    print(f"人体帧 (blob>={PERSON_BLOB_PX}px): {len(person)} ({ratio:.1f}%)")
    if person:
        Z = np.array([r[5] for r in person])
        X = np.array([r[3] for r in person])
        print(f"  X {X.min():+.2f} ~ {X.max():+.2f} m   Z 中位 {np.median(Z):.2f} m")
        # 运动学检查
        fr = np.array([r[0] for r in person], dtype=float)
        dt = np.diff(fr) / 30.0
        ok = dt < 0.5
        if ok.sum() > 3:
            spd = np.hypot(np.diff(X), np.diff(Z))[ok] / dt[ok]
            print(f"  速度 中位 {np.median(spd):.2f} m/s  >3m/s 跳变 {(spd>3).sum()}/{len(spd)}")
    print("  参考：空场约 3%，有人 34-37%（8-11 实测）")

    out = args.out or os.path.join(args.cam_dir, "torso_track.csv")
    with open(out, "w") as fh:
        fh.write("frame,timestamp_ns,blob_px,X_m,Y_m,Z_m,blob_ratio\n")
        for r in rows:
            fh.write("%d,%d,%d,%.4f,%.4f,%.4f,%.5f\n" % r)
    print(f"已写出 {out}")


if __name__ == "__main__":
    main()
