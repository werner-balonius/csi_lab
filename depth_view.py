#!/usr/bin/env python3
"""depth_view.py — 把 csi_cam.py 采的 depth.bin 渲染成 PNG，用于确认相机朝向与视野。

只依赖 numpy（Mac 端 ~/csienv 已有），不需要 OpenCV / ffmpeg。
PNG 用 zlib 手写，避免再引入图像库。

  ./depth_view.py <depth.bin> [--frame N] [--out out.png] [--width 640] [--height 360]
  ./depth_view.py <depth.bin> --grid              # 抽 6 帧拼一张，看全程
  ./depth_view.py <depth.bin> --profile           # 只打印距离分布，不出图
"""
import argparse
import os
import struct
import sys
import zlib

import numpy as np

W, H = 640, 360


def read_frame(path, idx, w, h):
    fr = w * h
    n = os.path.getsize(path) // (fr * 2)
    if idx < 0:
        idx += n
    if not (0 <= idx < n):
        sys.exit(f"帧号 {idx} 超出范围，文件共 {n} 帧")
    with open(path, "rb") as f:
        f.seek(idx * fr * 2)
        return np.frombuffer(f.read(fr * 2), dtype=np.uint16).reshape(h, w), n


def colorize(d, lo, hi):
    """深度 -> RGB。近处暖色，远处冷色，无效值黑色。turbo 风格的简化版。"""
    valid = d > 0
    x = np.zeros(d.shape, dtype=np.float64)
    x[valid] = np.clip((d[valid].astype(np.float64) - lo) / max(hi - lo, 1), 0, 1)

    # 分段线性调色板：红->黄->绿->青->蓝
    stops = np.array([
        [180, 0, 0],
        [255, 200, 0],
        [0, 200, 60],
        [0, 200, 220],
        [30, 40, 160],
    ], dtype=np.float64)
    pos = x * (len(stops) - 1)
    i0 = np.floor(pos).astype(int)
    i0 = np.clip(i0, 0, len(stops) - 2)
    t = (pos - i0)[..., None]
    rgb = stops[i0] * (1 - t) + stops[i0 + 1] * t

    rgb[~valid] = 0
    return rgb.astype(np.uint8)


def draw_marks(img, w, h):
    """画十字准心与三分线，便于判断相机朝向是否居中。"""
    cx, cy = w // 2, h // 2
    img[cy, :, :] = np.maximum(img[cy, :, :], 90)
    img[:, cx, :] = np.maximum(img[:, cx, :], 90)
    for f in (1, 2):
        img[:, w * f // 3, :] = np.maximum(img[:, w * f // 3, :], 55)
        img[h * f // 3, :, :] = np.maximum(img[h * f // 3, :, :], 55)
    return img


def write_png(path, rgb):
    h, w, _ = rgb.shape
    raw = b"".join(b"\x00" + rgb[y].tobytes() for y in range(h))

    def chunk(tag, data):
        c = struct.pack(">I", len(data)) + tag + data
        return c + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    png = (b"\x89PNG\r\n\x1a\n"
           + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(raw, 6))
           + chunk(b"IEND", b""))
    with open(path, "wb") as f:
        f.write(png)


def profile(d, tag=""):
    v = d[d > 0]
    if not len(v):
        print(f"  {tag} 全帧无有效深度")
        return
    pct = [np.percentile(v, p) for p in (5, 25, 50, 75, 95)]
    print(f"  {tag}有效 {len(v) / d.size * 100:5.1f}%  "
          f"范围 {v.min() / 1000:.2f}-{v.max() / 1000:.2f} m  "
          f"分位(5/25/50/75/95) " + "/".join(f"{p / 1000:.2f}" for p in pct) + " m")
    # 按距离分档，看人可能在哪一层
    bins = [(0, 1), (1, 2), (2, 3), (3, 4), (4, 5), (5, 7), (7, 10), (10, 99)]
    parts = []
    for a, b in bins:
        n = ((v >= a * 1000) & (v < b * 1000)).sum()
        if n / len(v) > 0.01:
            parts.append(f"{a}-{b}m:{n / len(v) * 100:.0f}%")
    print(f"        分布 {'  '.join(parts)}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("path")
    ap.add_argument("--frame", type=int, default=None)
    ap.add_argument("--out", default=None)
    ap.add_argument("--width", type=int, default=W)
    ap.add_argument("--height", type=int, default=H)
    ap.add_argument("--lo", type=float, default=None, help="调色下限（米）")
    ap.add_argument("--hi", type=float, default=None, help="调色上限（米）")
    ap.add_argument("--grid", action="store_true", help="抽 6 帧拼图")
    ap.add_argument("--profile", action="store_true", help="只打印距离分布")
    a = ap.parse_args()

    w, h = a.width, a.height
    total = os.path.getsize(a.path) // (w * h * 2)
    print(f"  文件 {os.path.basename(a.path)}  共 {total} 帧  {w}x{h}")

    if a.profile:
        for i in [0, total // 4, total // 2, total * 3 // 4, total - 1]:
            d, _ = read_frame(a.path, i, w, h)
            profile(d, f"帧{i:4d} ")
        return

    if a.grid:
        idxs = [int(total * k / 6) for k in range(6)]
        frames = [read_frame(a.path, i, w, h)[0] for i in idxs]
        allv = np.concatenate([f[f > 0] for f in frames])
        lo = a.lo * 1000 if a.lo else np.percentile(allv, 2)
        hi = a.hi * 1000 if a.hi else np.percentile(allv, 98)
        canvas = np.zeros((h * 2 + 12, w * 3 + 16, 3), dtype=np.uint8)
        canvas[:] = 25
        for k, (i, d) in enumerate(zip(idxs, frames)):
            r, c = divmod(k, 3)
            img = draw_marks(colorize(d, lo, hi), w, h)
            y0 = r * (h + 8) + 4
            x0 = c * (w + 4) + 4
            canvas[y0:y0 + h, x0:x0 + w] = img
            profile(d, f"帧{i:4d} ")
        out = a.out or os.path.splitext(a.path)[0] + "_grid.png"
        write_png(out, canvas)
        print(f"  调色范围 {lo / 1000:.2f}-{hi / 1000:.2f} m")
        print(f"  已写出 {out}")
        return

    idx = a.frame if a.frame is not None else total // 2
    d, _ = read_frame(a.path, idx, w, h)
    profile(d, f"帧{idx} ")
    v = d[d > 0]
    lo = a.lo * 1000 if a.lo else np.percentile(v, 2)
    hi = a.hi * 1000 if a.hi else np.percentile(v, 98)
    img = draw_marks(colorize(d, lo, hi), w, h)
    out = a.out or os.path.splitext(a.path)[0] + f"_f{idx}.png"
    write_png(out, img)
    print(f"  调色范围 {lo / 1000:.2f}-{hi / 1000:.2f} m")
    print(f"  已写出 {out}")


if __name__ == "__main__":
    main()
