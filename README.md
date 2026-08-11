# csi_lab

Person-in-WiFi-3D 实验的采集编排脚本：1-Tx-2-Rx 的 Wi-Fi CSI 采集，
配合 OAK-D 深度相机采集同步 RGB-D 监督数据。当前尚未生成经过验证的 3D 骨骼标签。

Mac 作主控，通过有线管理网 SSH 编排三台 Ubuntu 节点（PicoScenes + Intel AX210）。

> **接手前先读 [`HANDOFF.md`](HANDOFF.md)** —— 项目交接文档，含实验设计、
> 已验证成果、已推翻的结论、踩过的坑。
>
> **当前状态（2026-08-07）**：重新布置 5 m 场地后，Link2 人体响应已经出现。
> 两个静站窗在 node3 重复得到 +3.06% / +2.92%；走动时 node3 RSSI 动态标准差为
> 空场的 2.10×，node1 对照为 0.82×，node3 两根接收链的 100 ms CSI
> 子载波变化能量为 1.47× / 1.45×。这说明新布局的 Link2 **诊断门控通过**，
> 但两次空场仍有 2.57% / 3.16% 的慢漂，且走动试验没有完整末尾空场，
> 因此暂不开始批量正式采集。RSSI 只作为检测/分段和基线特征，不替代完整 CSI。
> 详见 HANDOFF.md §0.00。

```
        node2 (Tx)
        /        \
       /          \      每条链路上的人体运动会扰动 CSI
      /            \
 node1 (Rx1)    node3 (Rx2)
                   + OAK-D 相机（当前宿主，RGB-D 监督数据）
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
| `aligned/aligned_csi.npz` | 对齐后的分析/训练候选输入；仍需质量标签和骨骼监督 |
| `aligned/alignment_report.json` | 匹配率、时钟偏差、相机配对等指标 |
| `aligned/aligned_packets.csv` | 逐包对齐表 |
| `node1.csi` / `node3.csi` | PicoScenes 原始 |
| `*_depth.bin` | uint16 深度，640×360，小端 |
| `*_color.h265` | 1080p H.265 |
| `*_cam.json` | 相机内参、时间戳、时钟偏移 |
| `experiment.txt` | trial 参数及 Tx 接口、频率、报告功率和 AX210 功率控制模式 |

`aligned_csi.npz` 里 `node1_csi_data` / `node3_csi_data` 形状 `(T, 234, 2, 1)`，
已剔除 8 个导频子载波；`depth_frame_index` 给出每个包对应的深度帧号。

## 实验流程

### 当前批量前门控

不要直接运行 `phase_c_*.txt` 批量任务。先保持新布局不动并完成：

1. 固定天线、支架和线缆；记录 Tx monitor 接口的实测频率与 `iw dev` 报告功率。
   AX210 发射功率受固件/法规控制，接收增益由固件 AGC 管理；不要把 QCA9300 的
   PicoScenes `--rx-gain` 参数误当作 AX210 设置。
2. 运行一次正式空场，确认两端 5 秒分段均值没有不可解释的慢漂。
3. 运行一次 Link2 walk，动作窗必须包含约 5 秒前空场、15–20 秒动作和至少 5 秒后空场。
4. 人工核对彩色/深度时间线；同时比较 RSSI 动态标准差、CSI 子载波变化能量和两条链路的特异性。

只有当前后空场完整、node3 动态响应可重复且 node1 对照没有同步增加时，才进入每类 3 次重复采集。

### 阶段 C：节点间距对照实验

三档间距 × 三场景 × 3 次 = 27 组。

**移动方式**：node1、node3、相机、行走区**全部固定**（node1–node3 维持 5 m），
只把 node2 拉远到 10 m、15 m。这样自变量是 Tx–Rx 距离，
真值质量在三档间保持一致，不引入混杂因子。

> 深度误差随距离平方增长（基线仅 7.5 cm）：3 m 数厘米、5 m 十几厘米、10 m 数十厘米。
> 所以相机与行走区绝不能跟着移动。

| 场景 | 做法 |
|---|---|
| `empty` | 所有人离开三角形区域，也不站在任何两点连线上 |
| `walk_link1` | 一个人在 node2→node1 连线**中点**，垂直穿越链路来回走 |
| `walk_link2` | 同上，走 node2→node3 连线中点 |

走位要求（今天反复出问题的地方）：

* **场内只能有一个人**。其他人退到相机视野外，且避开两条链路 —— 站在链路上同样扰动 CSI
* 走**链路中点**，不是端点附近。紧邻接收天线时直射径占主导，旁经影响很小
* 横向跨度 ±1.5 m（总跨约 3 m），垂直于连线来回走，别原地踏步
* 距相机 2–5 m，全身始终在画面内。相机水平半视场角约 40°，2.5 m 处画面半宽约 2.1 m

### 每组跑完必须人工确认

采集指标全绿 **不等于** 数据有效。今天有三轮指标全绿但数据无用。

```bash
# 1. 采集指标（脚本自动输出）
#    匹配率 >95%、DATA_SUBCARRIERS=234、CAM_SEQ_GAPS=0、CAM_COLOR_DECODABLE=1、完整性通过

# 2. 深度距离分布 —— 确认人真的入镜、位置对
~/csienv/bin/python depth_view.py <depth.bin> --profile

# 3. 看画面 —— 确认单人、全身在画面内
ffmpeg -i <color.h265> -ss 5 -frames:v 1 /tmp/check.png
```

判断有没有人**用绝对阈值**，不要用相对阈值 —— 场景变化会让相对阈值失效。

### 批量采集

```bash
CAM_HOST=node3 ./csi_lab.sh batch phase_c_05m.txt
```

* 单组失败**不会中断批量**，只警告并继续。跑完务必检查有没有失败的组
* 不要给 batch 加管道过滤（`| grep`），缓冲会导致长时间无输出、看起来像卡死
* 每档跑完确认 Mac 已回收后再清理节点：`ssh node1 'rm -rf ~/csi_data/*'`

### 回收失败不等于数据丢失

SSH 中途断开时编排会报错，但数据通常在节点上完好。先查再决定是否重采：

```bash
ssh node1 'ls -la ~/csi_data/<目录>'      # 确认文件在、大小对
scp node1:~/csi_data/<目录>/\* <本地目录>/  # 手动回收
~/csienv/bin/python csi_align.py ...       # 重跑对齐
```

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
CAM_HOST=node3 ./csi_lab.sh check  # 含当前接在 node3 的相机
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
