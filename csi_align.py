#!/usr/bin/env python3
"""按发射包身份对齐 node1/node3 的 PicoScenes 采集，并与深度相机配对。

沿革
----
本文件基于队友的 align_1tx2rx.py，核心逻辑原样保留：
  * 对齐键为 (source_mac, sequence, fragment, task_id) 四元组。
    仅用 sequence 是不够的——12 位 seq 在 200 pkt/s 下约 20.5 秒回绕一次，
    task_id 负责跨回绕消歧。实测 5842 个包 duplicate_keys=0。
  * 只统计与目标发射源 MAC 相同的包。环境中其他 Wi-Fi 流量约占总帧数一半，
    不过滤会得到完全错误的包率与匹配率。
  * 读取 RxSBasic v>=4 的 system_ns（CLOCK_REALTIME epoch 纳秒），
    据此给出两个接收端的时钟偏差。

本版新增
--------
  1. 导频列剔除：242 -> 234。导频子载波 CSI 恒为 0（转 dB 即 -inf），
     且不同会话取值不同，直接作为模型输入会构成会话指纹泄漏。
     实测索引 [6,32,74,100,141,167,209,235]，对应 802.11ax HE 242-tone RU
     的 ±{22,48,90,116}。原始 242 列一并保留，便于回溯。
  2. 相机配对：把每个对齐包配到时间上最近的深度帧，并给出配对误差分布。
     深度帧时间戳位于 CLOCK_MONOTONIC 域，需加 realtime-monotonic 偏移
     换算到 epoch，该偏移由 csi_cam.py 在采集前后各采样一次。
"""

from __future__ import annotations

import argparse
import bisect
from collections import Counter, defaultdict, deque
import csv
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import statistics
import struct
import sys


MAGIC = 0x20150315
MAX_RECORD_SIZE = 256 * 1024 * 1024

# 802.11ax HE 242-tone RU 的导频子载波位置（以 DC 三音被剔除后的数组索引表示）
PILOT_INDICES = (6, 32, 74, 100, 141, 167, 209, 235)


class FormatError(ValueError):
    pass


@dataclass(frozen=True)
class Packet:
    frame_index: int
    source_mac: str
    sequence: int
    fragment: int
    task_id: int | None
    tx_id: int | None
    rx_timestamp: int | None
    system_ns: int | None

    @property
    def key(self) -> tuple[str, int, int, int] | None:
        if self.task_id is None:
            return None
        return self.source_mac, self.sequence, self.fragment, self.task_id


@dataclass(frozen=True)
class Capture:
    path: Path
    frame_count: int
    packets: tuple[Packet, ...]


def need(data: bytes, offset: int, size: int, label: str) -> None:
    if offset < 0 or size < 0 or offset + size > len(data):
        raise FormatError(f"{label} truncated at {offset}+{size}/{len(data)}")


def u16(data: bytes, offset: int, label: str) -> int:
    need(data, offset, 2, label)
    return struct.unpack_from("<H", data, offset)[0]


def u32(data: bytes, offset: int, label: str) -> int:
    need(data, offset, 4, label)
    return struct.unpack_from("<I", data, offset)[0]


def u64(data: bytes, offset: int, label: str) -> int:
    need(data, offset, 8, label)
    return struct.unpack_from("<Q", data, offset)[0]


def mac(raw: bytes) -> str:
    return ":".join(f"{part:02x}" for part in raw)


def normalize_mac(value: str) -> str:
    compact = value.lower().replace(":", "").replace("-", "").strip()
    if len(compact) != 12:
        raise ValueError(f"invalid MAC: {value}")
    try:
        return mac(bytes.fromhex(compact))
    except ValueError as exc:
        raise ValueError(f"invalid MAC: {value}") from exc


def parse_segment(data: bytes, offset: int) -> tuple[str, int, int, int]:
    segment_length = u32(data, offset, "segment length")
    total = segment_length + 4
    need(data, offset, total, "segment")
    need(data, offset + 4, 1, "segment name length")
    name_length = data[offset + 4]
    if name_length == 0:
        raise FormatError("zero-length segment name")
    need(data, offset + 5, name_length + 2, "segment header")
    name = data[offset + 5 : offset + 5 + name_length].rstrip(b"\0").decode(
        "ascii", errors="replace"
    )
    version_offset = offset + 5 + name_length
    version = u16(data, version_offset, "segment version")
    return name, version, version_offset + 2, total


def parse_record(data: bytes, frame_index: int) -> list[Packet]:
    need(data, 0, 11, "frame header")
    if u32(data, 0, "frame length") + 4 != len(data):
        raise FormatError(f"frame {frame_index}: length mismatch")
    if u32(data, 4, "frame magic") != MAGIC:
        raise FormatError(f"frame {frame_index}: bad magic")

    frame_version = u16(data, 8, "frame version")
    number_segments = data[10]
    if frame_version == 1:
        offset = 11
        number_mpdus = 1
    elif frame_version >= 2:
        number_mpdus = u16(data, 11, "MPDU count")
        offset = 13
    else:
        raise FormatError(f"unsupported frame version {frame_version}")

    rx_timestamp = None
    system_ns = None
    for _ in range(number_segments):
        name, version, payload_offset, total = parse_segment(data, offset)
        if name == "RxSBasic":
            rx_timestamp = u64(data, payload_offset + 2, "RxSBasic timestamp")
            if version >= 4:
                value = u64(data, payload_offset + 10, "RxSBasic system_ns")
                system_ns = value or None
        offset += total

    packets: list[Packet] = []
    for _ in range(number_mpdus):
        if frame_version == 1:
            mpdu_length = len(data) - offset
        else:
            mpdu_length = u32(data, offset, "MPDU length")
            offset += 4
        need(data, offset, mpdu_length, "MPDU")
        mpdu = data[offset : offset + mpdu_length]
        offset += mpdu_length
        if len(mpdu) < 24:
            continue

        sequence_control = u16(mpdu, 22, "sequence control")
        task_id = None
        tx_id = None
        if len(mpdu) >= 40 and u32(mpdu, 24, "PicoScenes header") == MAGIC:
            _, _, _, _, _, task_id, tx_id = struct.unpack_from("<IIHBBHH", mpdu, 24)
        packets.append(
            Packet(
                frame_index=frame_index,
                source_mac=mac(mpdu[10:16]),
                sequence=sequence_control >> 4,
                fragment=sequence_control & 0xF,
                task_id=task_id,
                tx_id=tx_id,
                rx_timestamp=rx_timestamp,
                system_ns=system_ns,
            )
        )
    return packets


def scan(path: Path) -> Capture:
    path = path.expanduser().resolve()
    packets: list[Packet] = []
    frame_index = 0
    file_offset = 0
    with path.open("rb") as stream:
        while True:
            word = stream.read(4)
            if not word:
                break
            if len(word) != 4:
                raise FormatError(f"{path}: truncated length at {file_offset}")
            body_length = struct.unpack("<I", word)[0]
            if body_length < 7 or body_length > MAX_RECORD_SIZE:
                raise FormatError(
                    f"{path}: invalid record length {body_length} at {file_offset}"
                )
            body = stream.read(body_length)
            if len(body) != body_length:
                raise FormatError(f"{path}: truncated record at {file_offset}")
            try:
                packets.extend(parse_record(word + body, frame_index))
            except FormatError as exc:
                raise FormatError(f"{path}: offset {file_offset}: {exc}") from exc
            file_offset += 4 + body_length
            frame_index += 1
    return Capture(path, frame_index, tuple(packets))


def choose_source(first: Capture, second: Capture, requested: str | None) -> str:
    first_counts = Counter(p.source_mac for p in first.packets if p.key is not None)
    second_counts = Counter(p.source_mac for p in second.packets if p.key is not None)
    if requested:
        source = normalize_mac(requested)
        if source not in first_counts or source not in second_counts:
            raise ValueError(f"source {source} is not present in both captures")
        return source
    common = set(first_counts) & set(second_counts)
    if not common:
        raise ValueError("no common PicoScenes source MAC with TaskId")
    return max(
        common,
        key=lambda source: (
            min(first_counts[source], second_counts[source]),
            first_counts[source] + second_counts[source],
        ),
    )


def align(
    first: list[Packet], second: list[Packet]
) -> tuple[list[tuple[Packet, Packet]], int, int]:
    first = [p for p in first if p.key is not None]
    second = [p for p in second if p.key is not None]
    count1 = Counter(p.key for p in first)
    count2 = Counter(p.key for p in second)
    duplicates1 = sum(value - 1 for value in count1.values() if value > 1)
    duplicates2 = sum(value - 1 for value in count2.values() if value > 1)

    queues: dict[tuple[str, int, int, int], deque[Packet]] = defaultdict(deque)
    for packet in second:
        assert packet.key is not None
        queues[packet.key].append(packet)
    matches: list[tuple[Packet, Packet]] = []
    for packet in first:
        assert packet.key is not None
        if queues[packet.key]:
            matches.append((packet, queues[packet.key].popleft()))
    return matches, duplicates1, duplicates2


def write_csv(path: Path, matches: list[tuple[Packet, Packet]]) -> None:
    fields = [
        "aligned_index",
        "source_mac",
        "sequence",
        "fragment",
        "task_id",
        "tx_id",
        "node1_frame_index",
        "node3_frame_index",
        "node1_rx_timestamp",
        "node3_rx_timestamp",
        "node1_system_ns",
        "node3_system_ns",
        "node3_minus_node1_system_ns",
    ]
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for index, (first, second) in enumerate(matches):
            delta = None
            if first.system_ns is not None and second.system_ns is not None:
                delta = second.system_ns - first.system_ns
            writer.writerow(
                {
                    "aligned_index": index,
                    "source_mac": first.source_mac,
                    "sequence": first.sequence,
                    "fragment": first.fragment,
                    "task_id": first.task_id,
                    "tx_id": first.tx_id,
                    "node1_frame_index": first.frame_index,
                    "node3_frame_index": second.frame_index,
                    "node1_rx_timestamp": first.rx_timestamp,
                    "node3_rx_timestamp": second.rx_timestamp,
                    "node1_system_ns": first.system_ns,
                    "node3_system_ns": second.system_ns,
                    "node3_minus_node1_system_ns": delta,
                }
            )


def load_prepared_csi(
    path: Path, frame_indices: list[int]
):
    """Select aligned rows from a receiver-side target_csi NPZ."""
    import numpy as np

    path = path.expanduser().resolve()
    with np.load(path, allow_pickle=False) as data:
        required = {"target_csi", "frame_index"}
        missing_keys = required - set(data.files)
        if missing_keys:
            raise RuntimeError(
                f"{path} missing keys: {', '.join(sorted(missing_keys))}"
            )
        cube = np.asarray(data["target_csi"])
        raw_indices = np.asarray(data["frame_index"], dtype=np.int64)

    if raw_indices.ndim != 1:
        raise RuntimeError(f"{path}: frame_index must be one-dimensional")
    if cube.ndim < 3:
        raise RuntimeError(f"{path}: target_csi has invalid shape {cube.shape}")
    if cube.shape[0] != raw_indices.size:
        raise RuntimeError(
            f"{path}: target_csi rows ({cube.shape[0]}) != "
            f"frame_index rows ({raw_indices.size})"
        )

    row_for_frame = {int(frame): row for row, frame in enumerate(raw_indices)}
    if len(row_for_frame) != raw_indices.size:
        raise RuntimeError(f"{path}: duplicate frame_index values")
    missing_frames = [frame for frame in frame_indices if frame not in row_for_frame]
    if missing_frames:
        preview = ", ".join(str(value) for value in missing_frames[:5])
        raise RuntimeError(f"{path}: aligned frame indices are missing: {preview}")
    rows = [row_for_frame[frame] for frame in frame_indices]
    return cube[rows]


def strip_pilots(cube, subcarrier_axis: int = 1):
    """剔除导频列。cube 形如 (frames, subcarrier, rx, tx)。

    只在子载波数确实为 242 时才动手。其他带宽/RU 配置下导频位置不同，
    盲目按索引删除会破坏数据，此时返回 None 让调用方跳过。
    """
    import numpy as np

    if cube.shape[subcarrier_axis] != 242:
        return None
    keep = np.setdiff1d(np.arange(242), np.asarray(PILOT_INDICES))
    return np.take(cube, keep, axis=subcarrier_axis), keep


def load_camera(meta_path: Path):
    """读取 csi_cam.py 的元数据，返回深度帧的 epoch 纳秒时间戳。

    偏移在采集前后各采样一次；这里按帧在采集窗口中的位置做线性插值，
    以吸收（极小的）时钟漂移。实测 10 秒漂移仅 62 ns，插值主要是防御性的。
    """
    with meta_path.open(encoding="utf-8") as stream:
        meta = json.load(stream)

    depth = meta["depth"]
    clock = meta["clock"]
    monotonic = depth["timestamp_ns_monotonic"]
    if not monotonic:
        raise ValueError(f"{meta_path}: 深度帧时间戳为空")

    start = int(clock["realtime_minus_monotonic_ns_start"])
    end = int(clock["realtime_minus_monotonic_ns_end"])
    span = monotonic[-1] - monotonic[0]
    epoch = []
    for value in monotonic:
        if span > 0:
            ratio = (value - monotonic[0]) / span
            offset = start + (end - start) * ratio
        else:
            offset = start
        epoch.append(int(value + offset))
    return meta, epoch


def pair_camera(csi_ns: list[int], depth_epoch_ns: list[int]):
    """把每个 CSI 包配到时间最近的深度帧。

    返回 (帧序号数组, 时间差数组)。时间差为 depth - csi，单位纳秒。
    深度帧时间戳单调递增，用二分查找即可。
    """
    indices = []
    deltas = []
    last = len(depth_epoch_ns) - 1
    for value in csi_ns:
        position = bisect.bisect_left(depth_epoch_ns, value)
        if position == 0:
            best = 0
        elif position > last:
            best = last
        else:
            before = depth_epoch_ns[position - 1]
            after = depth_epoch_ns[position]
            best = position - 1 if (value - before) <= (after - value) else position
        indices.append(best)
        deltas.append(depth_epoch_ns[best] - value)
    return indices, deltas


def export_npz(
    output: Path,
    first: Capture,
    second: Capture,
    matches: list[tuple[Packet, Packet]],
    first_prepared: Path | None = None,
    second_prepared: Path | None = None,
    camera_meta: Path | None = None,
    report: dict | None = None,
) -> tuple[list[int], list[int]]:
    import numpy as np

    indices1 = [pair[0].frame_index for pair in matches]
    indices2 = [pair[1].frame_index for pair in matches]
    if first_prepared is not None and second_prepared is not None:
        csi1 = load_prepared_csi(first_prepared, indices1)
        csi2 = load_prepared_csi(second_prepared, indices2)
    else:
        from CSIKit.reader import get_reader

        frames1 = get_reader(str(first.path)).read_file(str(first.path)).frames
        frames2 = get_reader(str(second.path)).read_file(str(second.path)).frames
        if max(indices1) >= len(frames1) or max(indices2) >= len(frames2):
            raise RuntimeError("CSIKit frame indices differ from raw PicoScenes records")
        csi1 = np.stack(
            [np.asarray(frames1[index].csi_matrix) for index in indices1]
        )
        csi2 = np.stack(
            [np.asarray(frames2[index].csi_matrix) for index in indices2]
        )

    if csi1.shape[0] != len(matches) or csi2.shape[0] != len(matches):
        raise RuntimeError("aligned CSI row count differs from packet matches")
    if csi1.shape[1:] != csi2.shape[1:]:
        raise RuntimeError(
            f"receiver CSI shapes differ: {csi1.shape} vs {csi2.shape}"
        )

    node1_ns = [pair[0].system_ns or 0 for pair in matches]
    node3_ns = [pair[1].system_ns or 0 for pair in matches]

    payload = {
        "node1_csi": csi1,
        "node3_csi": csi2,
        "node1_frame_index": np.asarray(indices1, dtype=np.int64),
        "node3_frame_index": np.asarray(indices2, dtype=np.int64),
        "sequence": np.asarray(
            [pair[0].sequence for pair in matches], dtype=np.uint16
        ),
        "task_id": np.asarray([pair[0].task_id for pair in matches], dtype=np.uint16),
        "node1_system_ns": np.asarray(node1_ns, dtype=np.uint64),
        "node3_system_ns": np.asarray(node3_ns, dtype=np.uint64),
    }

    # 导频剔除版本：模型输入应当用这个，242 列仅供回溯
    stripped = strip_pilots(csi1)
    stripped3 = strip_pilots(csi2)
    if stripped is not None and stripped3 is not None:
        payload["node1_csi_data"] = stripped[0]
        payload["node3_csi_data"] = stripped3[0]
        payload["data_subcarrier_index"] = np.asarray(stripped[1], dtype=np.int64)
        payload["pilot_subcarrier_index"] = np.asarray(PILOT_INDICES, dtype=np.int64)
        if report is not None:
            report["data_subcarriers"] = int(stripped[0].shape[1])
            report["pilot_subcarriers_removed"] = list(PILOT_INDICES)
    elif report is not None:
        report["warnings"].append(
            f"子载波数为 {csi1.shape[1]}（非 242），跳过导频剔除"
        )

    # 相机配对
    if camera_meta is not None:
        try:
            meta, depth_epoch = load_camera(camera_meta)
            cam_node = str(meta.get("node", ""))
            if "node3" in cam_node:
                reference, reference_name = node3_ns, "node3"
            elif "node1" in cam_node:
                reference, reference_name = node1_ns, "node1"
            else:
                reference, reference_name = node1_ns, "node1"
                if report is not None:
                    report["warnings"].append(
                        f"相机宿主 '{cam_node}' 不是任一接收端，"
                        f"深度与 CSI 分属不同时钟域，配对误差不可信"
                    )
            if not all(reference):
                raise ValueError("参考接收端缺少 system_ns（RxSBasic 版本 <4）")

            frame_index, delta = pair_camera(reference, depth_epoch)
            payload["depth_frame_index"] = np.asarray(frame_index, dtype=np.int64)
            payload["depth_dt_ns"] = np.asarray(delta, dtype=np.int64)
            payload["depth_timestamp_epoch_ns"] = np.asarray(
                depth_epoch, dtype=np.int64
            )

            if report is not None:
                magnitude = sorted(abs(value) for value in delta)
                count = len(magnitude)
                half_frame = 1_000_000_000 // 60  # 30fps 的半帧 ≈ 16.67 ms
                report["camera"] = {
                    "meta_file": str(camera_meta),
                    "host_node": cam_node,
                    "reference_receiver": reference_name,
                    "depth_frames": len(depth_epoch),
                    "depth_measured_fps": meta["depth"].get("measured_fps"),
                    "depth_sequence_gaps": meta["depth"].get("sequence_gaps"),
                    "clock_offset_drift_ns": meta["clock"].get("offset_drift_ns"),
                    "pair_abs_dt_median_ns": magnitude[count // 2] if count else None,
                    "pair_abs_dt_p95_ns": magnitude[int(count * 0.95)] if count else None,
                    "pair_abs_dt_max_ns": magnitude[-1] if count else None,
                    "pairs_beyond_half_frame": sum(
                        1 for value in magnitude if value > half_frame
                    ),
                    "calibration": meta.get("calibration"),
                }
        except Exception as exc:  # noqa: BLE001
            if report is not None:
                report["warnings"].append(f"相机配对失败: {exc}")

    np.savez_compressed(output, **payload)
    return list(csi1.shape), list(csi2.shape)


def time_bounds(packets: list[Packet]) -> tuple[int | None, int | None]:
    values = [p.system_ns for p in packets if p.system_ns is not None]
    return (min(values), max(values)) if values else (None, None)


def iso_ns(value: int | None) -> str | None:
    if value is None:
        return None
    return datetime.fromtimestamp(value / 1e9, timezone.utc).isoformat(
        timespec="microseconds"
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Align two PicoScenes receivers by MAC sequence + TaskId"
    )
    parser.add_argument("node1", type=Path)
    parser.add_argument("node3", type=Path)
    parser.add_argument("--tx-mac")
    parser.add_argument("--output-dir", type=Path, default=Path("aligned"))
    parser.add_argument("--export-npz", action="store_true")
    parser.add_argument(
        "--node1-target-npz",
        type=Path,
        help="receiver-side NPZ containing target_csi and raw frame_index",
    )
    parser.add_argument(
        "--node3-target-npz",
        type=Path,
        help="receiver-side NPZ containing target_csi and raw frame_index",
    )
    parser.add_argument(
        "--cam-meta",
        type=Path,
        help="csi_cam.py 产出的 *_cam.json，用于把深度帧配到 CSI 包",
    )
    args = parser.parse_args()

    if (args.node1_target_npz is None) != (args.node3_target_npz is None):
        parser.error("--node1-target-npz and --node3-target-npz must be used together")

    try:
        capture1 = scan(args.node1)
        capture3 = scan(args.node3)
        source = choose_source(capture1, capture3, args.tx_mac)
    except (OSError, FormatError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    target1 = [p for p in capture1.packets if p.source_mac == source and p.key]
    target3 = [p for p in capture3.packets if p.source_mac == source and p.key]
    matches, duplicate1, duplicate3 = align(target1, target3)

    output_dir = args.output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    csv_path = output_dir / "aligned_packets.csv"
    report_path = output_dir / "alignment_report.json"
    write_csv(csv_path, matches)

    deltas = [
        b.system_ns - a.system_ns
        for a, b in matches
        if a.system_ns is not None and b.system_ns is not None
    ]
    median_delta = statistics.median(deltas) if deltas else None
    mad_delta = (
        statistics.median(abs(value - median_delta) for value in deltas)
        if deltas
        else None
    )
    indices3 = [pair[1].frame_index for pair in matches]
    non_monotonic = sum(
        current <= previous
        for previous, current in zip(indices3, indices3[1:])
    )
    start1, end1 = time_bounds(target1)
    start3, end3 = time_bounds(target3)
    warnings: list[str] = []
    if not matches:
        warnings.append("no common packet IDs; captures are not the same transmission")
    if duplicate1 or duplicate3:
        warnings.append("duplicate keys were paired in receiver arrival order")
    if non_monotonic:
        warnings.append("node3 matched indices are non-monotonic")

    report: dict[str, object] = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "alignment_key": ["source_mac", "sequence", "fragment", "task_id"],
        "tx_mac": source,
        "node1": {
            "file": str(capture1.path),
            "all_frames": capture1.frame_count,
            "target_packets": len(target1),
            "duplicate_keys": duplicate1,
            "system_ns_start": start1,
            "system_ns_end": end1,
            "utc_start": iso_ns(start1),
            "utc_end": iso_ns(end1),
        },
        "node3": {
            "file": str(capture3.path),
            "all_frames": capture3.frame_count,
            "target_packets": len(target3),
            "duplicate_keys": duplicate3,
            "system_ns_start": start3,
            "system_ns_end": end3,
            "utc_start": iso_ns(start3),
            "utc_end": iso_ns(end3),
        },
        "matched_packets": len(matches),
        "node1_match_ratio": len(matches) / len(target1) if target1 else 0,
        "node3_match_ratio": len(matches) / len(target3) if target3 else 0,
        "unmatched_node1": len(target1) - len(matches),
        "unmatched_node3": len(target3) - len(matches),
        "node3_minus_node1_system_ns_median": median_delta,
        "node3_minus_node1_system_ns_mad": mad_delta,
        "node3_non_monotonic_steps": non_monotonic,
        "warnings": warnings,
        "outputs": {"aligned_packets_csv": str(csv_path)},
    }

    if args.export_npz and matches:
        npz_path = output_dir / "aligned_csi.npz"
        try:
            shape1, shape3 = export_npz(
                npz_path,
                capture1,
                capture3,
                matches,
                args.node1_target_npz,
                args.node3_target_npz,
                args.cam_meta,
                report,
            )
            report["outputs"]["aligned_csi_npz"] = str(npz_path)  # type: ignore[index]
            report["aligned_shapes"] = {"node1": shape1, "node3": shape3}
        except Exception as exc:
            warning = f"NPZ export skipped: {exc}"
            warnings.append(warning)
            print(f"WARNING: {warning}", file=sys.stderr)

    with report_path.open("w", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
        stream.write("\n")

    print(f"TX_MAC={source}")
    print(f"NODE1_TARGET={len(target1)}")
    print(f"NODE3_TARGET={len(target3)}")
    print(f"MATCHED={len(matches)}")
    print(f"NODE1_MATCH_RATIO={len(matches) / len(target1) if target1 else 0:.6f}")
    print(f"NODE3_MATCH_RATIO={len(matches) / len(target3) if target3 else 0:.6f}")
    print(f"NON_MONOTONIC={non_monotonic}")
    if median_delta is not None:
        print(f"CLOCK_OFFSET_NODE3_MINUS_NODE1_NS={int(median_delta)}")
    if "data_subcarriers" in report:
        print(f"DATA_SUBCARRIERS={report['data_subcarriers']}")
    camera = report.get("camera")
    if isinstance(camera, dict):
        print(f"CAM_HOST={camera['host_node']}")
        print(f"CAM_DEPTH_FRAMES={camera['depth_frames']}")
        print(f"CAM_PAIR_DT_MEDIAN_MS={camera['pair_abs_dt_median_ns'] / 1e6:.2f}")
        print(f"CAM_PAIR_DT_P95_MS={camera['pair_abs_dt_p95_ns'] / 1e6:.2f}")
        print(f"CAM_PAIRS_BEYOND_HALF_FRAME={camera['pairs_beyond_half_frame']}")
    for message in warnings:
        print(f"WARNING={message}", file=sys.stderr)
    print(f"REPORT={report_path}")
    return 0 if matches else 3


if __name__ == "__main__":
    raise SystemExit(main())
