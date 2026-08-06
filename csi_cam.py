#!/usr/bin/env python3
"""OAK-D 深度/彩色采集，供 CSI 实验作姿态真值。

时间戳设计（关键）
------------------
DepthAI 的 ImgFrame.getTimestamp() 位于 CLOCK_MONOTONIC 域；
PicoScenes RxSBasic(v>=4) 的 system_ns 位于 CLOCK_REALTIME 域（epoch 纳秒）。
两者不能直接比较。本脚本在采集前后各采样一次

    offset = CLOCK_REALTIME - CLOCK_MONOTONIC

并把每帧的 monotonic 时间戳原样保存。后处理时：

    epoch_ns = monotonic_ns + offset

采样两次是为了检测 NTP 步进/漂移；若首尾 offset 差异过大，本次数据的
时间基准不可信，元数据里会给出 offset_drift_ns 供下游判断。

深度与 RGB 对齐
---------------
StereoDepth 默认把输出降采样到 320x200，且 setOutputSize() 仅在深度对齐到
RGB 相机时才生效。这里显式 setDepthAlign(CAM_A) + setOutputSize(640,360)：

  * 640x360 恰为 1920x1080 的 1/3，深度像素 (u,v) 对应 RGB 的 (3u,3v)，
    无裁切、无形变，2D 关键点可直接查表取深度；
  * 若用 640x400（16:10）与 RGB 的 16:9 不匹配，对齐会引入裁切歧义。

安全边界（实测得出，不要越界）
------------------------------
  深度 640x360 对齐 + 彩色 1080p H.265  ->  稳定 30fps，约 15.0 MB/s
  深度 640x400 + 原始 1080p 彩色        ->  设备崩溃
  深度 640x400 + 4K H.265               ->  主机断电级复位（无日志硬重启）
相机在 USB2 下工作（UsbSpeed.HIGH），且仅由 USB 口供电，功耗余量很小。
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import time
from pathlib import Path

DEPTH_W, DEPTH_H = 640, 360
COLOR_W, COLOR_H = 1920, 1080
FPS = 30
KEYFRAME_EVERY_S = 1          # 每秒一个 IDR，使 H.265 流可从中间解码

_stop = False


def _on_signal(signum, _frame):
    global _stop
    _stop = True


def clock_offset_ns():
    """返回 (offset_ns, 采样不确定度_ns)。

    夹逼采样：monotonic -> realtime -> monotonic，取中点，
    不确定度即两次 monotonic 的间隔。
    """
    m1 = time.clock_gettime_ns(time.CLOCK_MONOTONIC)
    r = time.clock_gettime_ns(time.CLOCK_REALTIME)
    m2 = time.clock_gettime_ns(time.CLOCK_MONOTONIC)
    return r - (m1 + m2) // 2, m2 - m1


def td_to_ns(td):
    """depthai 返回 datetime.timedelta，转成整数纳秒。"""
    return (td.days * 86400 + td.seconds) * 1_000_000_000 + td.microseconds * 1000


def _read_calibration(device, dai, width, height):
    """读取与深度输出同尺寸的 RGB 内参及基线。

    深度已对齐到 CAM_A，故内参取 CAM_A 在该分辨率下的值：
        X = (u - cx) * Z / fx
        Y = (v - cy) * Z / fy
        Z = depth[v, u]  (毫米)
    读取失败不终止采集，仅在元数据中留下 error 字段——原始数据仍然有效，
    内参事后可以补测。
    """
    try:
        handler = device.readCalibration()
        matrix = handler.getCameraIntrinsics(
            dai.CameraBoardSocket.CAM_A, width, height
        )
        distortion = handler.getDistortionCoefficients(dai.CameraBoardSocket.CAM_A)
        result = {
            "aligned_to": "CAM_A",
            "width": width,
            "height": height,
            "fx": matrix[0][0],
            "fy": matrix[1][1],
            "cx": matrix[0][2],
            "cy": matrix[1][2],
            "intrinsic_matrix": [list(row) for row in matrix],
            "distortion": list(distortion),
            "depth_unit": "millimeter",
        }
        try:
            result["stereo_baseline_cm"] = handler.getBaselineDistance()
        except Exception:
            pass
        return result
    except Exception as exc:  # noqa: BLE001
        return {"error": f"{type(exc).__name__}: {exc}"}


def wait_device(dai, timeout: float = 25.0, settle: float = 0.5):
    """等待 OAK 设备出现在可用列表里。

    为什么必须等：每次打开设备都会上传固件，USB PID 从 2485（bootloader）
    变成 f63b（已启动）；进程退出后设备又要重新枚举回 2485。这个往返需要
    数秒，期间设备不可见。连续两次调用（例如预检紧接着采集）必然撞上，
    表现为"刚才还好好的，现在找不到设备"。
    """
    deadline = time.monotonic() + timeout
    attempt = 0
    while time.monotonic() < deadline:
        try:
            if dai.Device.getAllAvailableDevices():
                # 出现在列表里不等于立刻可以打开，再给一点稳定时间
                time.sleep(settle)
                return True
        except Exception:
            pass
        attempt += 1
        time.sleep(min(0.5 * attempt, 2.0))
    return False


def open_device(dai, timeout: float = 40.0):
    """打开设备，允许若干次重试。

    设备出现在 getAllAvailableDevices() 里不代表立刻能打开——固件上传与
    USB 重新枚举之间有一段窗口，此时打开会得到 "Couldn't open stream"。
    预检刚探测过设备时必然撞上，所以这里必须重试而不是一次成败。
    """
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        try:
            return dai.Device()
        except Exception as exc:  # noqa: BLE001
            last = exc
            time.sleep(1.5)
    raise RuntimeError(f"无法打开 OAK 设备（已重试至超时）: {last}")


def probe():
    """只检查设备可用性，不采集。供编排脚本预检调用。"""
    import depthai as dai

    if not wait_device(dai):
        print("CAM_OK=0")
        print("CAM_ERROR=未发现 OAK 设备（检查 USB 连接与 udev 规则）", file=sys.stderr)
        return 1
    device = open_device(dai)
    try:
        speed = str(device.getUsbSpeed())
        print("CAM_OK=1")
        print(f"CAM_NAME={device.getDeviceName()}")
        print(f"CAM_DEVICE_ID={device.getDeviceId()}")
        print(f"CAM_USB_SPEED={speed}")
        if "SUPER" not in speed.upper():
            print(f"CAM_WARN=USB 未协商到 SuperSpeed（当前 {speed}），"
                  f"必须守住 640x360 深度 + 1080p H.265 的配置边界")
    finally:
        device.close()
    return 0


def scan_h265(path: Path, max_bytes: int = 8 << 20):
    """扫描 H.265 起始码，确认流从开头就可解码。

    只解析 NAL 头，不做真正解码——目的是在采集当场就发现"流不可解"，
    而不是等回实验室用 ffmpeg 才发现整批数据都废了。

    HEVC NAL 头两字节，type = (byte0 >> 1) & 0x3F：
        32 VPS   33 SPS   34 PPS   16-21 IRAP(含 IDR/CRA)   0-9 非 IRAP slice

    关键：不能只统计参数集出现过几次。参数集出现在文件中部是没用的，
    必须出现在第一个 slice 之前，否则开头那段无法解码，ffmpeg 报
    "PPS id out of range" + "Could not find ref with POC"。
    这正是预热期丢弃彩色包造成的症状。
    返回 (是否可解, 说明, 计数)。
    """
    counts = {"vps": 0, "sps": 0, "pps": 0, "irap": 0, "slice": 0}
    try:
        with path.open("rb") as f:
            buf = f.read(max_bytes)
    except OSError as exc:
        return False, f"无法读取: {exc}", counts

    first_slice = None       # 第一个 slice（含 IRAP）的位置
    ps_before_slice = set()
    i, n = 0, len(buf)
    while i < n - 4:
        if buf[i] == 0 and buf[i + 1] == 0:
            if buf[i + 2] == 1:
                hdr, step = i + 3, 3
            elif buf[i + 2] == 0 and buf[i + 3] == 1:
                hdr, step = i + 4, 4
            else:
                i += 1
                continue
            if hdr < n:
                t = (buf[hdr] >> 1) & 0x3F
                if t in (32, 33, 34):
                    key = {32: "vps", 33: "sps", 34: "pps"}[t]
                    counts[key] += 1
                    if first_slice is None:
                        ps_before_slice.add(key)
                elif t <= 21:            # 各类 slice
                    if 16 <= t <= 21:
                        counts["irap"] += 1
                    else:
                        counts["slice"] += 1
                    if first_slice is None:
                        first_slice = i
            i += step
        else:
            i += 1

    missing = {"vps", "sps", "pps"} - ps_before_slice
    if missing:
        return (False,
                f"首个 slice 之前缺少 {'/'.join(sorted(missing)).upper()}"
                f"（首 slice @{first_slice}）",
                counts)
    if counts["irap"] == 0:
        return False, "没有 IRAP 关键帧", counts
    return True, "", counts


def capture(trial: str, duration: int, outdir: Path, node: str) -> int:
    import depthai as dai

    # 预检刚探测过设备时，它可能仍在重新枚举，必须先等它回来
    if not wait_device(dai, timeout=30.0):
        print("CAM_ERROR=等待 30 秒仍未发现 OAK 设备", file=sys.stderr)
        return 1
    device = open_device(dai)

    outdir.mkdir(parents=True, exist_ok=True)
    stem = f"{node}_{trial}"
    depth_path = outdir / f"{stem}_depth.bin"
    color_path = outdir / f"{stem}_color.h265"
    meta_path = outdir / f"{stem}_cam.json"

    signal.signal(signal.SIGINT, _on_signal)
    signal.signal(signal.SIGTERM, _on_signal)

    offset_start, unc_start = clock_offset_ns()

    depth_ts: list[int] = []
    depth_seq: list[int] = []
    color_ts: list[int] = []
    color_sizes: list[int] = []
    depth_frames = 0
    color_packets = 0
    color_bytes = 0
    dropped_note = ""

    with dai.Pipeline(device) as pipeline:
        left = pipeline.create(dai.node.Camera).build(dai.CameraBoardSocket.CAM_B)
        right = pipeline.create(dai.node.Camera).build(dai.CameraBoardSocket.CAM_C)
        color = pipeline.create(dai.node.Camera).build(dai.CameraBoardSocket.CAM_A)

        stereo = pipeline.create(dai.node.StereoDepth).build(
            left.requestOutput((640, 400)),
            right.requestOutput((640, 400)),
        )
        # 必须先对齐到 RGB，setOutputSize 才会生效（否则固定输出 320x200）
        stereo.setDepthAlign(dai.CameraBoardSocket.CAM_A)
        stereo.setOutputSize(DEPTH_W, DEPTH_H)

        encoder = pipeline.create(dai.node.VideoEncoder)
        encoder.setDefaultProfilePreset(
            FPS, dai.VideoEncoderProperties.Profile.H265_MAIN
        )
        # 关键帧间隔：默认可能整段只有开头一个 IDR，一旦丢包后续全部报废，
        # 也无法从中间随机访问。每 KEYFRAME_EVERY_S 秒一个 IDR，
        # 每个 IDR 前都会重发 VPS/SPS/PPS，使流可从任意关键帧解起。
        try:
            encoder.setKeyframeFrequency(FPS * KEYFRAME_EVERY_S)
        except Exception as exc:  # noqa: BLE001
            print(f"CAM_WARN=setKeyframeFrequency 不可用: {exc}", flush=True)
        color.requestOutput((COLOR_W, COLOR_H), dai.ImgFrame.Type.NV12).link(
            encoder.input
        )

        q_depth = stereo.depth.createOutputQueue(maxSize=8, blocking=False)
        q_color = encoder.out.createOutputQueue(maxSize=60, blocking=False)

        pipeline.start()

        # 丢弃启动瞬间的不稳定帧，并等待管线进入稳态
        warmup_deadline = time.monotonic() + 5.0
        warm = 0
        while warm < 15 and time.monotonic() < warmup_deadline:
            try:
                q_depth.get()
                warm += 1
            except Exception:
                break
        # 预热期的彩色包必须保留字节，不能丢弃。
        # H.265 的 VPS/SPS/PPS 只在流最开头出现一次，直接 get() 扔掉会让
        # 整个文件失去参数集，ffmpeg 报 "PPS id out of range" +
        # "Could not find ref with POC"，且无法从任意位置解码。
        # 这里把预热期的包缓存下来，开文件后原样写在最前面。
        warm_color: list[tuple[int, bytes]] = []
        while q_color.has():
            packet = q_color.get()
            warm_color.append((td_to_ns(packet.getTimestamp()),
                               packet.getData().tobytes()))

        # 实际尺寸以设备返回为准，不信任配置值：
        # StereoDepth 会在多种情况下悄悄改变输出尺寸
        probe_frame = q_depth.get()
        actual_w = probe_frame.getWidth()
        actual_h = probe_frame.getHeight()
        frame_bytes = probe_frame.getFrame().nbytes

        # 内参：没有它，深度值无法还原成三维米制坐标
        calibration = _read_calibration(device, dai, actual_w, actual_h)

        print(f"CAM_READY=1", flush=True)

        f_depth = open(depth_path, "wb", buffering=1024 * 1024)
        f_color = open(color_path, "wb", buffering=1024 * 1024)
        try:
            # 先落盘预热期缓存的包，保住流开头的 VPS/SPS/PPS
            for ts, data in warm_color:
                f_color.write(data)
                color_ts.append(ts)
                color_sizes.append(len(data))
                color_bytes += len(data)
                color_packets += 1
            warm_color.clear()

            deadline = time.monotonic() + duration
            while not _stop and time.monotonic() < deadline:
                frame = q_depth.get()
                array = frame.getFrame()
                f_depth.write(array.tobytes())
                depth_ts.append(td_to_ns(frame.getTimestamp()))
                depth_seq.append(frame.getSequenceNum())
                depth_frames += 1

                while q_color.has():
                    packet = q_color.get()
                    data = packet.getData()
                    f_color.write(data.tobytes())
                    color_ts.append(td_to_ns(packet.getTimestamp()))
                    color_sizes.append(int(data.size))
                    color_bytes += int(data.size)
                    color_packets += 1

            # 收尾：把编码器里已产出的残余包取干净
            drain_deadline = time.monotonic() + 1.0
            while time.monotonic() < drain_deadline:
                if not q_color.has():
                    break
                packet = q_color.get()
                data = packet.getData()
                f_color.write(data.tobytes())
                color_ts.append(td_to_ns(packet.getTimestamp()))
                color_sizes.append(int(data.size))
                color_bytes += int(data.size)
                color_packets += 1
        finally:
            f_depth.flush()
            os.fsync(f_depth.fileno())
            f_depth.close()
            f_color.flush()
            os.fsync(f_color.fileno())
            f_color.close()

    offset_end, unc_end = clock_offset_ns()

    span_s = (depth_ts[-1] - depth_ts[0]) / 1e9 if len(depth_ts) > 1 else 0.0
    depth_fps = (len(depth_ts) - 1) / span_s if span_s > 0 else 0.0
    color_fps = (len(color_ts) - 1) / span_s if span_s > 0 and color_ts else 0.0

    # 深度帧序号应连续；缺号说明主机来不及取走，属于真实丢帧
    gaps = 0
    if len(depth_seq) > 1:
        for prev, cur in zip(depth_seq, depth_seq[1:]):
            if cur != prev + 1:
                gaps += cur - prev - 1

    h265_ok, h265_msg, h265_counts = scan_h265(color_path)

    meta = {
        "trial": trial,
        "node": node,
        "requested_duration_s": duration,
        "stopped_early": bool(_stop),
        "depth": {
            "file": depth_path.name,
            "width": actual_w,
            "height": actual_h,
            "dtype": "uint16",
            "byte_order": "little",
            "bytes_per_frame": frame_bytes,
            "frames": depth_frames,
            "measured_fps": round(depth_fps, 3),
            "sequence_gaps": gaps,
            "timestamp_ns_monotonic": depth_ts,
            "sequence_num": depth_seq,
        },
        "calibration": calibration,
        "color": {
            "file": color_path.name,
            "width": COLOR_W,
            "height": COLOR_H,
            "codec": "h265",
            "packets": color_packets,
            "bytes": color_bytes,
            "measured_fps": round(color_fps, 3),
            "keyframe_interval_frames": FPS * KEYFRAME_EVERY_S,
            "decodable": h265_ok,
            "nal_counts": h265_counts,
            "timestamp_ns_monotonic": color_ts,
            "packet_bytes": color_sizes,
        },
        "clock": {
            "note": "epoch_ns = timestamp_ns_monotonic + realtime_minus_monotonic_ns",
            "realtime_minus_monotonic_ns_start": offset_start,
            "realtime_minus_monotonic_ns_end": offset_end,
            "offset_drift_ns": offset_end - offset_start,
            "sample_uncertainty_ns_start": unc_start,
            "sample_uncertainty_ns_end": unc_end,
        },
    }
    with meta_path.open("w", encoding="utf-8") as stream:
        json.dump(meta, stream, ensure_ascii=False)
        stream.write("\n")

    depth_size = depth_path.stat().st_size
    expected = depth_frames * frame_bytes
    if depth_size != expected:
        print(
            f"CAM_ERROR=深度文件大小 {depth_size} != 期望 {expected}",
            file=sys.stderr,
        )
        return 1
    if depth_frames == 0:
        print("CAM_ERROR=没有采集到任何深度帧", file=sys.stderr)
        return 1

    print(f"CAM_DEPTH={depth_path}")
    print(f"CAM_COLOR={color_path}")
    print(f"CAM_META={meta_path}")
    print(f"CAM_DEPTH_SIZE={actual_w}x{actual_h}")
    print(f"CAM_DEPTH_FRAMES={depth_frames}")
    print(f"CAM_DEPTH_FPS={depth_fps:.2f}")
    print(f"CAM_COLOR_PACKETS={color_packets}")
    print(f"CAM_COLOR_FPS={color_fps:.2f}")
    print(f"CAM_COLOR_DECODABLE={1 if h265_ok else 0}")
    print(f"CAM_COLOR_NAL=vps{h265_counts['vps']} sps{h265_counts['sps']} "
          f"pps{h265_counts['pps']} irap{h265_counts['irap']}")
    if not h265_ok:
        # 深度是 raw 的，仍然有效；彩色流不可解只降级不致命，
        # 所以给警告而不是返回失败，避免整组数据被丢弃。
        print(f"CAM_WARN=彩色流可能无法解码: {h265_msg}")
    print(f"CAM_SEQ_GAPS={gaps}")
    print(f"CAM_CLOCK_DRIFT_NS={offset_end - offset_start}")
    if dropped_note:
        print(f"CAM_WARN={dropped_note}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="OAK-D 深度/彩色采集")
    parser.add_argument("--trial")
    parser.add_argument("--duration", type=int)
    parser.add_argument("--outdir", type=Path)
    parser.add_argument("--node", default=os.uname().nodename.split("-")[0])
    parser.add_argument("--check", action="store_true", help="只探测设备，不采集")
    args = parser.parse_args()

    if args.check:
        return probe()

    missing = [
        name
        for name, value in (
            ("--trial", args.trial),
            ("--duration", args.duration),
            ("--outdir", args.outdir),
        )
        if value is None
    ]
    if missing:
        parser.error(f"缺少参数: {', '.join(missing)}")
    if args.duration <= 0:
        parser.error("--duration 必须为正整数")

    try:
        return capture(args.trial, args.duration, args.outdir, args.node)
    except Exception as exc:  # noqa: BLE001
        print(f"CAM_ERROR={type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
