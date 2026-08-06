# csi_lab

Person-in-WiFi-3D 实验的采集编排脚本：1-Tx-2-Rx 的 Wi-Fi CSI 采集，
配合 OAK-D 深度相机提供 3D 姿态真值。

Mac 作主控，通过有线管理网 SSH 编排三台 Ubuntu 节点（PicoScenes + Intel AX210）。

```
        node2 (Tx)
        /        \
       /          \      每条链路上的人体运动会扰动 CSI
      /            \
 node1 (Rx1)    node3 (Rx2)
   + OAK-D 相机（姿态真值）
```

## 一次采集做了什么

`csi_lab.sh <trial> <秒数>` 会依次完成：

1. 预检三台（内核/驱动匹配、AX210、磁盘、相机）
2. 两个接收端起 logger，**确认就绪**才发包（不是 sleep 猜）
3. 相机开始采集，等 `CAM_READY=1`
4. node2 发包（时长显式传递，不依赖缺省值）
5. 停止、落盘、回收到 Mac
6. **包级对齐 + 导频剔除（242→234）+ 相机时间配对**
7. 完整性校验（键完整、行数一致非零、子载波数 = 234）

产出在 `~/csi_results/<时间戳>_<模式>_<trial>/`：

| 文件 | 说明 |
|---|---|
| `aligned/aligned_csi.npz` | 训练直接用的数据 |
| `aligned/alignment_report.json` | 匹配率、时钟偏差、相机配对等指标 |
| `aligned/aligned_packets.csv` | 逐包对齐表 |
| `node1.csi` / `node3.csi` | PicoScenes 原始 |
| `*_depth.bin` | uint16 深度，640×360，小端 |
| `*_color.h265` | 1080p H.265 |
| `*_cam.json` | 相机内参、时间戳、时钟偏移 |

`aligned_csi.npz` 里 `node1_csi_data` / `node3_csi_data` 形状 `(T, 234, 2, 1)`，
已剔除 8 个导频子载波；`depth_frame_index` 给出每个包对应的深度帧号。

## 在新机器上搭主控环境

### 1. 依赖

```bash
# Python（对齐脚本用）
python3 -m venv ~/csienv
~/csienv/bin/pip install numpy

# 可选：看彩色图需要 ffmpeg
brew install ffmpeg          # macOS
sudo apt install ffmpeg      # Linux
```

深度图用 `depth_view.py` 即可，只依赖 numpy，不需要 ffmpeg/OpenCV。

### 2. SSH 配置

脚本里的 `node1` / `node2` / `node3` 是 **SSH 别名**，在 `~/.ssh/config` 里定义：

```
Host node1
    HostName 192.168.50.11
    User <用户名>
    IdentityFile ~/.ssh/id_ed25519

Host node2
    HostName 192.168.50.12
    User <用户名>
    IdentityFile ~/.ssh/id_ed25519

Host node3
    HostName 192.168.50.13
    User <用户名>
    IdentityFile ~/.ssh/id_ed25519
```

必须配好**免密登录**（`ssh-copy-id`），脚本用 `BatchMode=yes`，不会交互输密码。

### 3. 本地配置

```bash
cp csi_lab.conf.example csi_lab.conf
# 按需修改
```

### 4. 分发节点端脚本

```bash
for n in node1 node2 node3; do
    scp csi_agent.sh csi_cam.py $n:~/
done
```

节点端还需要（本仓库不含，属节点环境）：
- PicoScenes + 与内核匹配的 `picoscenes-driver-modules-<内核版本>`
- `depthai`（相机宿主机）
- sudo NOPASSWD：`ifconfig` `iw` `route` `ip` `nmcli` `systemctl`
  `array_prepare_for_picoscenes` `array_status` `pkill`

### 5. 验证

```bash
./csi_lab.sh check                 # 只预检
CAM_HOST=node1 ./csi_lab.sh check  # 含相机
```

## 用法

```bash
./csi_lab.sh <trial> [秒数]        # 单次采集，默认 30 秒
./csi_lab.sh batch <批量文件>       # 批量采集
./csi_lab.sh check                # 只做预检
./csi_lab.sh restore              # 恢复三台 Wi-Fi

./field.sh                        # 现场引导（装备清单 → 网络 → 预检 → 采集）
./field.sh net                    # 网络诊断 + 自动恢复 + 子网扫描
./field.sh pack                   # 装备清单
```

批量文件格式：

```
# trial名        秒数   重复次数
empty_05m        30     3
walk_link1_05m   30     3
walk_link2_05m   30     3
```

数据检查：

```bash
# 深度图渲染 / 距离分布（只需 numpy）
~/csienv/bin/python depth_view.py <depth.bin> --grid
~/csienv/bin/python depth_view.py <depth.bin> --profile

# 彩色图
ffmpeg -i <color.h265> -frames:v 1 -ss 5 out.png
```

## 踩过的坑

这些都是实际调试出来的，改代码前先读：

**内核会被自动升级切走。** PicoScenes 的补丁 `iwlwifi` 只为特定内核编译。
开机进了新内核就取不到 CSI，且报错隐晦（`Unresolvable device ID`）。
用 GRUB **条目 ID**（不是数字索引）锁定内核，并 `apt-mark hold`。
判断驱动是否为补丁版看 `modinfo iwlwifi | grep srcversion`，只看 `uname -r` 不够。

**H.265 预热期不能丢包。** VPS/SPS/PPS 只在流开头出现一次，
预热时 `q_color.get()` 丢弃会让整个文件无法解码
（`PPS id out of range` + `Could not find ref with POC`）。
`csi_cam.py` 现在缓存预热包并写在最前面，且用 `scan_h265()` 当场校验，
输出 `CAM_COLOR_DECODABLE`。校验必须要求参数集在**第一个 slice 之前**，
只统计出现次数会漏判。

**相机配置有硬边界。** 深度 640×360 对齐 RGB + 1080p H.265 ≈ 15 MB/s 稳定；
深度 + 原始 1080p → 设备崩溃；深度 + 4K H.265 → **主机断电级硬复位**（零日志）。
4K 与原始彩色是永久禁区。

**`setOutputSize()` 只在 `setDepthAlign(CAM_A)` 之后生效**，
否则 StereoDepth 固定输出 320×200。选 640×360 因其恰为 1920×1080 的 1/3，
深度像素 (u,v) ↔ RGB (3u,3v)，无裁切形变。

**发射时长必须显式传。** 旧版编排调 `tx` 不带参数，而 agent 缺省 30 秒 ——
超过 30 秒的实验会静默只发 30 秒，且 stop 时进程已退出、返回 0，全程无报错。

**`array_prepare_for_picoscenes` 静默失败仍返回 0。** 必须用 `iw dev` 校验
monitor 接口和信道，不能看退出码。PicoScenes 也拒绝以 root 运行。

**`CAM_PAIR_DT_*` 不能用来判断同步质量。** 30 fps 量化误差本身均匀分布 ±16.67 ms，
理论中位数 8.33 ms，实测 8.2–8.4 ms 就是这个饱和值，不是同步变差。

**不需要 NTP。** 两个接收端收同一广播包，`system_ns` 之差即时钟偏差，
每 trial 实测。跨会话偏差会变且符号会翻转，所以不能沿用上次的值。

**macOS 自带 bash 3.2**：不支持 `declare -A` / `mapfile`；
`$VAR` 紧邻中文字符会误解析（写 `${VAR}`）；`date +%s%N` 无效
（纳秒时间戳一律由节点端生成）。

## 文件

| 文件 | 说明 |
|---|---|
| `csi_lab.sh` | Mac 主控编排入口 |
| `csi_agent.sh` | 节点端脚本，需分发到三台 |
| `csi_cam.py` | OAK-D 采集，需分发到相机宿主 |
| `csi_align.py` | 包对齐 + 导频剔除 + 相机配对 |
| `depth_view.py` | 深度图渲染/距离分布，只依赖 numpy |
| `field.sh` | 现场引导与网络诊断 |
| `phase_c_*.txt` | 批量采集配置（5/10/15 m 三档） |
