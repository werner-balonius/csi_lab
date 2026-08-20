# Person-in-WiFi-3D 实验项目交接文档

> 生成日期：2026-07-27
> 最近更新：2026-08-11
>
> 原 Claude Code session：`f0a4831c-9ba7-4439-9e53-8a5302c40ce4`

---

## 0. 更新摘要（先读这节）

### 0.000 2026-08-11：5 m 小型动作 Pilot 完成，进入特征分析阶段

**当前进度：采集门控和小样本采集均已完成。不要继续盲目增加 walk。下一步是对
15 条有效主样本做 trial 内归一化、特征可视化和按 trial 分组的消融。**

有效主样本为 `empty`、`walk_link2`、`arm_wave`、`leg_lift`、`sit_to_stand`
各 3 条；另有 1 条固定 50 cm 座椅的专用空场。所有有效试验双端匹配率约
98.99%–99.48%，保留 234 个数据子载波，深度 30 FPS、序号缺口 0，动作段与
前后空场均由 RGB-D 时间线人工核验。

| 动作 | node3 RSSI 标准差倍率 | node3 CSI 100 ms 变化倍率（天线 0） |
|---|---:|---:|
| `walk_link2` | 2.43–2.75× | 3.67–4.69× |
| `arm_wave` | 1.76–1.94× | 2.34–2.84× |
| `leg_lift` | 1.64–1.76× | 2.48–2.64× |
| `sit_to_stand` | 0.94–0.99× | 1.33–1.47× |

结果支持 RSSI 用于强运动门控、CSI 用于较弱动作区分。`sit_to_stand` 使用独立座椅
布局，不能让模型学习静态座椅差异；必须用每条 trial 自身的前后空场做差分或高通
归一化。一条 `leg_lift_05m_pilot_02` 因左侧木门开启已标记无效，原始数据保留但
不得进入训练或验证。

仓库内归档见 `docs/experiments/20260811_pilot/`，机器可读派生特征见
`data/processed/20260811_pilot/`。原始 RGB-D/CSI 仍在 Mac 的
`/Users/d-low/Desktop/Group Project/csi_results/`；深度文件单个约 660–790 MiB，
且视频包含可识别人体，不进入普通 Git 主分支。

### 0.00 2026-08-07：新 5 m 布局的 Link2 动态门控通过，但尚未放行批量采集

**当前结论：采集与同步管线已经稳定，新布局显著改善了 Link2 人体响应；当前瓶颈从
“几乎无人体响应”转为“空场慢漂和动作协议不完整”。这些数据是成功诊断，不是正式训练集。**

相机现接在 `node3`，OAK-D-W 以 USB SUPER 运行。8 月 7 日新布局的四组三级验证如下：

| Trial | 技术质量 | 人体响应与判定 |
|---|---|---|
| `empty_05m_relayout_01` | 共同匹配 98.37%，相机 30 fps、零缺口 | 真实空场；node3 5 秒分段范围 2.57%，稳定性未通过 |
| `empty_05m_relayout_02` | 共同匹配 97.90%，相机 30 fps、零缺口 | 真实空场；node3 5 秒分段范围 3.16%，复测仍有慢漂 |
| `block_link2_05m_relayout_probe_01` | 共同匹配 98.62%，相机同步合格 | 两个静站窗在 node3 重复得到 +3.06% / +2.92%，静态诊断通过 |
| `walk_link2_05m_relayout_probe_01` | 共同匹配 98.07%，234 个数据子载波；CSI–相机配对中位数 8.34 ms | node3 RSSI 动态标准差 2.10× 空场，node1 对照 0.82×；Link2 动态诊断通过，但没有末尾空场 |

走动试验中，node3 两根接收链的 100 ms 子载波变化能量也达到空场的
**1.47× / 1.45×**，而 node1 为 **1.01× / 1.00×**。因此不能把“全频带 CSI
均值变化不大”解释为 CSI 无响应；全频带平均会抵消不同子载波的频率选择性变化。
RSSI 可以作为运动检测、动作分段和质量门控特征，但**不应替代 CSI 幅度/相位**。

同时保留以下负结果：调整后布局的三次 `walk_link1` 中，两次有效诊断的 node1
合并幅度响应仅为 −0.54% 和 −0.39%；旧 Link2 布局的人体响应也只有 +0.22%。
这说明响应高度依赖物理布局，后续必须把节点坐标、高度、天线方向和功率写入元数据。

**下一步门控（不要跳过）：**

1. 保持当前布局不动，进一步固定天线底座、节点和全部线缆。
2. 每次发包后记录 Tx monitor 接口、实际频率和 `iw dev` 报告功率。AX210 发射功率由
   固件/法规控制，接收增益由固件 AGC 管理；PicoScenes `--rx-gain` 仅适用于
   QCA9300，不能把它误记为 AX210 的固定增益。
3. 修正本地语音/动作计时，确保约 5 秒前空场、15–20 秒动作、至少 5 秒后空场。
4. 只重跑一组正式空场和一组正式 Link2 walk。若 node3 动态倍率继续约为 2、
   node1 对照接近 1，且空场慢漂可解释，才开始每类 3 次重复。
5. 所有原始 `.csi`、RGB-D 和失败试验继续保留；用质量标签区分，不人为删除。

Mac 统一数据目录：`/Users/d-low/Desktop/Group Project/csi_results/`。
总评估：`RELAYOUT_VALIDATION_20260807.md`。原始数据约 11 GB，位于 Git 仓库之外，
不要提交到 GitHub。

### 0.01 2026-08-06 现场：采到 3 组有效空场，但发现 CSI 对人体几乎无响应（历史问题）

> **2026-08-20 更新：本节「CSI 对人体遮挡不响应」的结论已被推翻，数据全部有效。**
> 受控实验证明 AX210 的 AGC 会自动补偿遮挡造成的功率损失
> （实测 RSSI −14.00 dB，`|H|` 仅 −0.13 dB，AGC 补偿 +13.88 dB）。
> 下表的 −2.6% 是 **AGC 补偿后的残差**，不代表人体未遮挡信号。
>
> **本节数据用归一化频谱形状重算的结果：**
> `block_test` **11.06%**（空场 2.68%，**4.1×**）；
> 三条 `walk_link1` 分别 **8.70% / 8.84% / 7.49%**（**2.8–3.3×**）。
> 无监督变点检测独立定位到 24.4 s，与 45 s 静站协议吻合。
> **当时判定「不合格」并反复重采三轮，是分析方法造成的误判。**
>
> 另：5 m 链路中点第一菲涅尔区直径 0.79 m，人体躯干宽 0.4–0.5 m，本就挡不满。
> 见 [`docs/experiments/20260820_link_integrity.md`](docs/experiments/20260820_link_integrity.md)。
> **此后分析不得使用 `|H|` 绝对幅度作为遮挡特征；不得使用置换检验。**

**这是 8 月 6 日当时最重要的未解问题；8 月 7 日新布局已在 Link2 上获得改善，见 §0.00。**

首次进场采集。空场基线 3 组合格并保留；`walk_link1` 反复重采三轮仍不合格，
原因不是走位，而是**接收信号对人体遮挡几乎不响应**。

**决定性证据**：一个人整整 45 秒静站在 node2–node1 链路正中间（各 2.5 m），
身体完整挡住直射径 ——

| | node1 平均幅度 |
|---|---|
| 空场 | 144.3 |
| 人静站链路中点 | 140.5 |
| **差异** | **仅 −2.6%** |

正常视距链路上人体遮挡应带来 10–30% 量级的变化。2.6% 基本等同噪声。

**已排除的因素**（不要重复排查）：

* 采集链路正常：匹配率 99.0–99.6%、子载波 234、深度零丢帧、H.265 可解、对齐通过
* CSI 数据是活的：相邻包完全相同的包数 = 0，无全零列、无时间维恒定列
* `nRx=2` 属实：两根天线幅度接近（node1 140.5/138.8，node3 136.3/140.4），均在工作
* 不是走位幅度：横向跨度从 0.54 → 0.69 → 1.04 m，node1 相关性无单调改善
  （−0.098 / −0.215 / −0.137），且符号不稳定

**调整 node1 天线朝向后有改善但仍不足**（`ant_check`，16:19）：

* node1 幅度~深度相关首次出现一致的正相关 **+0.217**（此前符号在正负间跳）
* 有人 vs 无人：node1 **+3.6%**，node3 −1.4%（方向合理：人在 link1 上）
* 但 3.6% 仍太弱，信噪比不足以支撑姿态估计

**推断**：接收信号被多径主导，直射径占比过低。人体遮挡直射径时，
多径叠加的相位关系变化可能相长也可能相消，故响应方向不一致、量级很小。

**当时提出的下次进场优先级**：

1. 检查发射功率与接收增益。5 m 距离信号很强，若 AGC 饱和则动态范围被压死。
   这是最可能的原因，且可在 PicoScenes 侧查证。
2. **直接试 15 m 档**。距离拉大后直射径相对增强、多径相对减弱。
   若 15 m 明显更好，说明 5 m 太近是主因，比在 5 m 上反复调试更有价值。
3. 抬高天线，远离地面/桌面反射。
4. 复核三台天线朝向是否都指向对方节点（node1 已调，node2/node3 未动）。

**验证方法（务必沿用）**：不要只看采集指标，要用深度流标注人在场的时刻，
再比对同一时间窗的 CSI。指标全绿但数据无效的情况今天出现了三轮。
`depth_view.py --profile` 可快速看距离分布；判断有无人用绝对阈值，
不要用相对阈值（场景变化会让相对阈值失效，今天踩过）。

### 0.02 2026-08-06：H.265 彩色流修复 + 本次数据清单

**H.265 预热丢包 bug（已修复）**

`csi_cam.py` 预热阶段用 `while q_color.has(): q_color.get()` 清空彩色队列，
把流开头的 VPS/SPS/PPS + 首个 IDR 一并丢弃。ffmpeg 报
`PPS id out of range` + `Could not find ref with POC`，且无法从中间随机访问。

证据：旧文件第一个 slice 在字节 7（P 帧），第一个参数集要到字节 575190 才出现。
注意默认关键帧间隔本来就是 ~30 帧（1 s），**不是** 关键帧间隔的问题。

修复：预热包改为缓存后写在文件最前面；显式 `setKeyframeFrequency(30)`；
新增 `scan_h265()` 采集当场校验，输出 `CAM_COLOR_DECODABLE` / `CAM_COLOR_NAL`。

**校验必须要求参数集出现在第一个 slice 之前**，只统计出现次数会漏判 ——
第一版就是这么写的，把两个坏文件都判成了"可解码"。

验证：ffmpeg 错误行数 28 → **0**；`-ss 3/5s` 中段随机访问可用。

**本次数据清单**

| 目录 | 状态 |
|---|---|
| `empty_05m_01/02/03` | **有效**。匹配 99.2–99.5%，深度零缺口，空场纯净（深度无移动物体、CSI 幅度 std 5.2–6.4%） |
| `_discarded_20260806/walk_link1_05m_*`（第一批） | **废弃**，见目录内 README.txt。人贴近 node1（1.9–2.5 m）小幅晃动，未达链路中点 |
| 第二批 walk_link1（15:58–16:11） | 走位改善（覆盖中点、检出 452 帧）但横向仅 0.63–1.04 m，CSI 无响应 |
| `block_test`（16:14） | 未按设计执行：人全程站原地看手机，无"离开"对照段 |
| `ant_check`（16:19） | node1 天线调整后，首次出现一致正相关 +0.217，但仅 +3.6% |

这些 `empty_05m` 三组可以保留作早期基线；当时的 `walk_*` 全部无效。
后续新布局诊断结果与正式采集门控见 §0.00。

**一个操作教训**：`./csi_lab.sh batch ... | grep ...` 会因缓冲导致长时间无输出，
看起来像卡死，实际早已跑完。不要给 batch 加管道过滤。

**SSH 中断不等于数据丢失**：16:00 那组回收时 node1 SSH 超时，编排报"相机采集失败"，
但数据在节点上完整（深度 1352 帧、大小精确匹配）。手动 scp 回收 + 重跑
`csi_align.py` 即可救回。遇到回收失败先查节点上的文件再决定是否重采。

### 0.03 2026-08-06：开机后内核自动切到 6.8，已修复并锁定

**现场首次开机即遇到：三台全部自动进入 `6.8.0-136-generic`，而补丁驱动只为 `6.5.0-15-generic` 编译过。**
若未察觉直接采集，会在 logger 阶段报 `Unresolvable device ID`，或取到无 CSI 的空数据。

根因：系统此前装入 `6.8.0-136-generic`，而 `GRUB_DEFAULT=0` 指向菜单第一项（总是最新内核）。
与关机方式无关，也不是配置丢失。

| 项 | 处理 |
|---|---|
| `GRUB_DEFAULT` | 改为 `<子菜单ID>>>><6.5条目ID>`，**用条目 ID 不用数字索引**，再装新内核也不会错位 |
| 备份 | `/etc/default/grub.bak.20260806`（三台均有） |
| 包锁定 | `apt-mark hold linux-image-6.5.0-15-generic linux-headers-6.5.0-15-generic` |
| 验证 | 三台 `uname -r` = `6.5.0-15-generic`，`modinfo iwlwifi` srcversion = `1DEF5F9EE1363E0C6199DBF` |

**注意条目 ID 每台不同**（各自根分区 UUID）：node1 `5ae2b797-…`、node2 `ad440e91-…`、node3 `63037fb1-…`。
不要跨机复制 `GRUB_DEFAULT`。

**srcversion 是判断驱动是否为补丁版的可靠依据**：补丁版 `1DEF5F9EE1363E0C6199DBF`；
6.5 内核树原版 `D8F82ADA16345109182A810`；6.8 原版 `0BA0F69002F5E2114D137A0`。
只看 `uname -r` 不够，要看 srcversion。

`update-grub`、`sed -i /etc/default/grub`、`apt-mark` **不在 sudo NOPASSWD 白名单内**，需用 `sudo -S` 喂密码。

**每次现场开机后，第一件事仍是 `./csi_lab.sh check`** —— 它已内置内核/驱动匹配检测，本次正是由它报出 node2 异常。

### 0.1 2026-08-05：深度相机接入，两套脚本合并为 `csi_lab`

**深度相机确认为 Luxonis OAK-D-W（不是 Azure Kinect）**，已完成采集链路并与 CSI 联合验证。
同时把「Mac 端远程编排」与「node2 端包对齐」两套脚本合并成一套 `csi_lab`，
消除版本漂移这个 bug 来源。

| 成果 | 数据 |
|---|---|
| 相机 | OAK-D-W，deviceId `19443010A17D1B1300`，MyriadX，USB 2.0 |
| 深度采集 | 640×360 对齐 RGB，30.00 fps，序号缺口 0 |
| 彩色采集 | 1080p H.265 硬编码，约 1.1 MB/s |
| 相机内参 | fx=fy=381.49，cx=317.62，cy=186.96，基线 7.5 cm |
| 端到端 | 匹配 2851 包，98.8% / 96.5%，234 数据子载波，深度 905 帧 |
| 批量模式 | 3 组连续采集全部通过，跨组 Wi-Fi 不反复恢复 |

**推翻 / 修正的四条既有结论：**

| 旧说法 | 修正 |
|---|---|
| 实际包率约 **400 pkt/s** | **错误，实际就是 200 pkt/s**。旧数字把环境流量算进去了：node1 总帧 10991 中仅 5842 来自本项目发射端，其余为环境 Wi-Fi。10991/30≈366≈"400" |
| sequence 每 **~10 秒**回绕 | 200 pkt/s 下 12 位 seq 回绕周期为 **20.5 秒** |
| Azure Kinect SDK 不支持 macOS，是头号 blocker | **前提不成立**。实际硬件是 OAK-D-W，DepthAI 同时支持 Linux 与 macOS。该 blocker 解除 |
| Mac 与三台节点脚本校验和一致 | **8-4 起已不成立**。队友更新过节点端脚本，Mac 副本停留在 7-31。现已由 `csi_lab` 统一 |

**新确立的关键事实：**

1. **CSI 时间戳位于 `CLOCK_REALTIME`（epoch 纳秒），来自 `RxSBasic` v≥4 的 `system_ns`**。
   深度帧时间戳位于 `CLOCK_MONOTONIC`。换算关系：
   `epoch_ns = monotonic_ns + (CLOCK_REALTIME - CLOCK_MONOTONIC)`，
   该偏移由 `csi_cam.py` 在采集前后各采样一次，实测 10 秒漂移仅 62 ns。

2. **节点间时钟偏差可以每 trial 直接实测，不需要 NTP/chrony。**
   两接收端收到的是同一个广播包，其 `system_ns` 之差即为时钟偏差。
   实测同一会话内 2.42 → 2.44 → 2.47 ms（约 3 分钟），相对漂移约 **0.3 ppm**，
   30 秒 trial 内变化约 8 µs，可视为常数。
   但**跨会话会变**（6.53 ms / 14.12 ms / 3.02 ms 均出现过），必须每次测。

3. **相机必须接在接收端（node1 或 node3），不能接在 node2。**
   接收端方案下深度与该端 CSI 天然同钟，另一端偏差可实测；
   接 node2 则 node2 只发不收、没有 `system_ns` 记录，偏差**无法测量**。

4. **相机配对误差指标有饱和陷阱。** 30 fps 下最近帧的量化误差本身均匀分布在
   ±16.67 ms，理论中位数恰为 8.33 ms。实测 8.25–8.38 ms 正是这个值，
   **该指标测不出时钟偏差**——偏差超过半帧时它照样好看，但帧会系统性配错。
   不要用它来验证同步质量。

5. **相机的硬约束（实测）：**
   - USB 2.0 供电，功耗余量极小
   - 深度 640×360 + 1080p H.265 → 稳定，约 15.0 MB/s
   - 深度 + **原始** 1080p 彩色 → **设备崩溃**
   - 深度 + **4K** H.265 → **主机断电级硬复位**（零日志，`last` 记为 `crash`）
   4K 与原始彩色为永久禁区。建议配有源 USB Hub。

6. **`StereoDepth` 默认把输出降采样到 320×200**，且 `setOutputSize()` 仅在
   `setDepthAlign(CAM_A)` 之后才生效。必须显式设置，否则拿到的是半分辨率。

### 0.2 2026-07-31：远程编排打通，`1-Tx-2-Rx` 首次取得实证

**有线管理网 `192.168.50.0/24` 已完全可用**，三节点均可从 Mac 免密 SSH、下发脚本、回收数据。
**首次实测到两个接收端同时采集同一广播流**，并完成 packet 级对齐。

| 成果 | 数据 |
|---|---|
| 一发二收 | node1 收到自有帧 7,173，node3 收到 7,296 |
| packet 对齐 | 配对 7,056，占 node1 自有帧 98.4% / node3 96.7%，联合覆盖 95.2% |
| 链路质量 | 丢包 1.8%–3.3%，无重复、无乱序 |

排除的四个独立故障（详见 §10）：节点未装 sshd；管理网段无 DHCP 导致接口整体消失；
eduroam 客户端隔离；**`array_prepare_for_picoscenes` 静默失败**。

同时**推翻了 2026-07-30 的两条结论**：

| 07-30 的说法 | 修正 |
|---|---|
| PicoScenes 与 array_prepare 都不需要 root | **只有 PicoScenes 拒绝 root**。array_prepare 内部调用 `sudo ifconfig/iw/route`，必须配 NOPASSWD |
| 导频列是"恒定高出约 9 dB"的常数 | 更准确：**导频列 CSI 恒为 0 或占位常数，完全无信息**，且不同会话取值不同 |

### 0.3 2026-07-30：数据解析层的三个纠正

| 更早的说法 | 现状 |
|---|---|
| 首份样本形状 `(45601, 52, 2, 1)` | **错误**。52 是 CSIKit 截断 bug 的产物，真实为 242 tone |
| tone 数 234 与 242 定义不明 | **已解决**。242 = 234 数据 + 8 导频 |
| packet 对齐必须走原生 MATLAB | **前提不成立**，纯 Python 即可，且已于 07-31 实测通过 |

---

## 1. 给接手 AI 的指令

你正在接手 Ganmin Wang 的毕业设计实验项目。请把本文档作为当前项目事实的主要来源，并遵守以下规则：

1. 先区分「已验证」「用户报告」「计划中」「已推翻」，不要把计划写成成果。
2. 当前已实现的是 CSI 数据采集基础设施，不是完整的 3D 姿态估计系统。
3. `node1`/`node3` 已实现**并发采集 + packet 级对齐**（2026-07-31，见 §0.1），
   但**尚未做跨设备 phase 校准**，不要称"已同步"或直接融合两端相位。
4. 执行命令前先确认节点当前状态、物理连接、内核版本、`PhyPath`、管理网络和文件位置。
5. 不要修改或删除原始 `.csi`。解析结果写到新文件或独立结果目录。
6. 涉及重装系统、擦除磁盘、改内核/网络/驱动/固件或大量覆盖文件时，必须先说明影响并获得确认。
7. 当前优先级是可重复、可记录、可对齐的采集管线，不是训练模型或追求 MPJPE。
8. **任何用 `csi_inspect.py` 产出的 `.npy` 都不可信**（见 §6.1），分析一律从原始 `.csi` 重新开始。
9. **不要相信 `array_prepare_for_picoscenes` 的退出码**，它失败时也返回 0（见 §10.7）。
   任何涉及采集的操作，都要用 `iw dev` 校验 monitor 接口与信道的真实状态。
10. 采集用 `~/csi_lab/csi_lab.sh`，不要手工敲 PicoScenes 命令——历史上大批数据
    因为手工操作而误用单播、缺失元数据。旧的 `run_experiment.sh` 与
    `run_1tx2rx.sh` 仅作历史保留，**不要再用**（见 §9.2）。
11. **相机时间戳与 CSI 时间戳属于不同时钟域**，换算见 §0.1。不要直接比较两者的
    原始数值。
12. **不要用 `CAM_PAIR_DT_*` 指标判断时钟同步质量**，它被 30 fps 量化误差饱和
    （见 §0.1 第 4 条）。

## 2. 项目一句话概括

使用 Intel AX210、LattePanda Mu 和 PicoScenes 搭建 `1-Tx-2-Rx` Wi-Fi CSI 多链路实验平台，为后续采集同步的 CSI 与深度图真值、构建 Person-in-WiFi-3D 数据集和 3D 人体姿态估计提供数据采集基础。

## 3. 项目目标与范围

参考论文：`Person-in-WiFi 3D: End-to-End Multi-Person 3D Pose Estimation with Wi-Fi`，CVPR 2024。
本地：`/Users/akiyamarinko/Downloads/2024CVPR_Person_in_WiFi_3D.pdf`

用户属于 Group 1（实验系统组），责任：

- 搭建 Wi-Fi sensing 环境和硬件测试床
- 可靠采集 Wi-Fi CSI
- 采集深度图 ground truth
- 解决多接收端对齐与 CSI/深度同步
- 建立可复现的采集、记录、解析、备份流程
- 交付可用原型和可靠采集管线

**不属于** Group 1 当前考核指标：复现论文训练结果、论文级 MPJPE、端到端多人 3D pose 训练、发布大规模标注数据集。

## 4. 当前系统架构

```text
                     HE-SU packets, 20 MHz, 2.4 GHz ch1, ~200 pkt/s

      node1 (Rx/logger)  <---  node2 (Tx/injector)  --->  node3 (Rx/logger)
             |                                                  |
             +--------------- PicoScenes .csi -------------------+
                                    |
                      mac_header.py  +  patched CSIKit
                   （MAC 头/seq）      （CSI 矩阵）
                                    |
                      按 seq 号做 packet-level 对齐
                                    |
                        CSI + depth 时间配对（已完成）
                                    |
                    场地外参/3D 骨骼标签（尚未完成）
                                    |
                      Person-in-WiFi 模型适配（未开始）
```

### 4.1 硬件

| 项目 | 当前配置 |
|---|---|
| 计算节点 | LattePanda Mu x3，Intel N100、8 GB RAM、64 GB eMMC |
| CSI 网卡 | Intel AX210 x3，M.2 E-key 2230 |
| 天线 | 记录为每卡一根 2.4 GHz 天线；`nRx=2` 与实际天线数是否一致**仍未核实** |
| 发射节点 | `node2`，AX210 MAC = `E0:D5:5D:4D:01:E8` |
| 接收节点 | `node1`、`node3` |
| 深度相机 | 硬件已到手，**型号与连接方式尚未确认**，仍是交付的关键前置 |
| 管理网络 | **已建成并验证**，见 §4.4 |

### 4.2 节点身份

| 逻辑节点 | 主机/用户 | 角色 | 管理 IP | AX210 MAC |
|---|---|---|---|---|
| `node1` | `werner` / `werner-ADL-N` | Rx1、主分析节点 | `192.168.50.11` | `20:bd:1d:75:4d:38` |
| `node2` | `alfonse` / `alfonse-ADL-N` | **Tx** | `192.168.50.12` | `e0:d5:5d:4d:01:e8` |
| `node3` | `kaminashi` / `kaminashi-ADL-N` | Rx2 | `192.168.50.13` | `e0:d5:5d:56:09:41` |

`e0:d5:5d:4d:01:e8` 即数据中 addr2（源地址）出现的值，硬件与数据分析在此互相印证。
三台的有线管理网卡均为 `enp2s0`，AX210 均为 `wlp1s0`。

**注意用户名与节点的对应关系**：`alfonse` 是 **node2 发射端**，不是 node1。
曾因误用 `werner@` 连接 node2 而反复出现 `Permission denied`——用户不存在时
OpenSSH 一律回该错误，不会提示"无此用户"。

### 4.3 软件栈

| 项目 | 当前选择 |
|---|---|
| OS | Ubuntu 22.04.5 amd64 |
| CSI 工具 | PicoScenes |
| 采集内核 | `6.5.0-15-generic`（driver 必须匹配） |
| Python 解析 | CSIKit 2.5 + 本地 RxSBasic 补丁（v4/v5 → parseV3） |
| MAC 头/seq 解析 | `mac_header.py`（本项目自建） |
| 原生解析 | Windows PicoScenes MATLAB toolbox，用户报告可读，**未做过定量比对** |
| 信道 | `2412 20`（2.4 GHz ch1、20 MHz） |
| PHY preset | `TX_CBW_20_HESU` |
| packet spacing | `5000 us`，约 200 pkt/s |

### 4.4 有线管理网（2026-07-31 建成）

```text
Mac 192.168.50.1  ──┐
node1 .11 ──────────┤  哑交换机（无 DHCP、无网关）
node2 .12 ──────────┤  Mac 侧经 USB 网卡 en5，实测 100BASE-TX 全双工
node3 .13 ──────────┘
```

- 全部静态 IP，通过 NetworkManager 的 `mgmt` 连接配置，`ipv4.never-default yes`
  确保不抢占默认路由，节点与 Mac 的正常上网不受影响。
- Mac `~/.ssh/config` 中已有 `node1` / `node2` / `node3` 别名，密钥 `~/.ssh/id_ed25519`。
- **必须走有线**：`array_prepare_for_picoscenes` 会把 AX210 切入 monitor 模式，
  任何经 AX210 建立的 SSH 都会断开。
- **eduroam 不可作为管理通道**：实测存在客户端隔离，同网段两台机器也互相不可达。
- 该网段没有网关，节点在管理网上无法访问外网。

## 5. 已验证的成果

以下有本地文件或命令输出直接支持：

1. 三节点 AX210 曾被 `array_status` 识别，`PhyPath` 均为 `1`。
2. `node2 → node1` 的受控 `1-Tx-1-Rx` 采集稳定可重复，本地共 **8 份 `.csi`**。
3. **链路质量良好**：按 802.11 sequence number 实测，正常 trial 丢包率 **1.0%–1.9%**，无重复帧、无乱序。
4. **子载波结构已确定**：HE-SU 20 MHz 为 **242 tone = 234 数据 + 8 导频**，导频位于索引 `6, 32, 74, 100, 141, 167, 209, 235`。旧文档中 234 与 242 的矛盾至此消解。

   **导频列不含任何可用信息，必须剔除**（2026-07-30 多次采集实测）：
   - 通常整列 CSI 恒为 0，取 dB 后为 `-inf`；
   - 偶尔是固件填的占位常数（一次采集内只有 1~2 个不同取值，标准差近 0）；
   - 不同采集会话的填充值不同（观察到 52.4 dB 与 36.4 dB）。
   
   因此若把 242 列整体喂给模型，这 8 列会成为「数据来自哪次采集」的指纹，
   造成信息泄漏，训练指标虚高而换会话即崩。**模型输入用 234 个数据子载波。**
   
   另一个有用的结论：剔除导频列后，**234 个数据子载波非有限值占比为 0.0000%**，
   下游无需再做 finite mask。采集中出现的非有限值恰好等于 `8/242 = 3.3058%`，
   全部来自导频列。
5. **发射端可逐帧确认**：MAC 头 addr2 = `E0:D5:5D:4D:01:E8` 即 node2，可精确区分自有流量与环境流量。
6. **sequence number 可直接提取**，99.03% 严格 +1，为 packet-level 对齐提供现成主键。
7. 解析管线可产出 amplitude、phase、NPY、PNG、CSV、report。
8. PicoScenes 全局库路径破坏 snap/Firefox 的问题有确定修复（§10.2）。
9. **有线管理网建成**（§4.4）：三节点静态 IP、SSH 免密、sudo 白名单、脚本分发、
   数据回收全部可用，Mac 端一条命令完成采集。
10. **`1-Tx-2-Rx` 并发采集实测通过**（2026-07-31，25 秒广播）：
    node1 自有帧 7,173、node3 自有帧 7,296，两端目的地址均为广播，
    子载波结构一致，丢包 3.3% / 1.8%。
11. **跨接收端 packet 级对齐实测通过**：配对 7,056 包，
    占各自自有帧 98.4% / 96.7%，联合覆盖 95.2%。纯 Python，不依赖 MATLAB。
12. **实际包率确认为 200 pkt/s**（`--delay 5000 µs` 的名义值，实测吻合）。
    2026-08-04 的 30 秒采集：`system_ns` 首末相差 29.976 s，
    node1 收到本项目发射端的包 5842 个 → 195 pkt/s，node3 5918 个 → 197 pkt/s。
    发包量 `--repeat 6000` 恰好跑满 30 秒。

    > **注意**：本文档曾长期记载「实际约 400 pkt/s」，那是错的。
    > 该数字把环境流量算了进去——node1 总帧 10991 中只有 5842 来自本项目发射端，
    > 其余为环境 Wi-Fi，10991/30 ≈ 366 ≈「400」。
    > 任何包率统计都必须先按发射端 MAC 过滤。

13. **深度相机链路已打通**（2026-08-05），OAK-D-W，与 CSI 联合采集验证通过。
    详见 §0.1 与 §9.2。

14. **相机采集的数据量远大于 CSI**：深度约 14.1 MB/s，是同期 CSI 的数十倍。
    一个 30 秒 trial（相机按 rx 时长跑 45 秒）约 750 MB，其中深度占 85%。

## 6. 已推翻或不能声称的部分

### 6.1 CSIKit 两个 bug（重要）

**bug 1 — 静默截断。** `csitools.get_CSI()` 按**第一帧**的宽度分配输出数组。monitor 模式下首帧常是环境中的 legacy 帧（52 tone），于是后续 242 tone 的 HE-SU 帧被静默截断成前 52 列，而这 52 列混合了两套完全不同的物理子载波。不报错、不警告。

- 这是旧文档 `(45601, 52, 2, 1)` 的真正来源，不是「解析约定差异」。
- **后果：所有由 `csi_inspect.py` 产出的 `.npy` 都受污染，不可用于任何分析。**
- 修复：建数组前先按 MAC 过滤，只保留自有流量。过滤后首份样本得 `(36435, 242)`，非有限值占比从约 17% 降到 0.001%。

**bug 2 — MAC 字段标错。** `read_pico.py:85` 取 MAC 头偏移 `[4:10]` 并命名为 `source_mac`，但该偏移是 **addr1（目的地址）**。判据：广播采集中该字段为 `FF:FF:FF:FF:FF:FF`，而广播地址不可能作为源地址。真正的源地址在 `[10:16]`。用 `mac_header.py` 代替。

### 6.2 `1-Tx-2-Rx` —— 2026-07-31 已取得实证

早期文档中的该结论一度缺乏本地证据（无任何 node3 文件，`trials_log.txt` 4 条记录
全为 `1Tx-1Rx`）。**2026-07-31 已实测通过**，见 §0.1。

历史数据仍存在的问题（这些文件不要用于正式分析）：
- 8 份旧 `.csi` 中只有 `node1_empty.csi` 使用广播目的地址，其余为默认单播
  `00:16:EA:12:34:56`（该值实为 array_prepare 给主接口设置的 PicoScenes 默认 MAC）。
- 所有由 `csi_inspect.py` 产出的 `.npy` 均受 §6.1 截断 bug 影响。

### 6.3 原始数据缺失

`node1_walk_link1` 的 `_amp.npy`、`_phase.npy`、`_report.txt`、`_amp_phase.png` 均在，**但 `.csi` 不存在**。唯一一次 walk trial 的原始文件已丢失，而现存 npy 又受 bug 1 污染——**该 trial 不可重生，须重做**。

### 6.4 其余未完成项

- 未完成跨设备 phase calibration 或 phase fusion
- 深度相机已到手，但**型号、SDK、接哪台机器、同步方式全未确定**
- 未实现 CSI 与 depth frame 的同步、标定、配对
- 未形成可训练带标签数据集
- 未完成论文模型输入层对 AX210 CSI 维度的适配
- 未训练模型、无定量 pose 结果
- 未完成 `empty` / `walk_link1` / `walk_link2` 的多轮受控重复实验
- **节点物理布局未固定也未记录**，`walk_link1` / `walk_link2` 无法保证可比性
- **不能仅凭幅度扰动声称检测到人体动作**（见 §7.7）

已于 2026-07-31 完成、不再属于未完成项：远程 SSH 编排、`1-Tx-2-Rx` 并发采集、
跨接收端 packet 对齐。

## 7. 关键技术决策

### 7.1 使用 AX210 + PicoScenes，而非 Intel 5300 工具链
论文原系统用 Intel 5300，现有硬件是 AX210，属硬件适配而非逐硬件复刻。

### 7.2 使用原生 Ubuntu 22.04.5
PicoScenes 驱动依赖原生 Linux。Windows、WSL、Ubuntu 24.04 均不适合作采集节点。

### 7.3 优先 `1-Tx-2-Rx`
单发射端避免注入碰撞，两接收端观察同一 packet stream，便于按 sequence number 对齐。

### 7.4 多接收端使用广播（保留，但原因存疑）

现行做法：injector 显式加 `--target-mac-address FF:FF:FF:FF:FF:FF`，已固化进
`csi_node.sh`，不再依赖手工输入。

**但"单播导致只有一个 receiver 采到数据"这个解释很可能是误诊**，理由有二：

1. monitor 模式本身就是混杂接收，网卡会收下空中所有帧，与目的地址无关。
2. 历史单播 trial 中 node1 收得很正常（`node1_static` 5,674 帧、
   `rx_1_260703` 36,437 帧）。若单播会阻断 monitor 接收，node1 也该收不到。

更可能的真实原因是 §10.7 的 `array_prepare` 静默失败——某台节点的 monitor 接口
没有信道，于是"后接收的那台收不到包"。

**该结论尚未实证。** 待做对照实验：两端均确认 monitor 接口在 2412 MHz 后，
分别用广播和单播各跑一次，观察两端是否都有数据。
无论结论如何，广播在多接收端场景下语义正确且无代价，应继续保留。

### 7.5 第一阶段优先分析 amplitude
跨设备 raw phase 受 CFO、SFO、STO、CPE 影响，不能直接拼接或相减。先用 `abs(CSI)`，相位校准放后续。

### 7.6 packet 对齐走 Python，不依赖 MATLAB（2026-07-31 已实测）

旧版认为必须从 PicoScenes 原生结构提取 sequence number。**该前提不成立。**
802.11 sequence control 字段就在 MAC 头偏移 `[22:24]`，低 4 位 fragment、高 12 位 sequence。

实测（node1 ↔ node3，25 秒广播采集）：配对 **7,056** 包，占各自自有帧
**98.4% / 96.7%**，联合覆盖 **95.2%**。

MATLAB 原生工具仍可作为独立交叉验证手段，但**不再是阶段 D 的前置条件**。

两个必须注意的实现细节：

1. **先按发射源筛选，再对齐。** 空口上有大量其他设备的流量，各自维护独立的
   sequence 计数器；混在一起会同时破坏 unwrap 和求交集。曾因未筛选而得到
   33.7% 的虚假 coverage，筛选后为 98.4%。
2. **sequence 是 12 位，每 4096 包回绕一次。** 包率 200 pkt/s（实测确认，见 §5.12），
   即**每 20.5 秒回绕一次**，任何超过 20 秒的采集都必须处理回绕。

> **2026-08-05 更新：现行方案已不再依赖 unwrap。**
>
> 队友的对齐实现改用四元组 `(source_mac, sequence, fragment, task_id)` 作为
> 对齐键。`task_id` 来自 MPDU 内偏移 24 处的 PicoScenes 头（magic `0x20150315`），
> 负责跨 sequence 回绕消歧，因此不需要 unwrap 也不会因回绕而误配。
> 实测 5842 个包 `duplicate_keys = 0`，30 秒采集（跨越一次回绕）零冲突。
>
> 该逻辑已并入 `~/csi_lab/csi_align.py`。`mac_header.py` 与其 `unwrap_seq()`
> 仅作历史保留。

### 7.7 单次扰动不足以支撑动作检测结论

首份样本在 135–146 s 出现明确信道变化，但性质是**频率倾斜**而非宽带衰减：低频段约 −9.0 dB、高频段约 +5.0 dB、全带均值仅 −1.38 dB。倾斜排除了 AGC 增益变化（那会使所有 tone 同向平移），指向多径结构改变。

但该结论基于**单次、无同步真值、无重复**的采集，只能作为「系统对环境变化敏感」的证据，**不能作为人体动作检测成立的证据**。

## 8. 基准命令

每台节点先确认：

```bash
lsb_release -a          # 期望 Ubuntu 22.04.x / jammy
uname -r                # 期望 6.5.0-15-generic
array_status            # AX210 可见，PhyPath 需实际核对
```

三节点设置相同信道：

```bash
array_prepare_for_picoscenes 1 "2412 20"
```

`node1` 与 `node3` 启动 logger：

```bash
PicoScenes "-d debug -i 1 --mode logger --plot"
```

`node2` 启动广播 injector：

```bash
PicoScenes "-d debug -i 1 --mode injector --preset TX_CBW_20_HESU \
  --repeat 1e5 --delay 5e3 --target-mac-address FF:FF:FF:FF:FF:FF"
```

注意事项：

- 信道用 `"2412 20"` 两参数形式。旧文档中 `"2437 20 2437"` 三参数形式会导致 `iw set freq` 失败。
- `--target-mac-address FF:FF:FF:FF:FF:FF` **不可省略**，否则退回单播默认值 `00:16:EA:12:34:56`。
- `array_prepare_for_picoscenes` 会改变 AX210 的普通 Wi-Fi 状态，可能断开经该网卡的 SSH。远程控制必须走独立有线管理网。
- **`PicoScenes` 绝不能用 sudo 运行**，它会拒绝并崩溃：`Don't run PicoScenes with root privilege!`
- **执行前必须先让 NetworkManager 交出 AX210**（`nmcli device set wlp1s0 managed no`），
  否则 monitor 接口的信道设置会被顶掉，见 §10.7。
- 停止 logger 用 `Ctrl+C` 或 `SIGINT`，否则 `.csi` 不会正常保存。

### 8.1 日常用法（2026-08-05 起，用 `csi_lab`）

不要再手工敲上面这些命令，也不要再用 `run_experiment.sh` / `run_1tx2rx.sh`。
统一入口是 `~/csi_lab/csi_lab.sh`，它已内置全部校验。

```bash
cd ~/csi_lab

./csi_lab.sh check                          # 只做预检，不采集
CAM_HOST=node3 ./csi_lab.sh check           # 连当前接在 node3 的相机一起预检

CAM_HOST=node3 ./csi_lab.sh empty_01 30     # 单次采集，带相机
./csi_lab.sh empty_01 30                    # 单次采集，不带相机

CAM_HOST=node3 ./csi_lab.sh batch phase_c.txt   # 批量采集（通过 §0.00 门控后）

./csi_lab.sh restore                        # 恢复三台 Wi-Fi
```

**环境变量**

| 变量 | 默认 | 说明 |
|---|---|---|
| `CAM_HOST` | 空 | 相机宿主，当前为 `node3`。留空则不采 RGB-D；RGB-D 尚需转换并验证为 3D 骨骼标签 |
| `NO_RESTORE` | 0 | 采集后不恢复 Wi-Fi（批量内部自动使用） |
| `TX_NODE` / `RX_A` / `RX_B` | node2 / node1 / node3 | 角色分配 |
| `RX_MARGIN` | 15 | 接收端比发射端多跑的秒数；调小可省空间 |
| `BATCH_GAP` | 10 | 批量模式两组之间的准备时间 |

结果落在 `~/csi_results/<时间戳>_<模式>_<trial>/`，模式为 `1t2r` 或 `1t2r_cam`。

**批量文件格式**（`#` 开头为注释）：

```
# trial名      秒数   重复次数
empty            30      3
walk_link1       30      3
```

批量模式中途某组失败会自动跳到下一组，不中断整批；全批结束才恢复 Wi-Fi。

**修改脚本后必须重新分发：**

```bash
cd ~/csi_lab
for n in node1 node2 node3; do scp -q csi_agent.sh csi_cam.py $n:~/ ; done
# 校验三台一致
for n in node1 node2 node3; do ssh $n 'sha256sum ~/csi_agent.sh ~/csi_cam.py'; done
```

> 历史教训：2026-08-04 队友更新了节点端脚本但未回流 Mac，导致两边版本漂移，
> 进而产生「`tx` 不传时长会静默只发 30 秒」这个无报错的数据缺陷。
> 每次改动后务必核对三台校验和。

## 9. 本地文件

### 9.1 参考文件

| 路径 | 用途 | 注意事项 |
|---|---|---|
| `~/person-in-wifi-3d-handoff.md` | 本文档 | 新会话首先阅读 |
| `~/Downloads/2024CVPR_Person_in_WiFi_3D.pdf` | 原论文 | — |
| `~/Downloads/one-tx-two-rx-plan.md` | 实验计划、记录字段、成功标准 | 最有用的计划文档 |
| `~/Downloads/picoscenes-node-setup-SOP.md` | 安装与故障排查 | 第 108 行仍有过时三参数信道命令 |
| `~/Downloads/first-csi-test-procedure.md` | 早期双节点流程 | 第 18 行仍有过时三参数信道命令 |

### 9.2 采集与编排脚本

全部于 2026-07-31 重写，旧版备份在 `~/Downloads/old_scripts/`。

| 路径 | 位置 | 用途 |
**现行脚本集（2026-08-05 起，唯一在用的一套）**

| 路径 | 位置 | 用途 |
|---|---|---|
| `~/csi_lab/csi_lab.sh` | Mac | **编排入口**：预检 → 先收后发 → 回收 → 对齐 → 恢复。含相机与批量模式 |
| `~/csi_lab/csi_align.py` | Mac | 包对齐 + 导频剔除（242→234）+ 相机时间配对 |
| `~/csi_lab/csi_agent.sh` | 分发到三节点 `~/` | `check` / `check-rx` / `check-cam` / `tx` / `rx` / `cam` / `stop` / `restore` |
| `~/csi_lab/csi_cam.py` | 分发到三节点 `~/` | OAK-D 深度+彩色采集，含内参与时钟偏移 |
| `~/csi_lab/field.sh` | Mac | **现场引导脚本**：网络诊断 → 预检 → 相机 → 试采 → 三档距离批量。把布局记录做成不可跳过的一环 |
| `~/csi_lab/phase_c_05m.txt` 等 | Mac | 三档距离的批量配置 |
| `csi_plot.py` | 三节点 `~/` | 接收端本地出图，并产出对齐所需的 `target_csi.npz` |

`csi_lab` 由两套前身合并而来，各自保留的部分：

- **来自队友的 `run_1tx2rx.sh` / `align_1tx2rx.py`**：四元组对齐键、`system_ns`
  时钟测量、发包前确认两个接收端的 PicoScenes 确实在跑、对齐后 NPZ 完整性校验。
- **来自 Mac 端 `run_experiment.sh`**：Mac 主控、带模式标签的结果目录、
  回收后大小校验、`NO_RESTORE` 开关。
- **来自队友的 `csi_node.sh`**：`do_prep` 静默失败校验、`do_stop` 的 tx/rx 分离、
  `do_restore` 用 `ethtool -P` 取回硬件 MAC。**这三处是踩坑换来的，不要改动。**

**历史保留（不要再使用）**

| 路径 | 位置 | 说明 |
|---|---|---|
| `~/Downloads/run_experiment.sh` | Mac | 旧 Mac 端编排。调用 `tx` 不传时长，配新版 agent 会静默只发 30 秒 |
| `~/Downloads/csi_node.sh` | Mac | 7-31 版节点脚本，已被队友 8-4 版取代 |
| `node2:~/run_1tx2rx.sh`、`node2:~/align_1tx2rx.py` | node2 | 队友的 node2 主控版本 |
| `node2:~/桌面/之前的版本/` | node2 | 队友归档的 7-31 版，与 Mac `~/Downloads` 一致 |
| 三节点 `~/csi_node.sh` | 节点 | 8-4 版，已被 `csi_agent.sh` 取代 |
| `~/Downloads/bootstrap_node.sh` | 节点本机运行一次 | 新节点接入管理网（静态 IP、SSH、公钥），仍然有效 |
| `~/Downloads/csi_run.sh` | 节点 | 更早的本地交互式脚本 |

新版相对旧版修掉的问题：

| 旧版行为 | 后果 | 现状 |
|---|---|---|
| `scp "*_${TRIAL}*.csi"` + `2>/dev/null` | 同名 trial 直接覆盖且错误被吞，**疑为 `node1_walk_link1.csi` 丢失的原因** | 结果目录带时间戳，目标已存在即中止，回收后比对文件大小，节点原文件保留 |
| `pkill` / `pkill -9` | SIGTERM/SIGKILL 不保证 `.csi` 落盘 | `SIGINT` + 轮询确认退出，超时才强杀并明确告警；发射端与接收端分别处理 |
| 靠"最新的 .csi"猜产出文件 | 并发或异常时认错文件 | 每个 trial 独立空目录，产出唯一确定 |
| 全流程无错误检查 | ssh 失败、PicoScenes 崩溃都报"完成" | 每步校验返回码，失败即中止并清理 |
| 信任 `array_prepare` 的退出码 | **静默失败，采到废数据**（§10.7） | 直接查 `iw dev` 校验 monitor 接口与信道 |
| 调用 `csi_inspect.py` 出图 | 图基于被截断的 52 列，是错的 | 换成 `csi_plot.py` |
| 无元数据 | `trials_log.txt` 靠手填，大量 `?` | 自动写 `metadata.txt` 与 `experiment.txt` |

### 9.3 解析脚本

| 路径 | 位置 | 用途 | 状态 |
|---|---|---|---|
| `~/Downloads/mac_header.py` | Mac | MAC 头 / sequence / 跨接收端对齐 | 阶段 D 主力工具 |
| `~/Downloads/csi_plot.py` | 分发到接收端 | 采集后本地出图与摘要 | 自动检测无信息列 |
| `~/Downloads/reparse_controlled.py` | Mac | 按 MAC 过滤重解析历史数据 | 可用 |
| `~/Downloads/csi_inspect.py` | — | 旧解析脚本 | **受截断 bug 影响，勿再使用** |
| `~/Downloads/csi_export_csv.py` | — | NPY 转 CSV | 硬编码「52 个子载波」，需改 |

`csi_plot.py` 逐帧读取 `csi_matrix` 并按「目的地址 + 子载波数」筛选，
**不使用 `csitools.get_CSI()`**，从根本上规避 §6.1 的截断 bug。
每次采集后自动输出：目的地址是否广播、子载波数、无信息列位置、有效子载波数、
帧构成、幅度中位数，并存一份已过滤的 `_amp_clean.npy`。
| `~/Downloads/csi_phase_sanitize.py` | 逐帧 unwrap / detrend | 有效性未验证 |

### 9.4 原始数据清单（2026-07-30 实测）

| 文件 | 总帧 | 自有帧 | 目的地址 | 丢包 | 时长 |
|---|---|---|---|---|---|
| `FirstCSI/rx_1_260703_134955.csi` | 45601 | 36437 | 单播 | 1.0% | 182 s |
| `csi_data/empty_260705_144954.csi` | 7384 | 4950 | 单播 | 1.3% | 25 s |
| `csi_data/empty2_260705_161305.csi` | 8257 | 4917 | 单播 | 1.7% | 25 s |
| `csi_data/node1_Yifei1empty.csi` | 7991 | 5654 | 单播 | 1.4% | 28 s |
| `csi_data/node1_static.csi` | 9069 | 5674 | 单播 | 1.6% | 28 s |
| `csi_data/node1_empty.csi` | 6898 | 5629 | **广播** | 1.9% | 28 s |
| `csi_data/empty2_260705_160718.csi` | 2 | 1 | — | — | 废弃 |
| `csi_data/node1_ChenZhuoTest.csi` | 46443 | — | 无自有流量 | 76% | 废弃 |
| `csi_data/node1_walk_link1.csi` | — | — | — | — | **文件缺失** |

## 10. 已知问题

### 10.1 内核与 PicoScenes driver 不匹配
症状：`array_status` 能看到 AX210，`array_prepare_for_picoscenes` 能建接口，但 logger/injector 报 `Unresolvable device ID`。
检查 `apt-cache search picoscenes-driver-modules` 与 `uname -r`。不要盲目升级内核。

**2026-08-06 实际发生过一次**：开机自动进 6.8.0-136。已通过 GRUB 条目 ID + `apt-mark hold` 修复，
详见 §0.0。若再次出现，先跑 `./csi_lab.sh check`，它会直接指出是哪台、哪个内核。
快速确认命令：

```bash
for n in node1 node2 node3; do
  ssh $n 'echo "$(hostname) $(uname -r) $(modinfo iwlwifi | grep srcversion)"'
done
# 期望：三台均 6.5.0-15-generic，srcversion 1DEF5F9EE1363E0C6199DBF
```

### 10.2 PicoScenes 库路径破坏 snap/Firefox

```bash
sudo mv /etc/ld.so.conf.d/picoscenes.conf /etc/ld.so.conf.d/zzz-picoscenes.conf
sudo ldconfig
```

执行前先检查是否已改名。不要删除 PicoScenes library path。

### 10.3 tone 数（已解决）
242 total = 234 data + 8 pilot。导频索引 `6, 32, 74, 100, 141, 167, 209, 235`。
凡是出现 52，即为 §6.1 bug 1。

**dataset schema：模型输入用 234 个数据子载波，剔除 8 个导频列。**
理由见 §5.4——导频列是常数或全零，且填充值随采集会话变化，保留会造成信息泄漏。
若为了索引稳定而保留 242 列存档，必须在文档和加载代码中显式标注导频位置，
并在进入模型前丢弃。

### 10.4 非有限 amplitude
剔除导频列后，234 个数据子载波非有限值占比为 0。若使用全部 242 列，
非有限值恰为 `8/242 = 3.31%`，全部来自导频列。
`csi_plot.py` 会自动检测并报告无信息列，不要直接对全部 242 列取 `mean`。

### 10.5 接收端不同步（方法已具备）
不能用 frame index 对齐（`node1[i] != node3[i]`）：两端启动时间不同、丢失的包不同。
正确做法：`mac_header.py` 提取 sequence → `unwrap_seq()` 处理 12 位回绕 → `align()` 取交集 → 输出配对索引与 coverage。

### 10.6 SSH 管理网络（已建成，见 §4.4）

排障顺序，按当时实际踩到的先后：

| 现象 | 含义 | 处理 |
|---|---|---|
| `en5` 显示 `status: inactive` | 物理链路未建立 | 查网线、交换机端口、对端是否开机 |
| 交换机只亮一个灯 | 协商在 100M，千兆灯不亮 | 正常，管理流量够用 |
| 拿到 `169.254.x.x` | 该网段没有 DHCP | 用静态 IP，不要架 DHCP |
| 主机整个从网络消失 | NM 卡在获取 IP 后把接口拉下 | 改 `ipv4.method manual` |
| `Connection refused` | 主机可达但没有 sshd | `apt install openssh-server` |
| 输对密码仍 `Permission denied` | **用户名错了**（如对 node2 用了 `werner`） | 核对 §4.2 的用户对应关系 |
| 密码提示处看似无响应 | 终端输密码不回显，属正常 | 盲打后回车 |

发现主机的可靠手段（IPv4 未配好时也能用）：

```bash
ping6 -c 4 ff02::1%en5      # 链路上所有 IPv6 主机都会应答
```

### 10.7 `array_prepare_for_picoscenes` 静默失败（最隐蔽的坑）

**它在内部失败时仍会打印 `Preparation is done.` 并返回 0。** 绝不能相信它的退出码。

两种触发方式：

1. **缺少 sudo 免密。** 它内部调用 `sudo ifconfig` / `sudo iw` / `sudo route`。
   经 SSH 执行时没有终端，sudo 无法提示密码，每一步都失败，但脚本照常宣告成功。
2. **NetworkManager 仍在管理 AX210。** 即使 sudo 正常，NM 会持续维持 Wi-Fi 连接，
   导致 monitor 接口的信道设置被顶掉——接口存在，但没有频率。

实际后果（2026-07-31 实测）：node3 的 `wlp1s0` 停留在 managed 模式、信道 104
（5520 MHz），25 秒只收到 141 帧，且全是发给它自己 MAC 的；同期 node1 收到 12,280 帧。
**整条编排链路一路报成功。**

这很可能也是历史上"先启动的接收端能收包、后启动的收不到"的真正原因，
而不是 §7.4 记载的单播/广播差异。

必须的两道防线：

```bash
# 1. sudo 免密（每节点一次）
sudo tee /etc/sudoers.d/picoscenes >/dev/null <<EOF
$(whoami) ALL=(ALL) NOPASSWD: /usr/sbin/ifconfig, /usr/sbin/iw, /usr/sbin/route, \
/usr/sbin/ip, /usr/bin/nmcli, /usr/bin/systemctl, \
/usr/sbin/array_prepare_for_picoscenes, /usr/sbin/array_status, /usr/bin/pkill
EOF
sudo chmod 440 /etc/sudoers.d/picoscenes && sudo visudo -c   # 必须看到"解析正确"

# 2. 执行后校验真实状态，不看退出码
iw dev            # 必须存在 type monitor 的接口，且 channel 为目标值
array_status      # node1/node3 行尾应出现 "2412 20 2412"
```

`csi_node.sh:do_prep()` 已内置这两道检查，失败即中止。

### 10.8 AX210 与 NetworkManager 的归属

采集期间 AX210 必须脱离 NM；采集结束后应交还，否则节点无法上网。
`csi_node.sh` 用运行时方式管理，不使用永久 `unmanaged-devices` 配置文件——
后者会让 `systemctl restart NetworkManager` 重新标记为未托管，导致 Wi-Fi 永远恢复不了。

- 采集前：`sudo nmcli device set wlp1s0 managed no`
- 采集后（`csi_node.sh restore`）：删 monitor 接口 → 用 `ethtool -P` 取回硬件原始 MAC
  并还原 → `nmcli device set ... managed yes` → 重启 NM

注意 `array_prepare` 会把主接口 MAC 改成 `00:16:ea:12:34:56`，不还原的话
NM 会拒绝接管该设备，表现为 `wlp1s0` 一直是"未托管"。

## 11. 下一阶段执行计划

### 阶段 A：环境与远程编排 —— **已完成（2026-07-31）**

三节点管理网、SSH 免密、sudo 白名单、脚本分发、自动化采集与校验均已打通，
详见 §4.4、§8.1、§9.2。`csi_node.sh check` 可随时复查任一节点。

**遗留**：三台的**物理天线实际根数仍未核实**，`nRx=2` 是否与硬件一致存疑。

### 阶段 B：清理分析工具 —— 大部分完成

1. 已停用 `csi_inspect.py`，改用 `csi_plot.py`（采集时自动）+ `mac_header.py`。
2. **仍待办：删除或隔离所有历史 `.npy`**——它们全部受截断 bug 污染。
3. **仍待办**：修正 `csi_export_csv.py` 的硬编码 52-tone 提示。
4. dataset 表示已确定：**模型输入用 234 个数据子载波**，见 §10.3。

### 阶段 C：受控 `1-Tx-2-Rx` 正式采集（**当前主线**，1–2 天）

采集与校验已自动化，但**开跑前必须先固定并记录节点物理布局**，
否则 `walk_link1` 与 `walk_link2` 之间没有可比性，重复试验也无意义。
需要的是：三台机器的坐标或布局草图、天线朝向、人行走的路径与速度约定。

| 场景 | 动作 | 次数 |
|---|---|---|
| `empty` | 两链路间无人 | ≥3 |
| `walk_link1` | 人穿过 `node2 → node1` LoS | ≥3 |
| `walk_link2` | 人穿过 `node2 → node3` LoS | ≥3 |

每次 30 秒，一条命令即可，元数据、对齐、自检全部自动完成：

```bash
cd ~/csi_lab
CAM_HOST=node3 ./csi_lab.sh batch phase_c.txt
```

每次跑完必须人工确认脚本输出的四项：**匹配率在 95% 以上**、
**`DATA_SUBCARRIERS=234`**、**`CAM_SEQ_GAPS=0`**、**完整性检查通过**。
任一不符当场重做，不要事后补救。

#### 距离变量（2026-08-05 加入）

除场景外还要考察**节点间距**：**5 m / 10 m / 15 m** 三档。

**设计约束：相机与行走区必须固定，只移动节点。**
理由是立体深度误差随距离平方增长（见 §11 阶段 E），
若相机到人的距离随节点间距一起变，深度真值质量就成了随条件变化的混杂因子，
届时无法判断差异来自射频几何还是来自真值质量。

因此推荐布置（当前相机在 `node3`）：

- **node1、node3、相机和行走区全部固定不动**
- **移动 node2（Tx）**来改变间距：5 m / 10 m / 15 m
- 每次都必须记录三个节点、相机、天线和行走区的坐标/高度

每换一档距离都要重新记录布局，并重跑一遍三个场景：

```bash
CAM_HOST=node3 ./csi_lab.sh batch phase_c_05m.txt   # 摆到 5 m 后
CAM_HOST=node3 ./csi_lab.sh batch phase_c_10m.txt   # 摆到 10 m 后
CAM_HOST=node3 ./csi_lab.sh batch phase_c_15m.txt   # 摆到 15 m 后
```

共 3 距离 × 3 场景 × 3 次 = **27 组**，约 20 GB，预计 2 小时以上。

**距离增大时要盯住两个退化信号：**

1. **匹配率下降。** 2.4 GHz 自由空间路损 15 m 比 5 m 多约 9.5 dB，室内多径更差。
   若 15 m 档匹配率跌破 90%，需要记录下来，该档数据的可用性要单独评估。
2. **相机宿主节点磁盘。** 当前相机在 `node3`，它会产生绝大多数数据。每组实测约
   0.75 GB，27 组约需 20 GB；批量前必须在三台节点和 Mac 上重新运行 `df -h`。
   每跑完一档先确认 Mac 文件大小、对齐结果和备份完整，再决定是否清理节点副本：

   ```bash
   ssh node1 'du -sh ~/csi_data; df -h ~'
   ssh node3 'du -sh ~/csi_data; df -h ~'
   ```

顺带可完成 §7.4 遗留的对照实验：同样条件下广播与单播各跑一次，
确认"单播导致只有一端收到"是否属实。

### 阶段 D：packet-level 对齐 —— **方法已验证，由队友接手**

```bash
python3 mac_header.py node1_xxx.csi node3_xxx.csi
```

2026-07-31 实测：配对 7,056 包，两端覆盖 98.4% / 96.7%，联合 95.2%。

后续工作（队友负责）：

1. 用返回的 `idx_a` / `idx_b` 索引两端 CSI 矩阵，得到严格配对的 packet pair。
2. 比较同一 packet 在两条链路上的 amplitude。
3. **不要直接融合未校准的 raw phase。**

实现注意事项见 §7.6，尤其是「必须先按发射源筛选」和「12 位 sequence 回绕」两点。

### 阶段 E：深度 ground truth —— **采集链路已打通（2026-08-05）**

原文档称此为「头号 blocker」，前提是「Azure Kinect SDK 不支持 macOS」。
**该前提不成立**：实际硬件是 Luxonis OAK-D-W，DepthAI 同时支持 Linux 与 macOS。

已完成：

- 型号确认 **OAK-D-W**（`lsusb` 显示 `03e7:2485 Intel Movidius MyriadX`，
  这是**未启动状态**的 PID，启动后变为 `03e7:f63b`）
- `depthai 3.8.0` + udev 规则已铺到**三台节点**，相机插哪台都能用
- 采集脚本 `csi_cam.py`，深度 640×360 对齐 RGB @30fps + 彩色 1080p H.265
- **相机内参已随每次采集自动保存**：fx=fy=381.49，cx=317.62，cy=186.96，
  基线 7.5 cm，深度单位毫米。三维还原：
  `X=(u-cx)·Z/fx`，`Y=(v-cy)·Z/fy`，`Z=depth[v,u]`
- 与 CSI 的联合采集、时间配对、完整性校验全部跑通

**时间同步：不需要 chrony。** 原计划的「软件时钟 + 物理事件」两层方案中，
第一层已被更好的办法取代：两个接收端收到的是同一个广播包，其 `system_ns`
之差即为时钟偏差，每 trial 直接实测，精度远高于 NTP（MAD 约 0.145 ms）。
相机接在接收端时与该端 CSI 天然同钟。详见 §0.1。

**第二层「物理同步事件」仍然建议做**，作为端到端验证手段——
软件层面对得上不等于物理上对得上，中间任何一环（驱动缓冲、相机曝光延迟）
出问题只有物理事件能暴露。

#### 仍未解决：深度精度与距离的矛盾

立体深度误差随距离**平方**增长，大致关系：

```
误差 ≈ Z² × 视差误差 / (基线 × 焦距像素)
     = Z² × δd / (0.075 × 381.49)
```

代入典型视差误差，量级如下（仅为量级估计，需现场实测确认）：

| 相机到人的距离 | 深度误差量级 |
|---|---|
| 3 m | 数厘米 |
| 5 m | 十几厘米 |
| 10 m | 数十厘米 |

**姿态真值需要厘米级精度，因此人必须待在离相机较近的范围内（建议 ≤5 m）。**
这与「节点间距 15 m」并不矛盾——**节点间距和相机到人的距离是两个独立的量**，
布置时必须让相机靠近行走区，而不是跟着节点一起拉远。

可能的改进（尚未实施）：`StereoDepth` 开启 subpixel 模式可显著降低视差误差，
代价是带宽上升。现有 USB2 余量（用了约 40%）应该容得下，值得试。

其余仍待办：场地坐标系与相机外参标定、姿态估计模型选型、
数据保护与被试知情同意。

在完成场地标定与姿态估计前，不应宣称已建立 Person-in-WiFi-3D training dataset。

## 12. 成功标准

短期 `1-Tx-2-Rx` 阶段（截至 2026-08-07）：

| # | 标准 | 状态 |
|---|---|---|
| 1 | `node1`、`node3` 均稳定采集 node2 的广播 CSI | **已达成** |
| 2 | 两份 `.csi` 均可正常解析 | **已达成** |
| 3 | frame/tone/Rx/Tx 维度稳定且有定义 | **已达成**（242 tone，234 有效） |
| 4 | 可量化 packet count、packet rate、丢包、解析异常 | **已达成**（自动自检） |
| 5 | 每次实验有完整元数据 | **已达成**（自动写入） |
| 6 | **布局记录** | **部分达成**：新 5 m 布局有现场照片和 trial 命名，但仍缺节点精确坐标、高度、天线方向和外参 |
| 7 | 空场数据在重复试验中有可解释 baseline | **诊断达成、正式未达成**：两次真实空场可复现慢漂，但 node3 5 秒分段范围仍为 2.57% / 3.16% |
| 8 | 人穿过不同链路时对应 link 有可重复差异 | **部分达成**：Link2 静态与动态响应通过；Link1 仍失败，且 Link2 尚缺正式重复和末尾空场 |
| 9 | 完成两接收端 packet-level 对齐并给出 coverage | **已达成**（最新 Link2 walk 共同匹配率 98.07%） |
| 10 | 深度相机与 CSI 联合采集、时间可对齐 | **已达成**（相机迁至 node3 后复验通过；配对中位数 8.34 ms） |
| 11 | 相机内参随数据保存，可还原三维坐标 | **已达成**（自动写入） |
| 12 | 批量采集可无人值守连续跑多组 | **已达成**（实测 3 组） |

剩余的 6/7/8 三项现在卡在两个明确门控：**补齐布局/功率元数据**，以及
**消除或解释空场慢漂并获得完整的前空场—动作—后空场协议**。在此之前不要运行
`phase_c_*.txt` 的正式批量任务。

仍未核实：**三台的物理天线实际根数**（数据里 `nRx=2`，与硬件是否相符存疑）。

Group 1 已完成原始 RGB-D 接入、CSI/depth 时间配对、统一结果目录，以及从采集到
对齐后的 CSI/depth pair。完整研究数据集仍需：

1. 补齐场地坐标、相机外参、节点高度和天线方向等几何元数据。
2. 从 OAK-D RGB-D 生成并验证 2D/3D 骨骼标签；当前深度图本身不能直接称为 3D 姿态真值。
3. 完成正式空场与 Link1/Link2 重复采集，并按质量标志区分诊断、失败和训练候选样本。
4. 建立 RSSI、CSI 幅度、去噪/差分相位的同窗特征与消融评估，避免只比较 RSSI 标准差和 CSI 全频带均值。

## 13. 建议数据目录结构

不要继续把正式实验文件散落在 `Downloads`：

```text
person-in-wifi-3d-experiment/
  README.md
  docs/{handoff,setup,experiment-protocol}.md
  scripts/{node,host,analysis,matlab}/
  data/
    raw/YYYY-MM-DD/trial_name/{node1.csi,node3.csi,metadata.yaml}
    interim/
    processed/
  results/{figures,reports,alignment}/
```

原始目录只追加不覆盖。processed 输出应能追溯到 raw file、parser 版本和参数。
`node1_walk_link1.csi` 的丢失说明现有做法已经出过一次事故。

## 14. 信息可信度说明

- 「已验证」表示有本地文件或命令输出证据，不代表今日再次现场复测通过。
- 节点当前 IP、SSH 状态、物理天线连接、内核版本、PicoScenes 版本均需在恢复实验前重新检查。
- §9.4 数据清单为 2026-07-30 对本地原始文件逐帧实测所得。
- 本文档不含密码或 API key。
