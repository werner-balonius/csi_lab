# 链路距离对检测准确率与灵敏度的影响（执行方案）

> **执行状态更新（2026-08-28）：继续暂停。** 单一 RF 会话内完成300秒连续空场后，
> node1/node3 后270秒前后半频谱差异为 `2.527% / 12.884%`。这排除了跨 trial
> 网卡重新初始化作为主要根因；node3出现覆盖两根天线和大多数子载波的可逆跳变。
> 详见 [`20260828_continuous_drift_diagnosis.md`](20260828_continuous_drift_diagnosis.md)。
> 下一步是核对现场事件；若现场完全静止，则交换node1/node3位置做硬件跟随判别。
> 判别前不得开始金属板或人体正式采集。

> **执行状态（2026-08-26 18:33 BST）：暂停，尚未进入物理控制或人体采集。**
> 1 m 布局在信道 1 与信道 11 上均未通过双空场门控。最后一次在信道 11 先采
> 1 条同会话 RF 预热、再采 2 条正式基线；正式基线差异为 node1 `5.864%`、
> node3 `5.001%`，且后条 trial 内仍有单调漂移。Node3 包匹配率为
> `96.65% / 95.70%`，也低于 98% 目标。所有这些 trial 仅作失败诊断，不能纳入
> 距离准确率/灵敏度统计。下一步应改为连续长空场诊断并排查物理微动/周期性环境
> 变化，而不是继续重复双基线或放置金属板。

## 1. 研究问题与表述边界

本实验测量的是单人**存在/运动检测**随链路距离与横向偏移的变化，不把检测准确率
称为位置回归精度，也不声称复现 3D 姿态估计。

主要问题：

1. 链路由 1 m 增加到 3 m、5 m 后，人体检测灵敏度如何变化？
2. 人从链路中心横向偏离 0.5 m、1.0 m 后，灵敏度如何变化？
3. 静止人体与运动人体的距离边界是否不同？
4. RSSI、归一化 CSI 频谱和 CSI 时序变化分别在哪些条件下有效？

结论严格限定于当前房间、1Tx-2Rx、20 MHz、2.4 GHz 和一名参与者。

## 2. 实验矩阵

| 因素 | 水平 |
|---|---|
| node2→node1 主链路距离 | 1 / 3 / 5 m |
| 射频信道 | 暂定 2462 MHz（信道 11），20 MHz；须先通过基线门控 |
| 人相对链路中点横向偏移 | 0 / 0.5 / 1.0 m |
| 状态 | 静止站立 / 原地踏步 |
| 重复 | 每条件 3 次 |

共 `3 × 3 × 2 × 3 = 54` 条人体 trial。每个距离另采两条物理控制：

- `plate_rx`：金属板距 node1 天线约 5 cm，验证测量链路自身灵敏。
- `plate_mid`：金属板位于主链路中点，量化该距离的直射遮挡可测性。

`plate_mid` 不设停止阈值，因为它本身就是随距离变化的待测量；`plate_rx` 若频谱响应
低于 17%，说明仪器或操作异常，该距离的人体阴性结果不可解释。

## 3. 固定几何

坐标系以 node2 Tx 为原点，主链路沿 +X；所有天线高度均为 1.0 m。

| 元件 | 坐标 |
|---|---|
| node2 | `(0, 0, 1)`，三档全程固定 |
| node1 | `(D, 0, 1)`，D=1/3/5 m |
| node3 | `(0, 3, 1)`，三档全程固定 |
| 人体中心 | `(D/2, 0/−0.5/−1.0, 0)` |

偏移统一沿 −Y，即远离 node3，避免偏移人体靠近对照接收端。三档之间只移动 node1
和对应地面中点标记；node2、node3、家具保持不动。

开始前必须把 8 月 26 日曾弯折 45° 的天线恢复为原始竖直方向。

## 4. 单条 trial 协议

每条 35 秒，Mac 本地语音自动提示：

| 时间 | 内容 | 是否计分 |
|---|---|---|
| 0–4 s | 场地空，无人/无板 | 基线 |
| 5.2 s | 提示进入或放板 | 过渡丢弃 |
| 8–21 s | 指定状态保持稳定 | 阳性窗 |
| 22 s | 提示完成并离场 | 过渡丢弃 |
| 27–34 s | 场地再次为空 | 基线 |

静止：面向 node1，双脚中心对准标记，正常呼吸，不做动作。

运动：面向 node1，在标记处持续原地踏步，双臂自然摆动；不得沿链路来回走。

每条 trial 自带前后空场，因此不跨 trial 使用绝对 CSI 幅度作人体效应，降低 AGC、
环境漂移和距离改变造成的混杂。

## 5. 每个距离的门控与执行

以下以 1 m 为例；3/5 m 将 `01` 替换为 `03/05`。

### 5.1 布局后静置与预检

移动 node1 并贴好三个横向标记后，任何节点/天线若被移动或重启，静置至少 20 分钟。

```bash
cd "/Users/d-low/Desktop/Group Project/csi_lab"
CFG="$PWD/configs/distance_01m.conf"

CSI_LAB_CONFIG="$CFG" ./csi_lab.sh check
CSI_LAB_CONFIG="$CFG" ./csi_lab.sh ds_d01m_warmup 15
```

要求：匹配率 >98%、234 个数据子载波、非单调时间戳 0。RSSI std 接近 0 表示截顶，
该距离的 RSSI 指标不可用，但不自动否定归一化频谱指标。

### 5.2 空场稳定性

2026-08-26 现场扫描显示信道 1 在三节点处均存在强度 99–100/100 的同信道 AP；
信道 11 最强干扰为 39–42/100。因此距离实验统一改用信道 11。信道 1 的失败基线
只作诊断记录，不与信道 11 的正式数据合并。

```bash
CSI_LAB_CONFIG="$CFG" ./csi_lab.sh batch batch_distance_baseline_same_session_01m.txt

# 把下面两个目录替换成刚完成批次中打印的精确时间戳目录；
# 不要用通配符，否则会把历史失败基线一起纳入比较。
python3 scripts/baseline_check.py \
  ../csi_results/YYYYMMDD_HHMMSS_1t2r_ds_d01m_baseline_same_session_01 \
  ../csi_results/YYYYMMDD_HHMMSS_1t2r_ds_d01m_baseline_same_session_02
```

- <1%：理想，通过。
- 1–2%：记录为边缘；因人体 trial 自带基线，可继续 pilot，但须报告。
- >2% 或 trial 内单调漂移：停止，继续静置并排除人员/风扇/移动物体。

### 5.3 物理控制

```bash
CSI_LAB_CONFIG="$CFG" ./csi_lab.sh batch batch_distance_controls_01m.txt
python3 scripts/distance_sensitivity.py --results ../csi_results --node node1
```

`plate_rx` 频谱变化须 ≥17%；`plate_mid` 的 RSSI 下降与频谱变化作为距离曲线的一部分。
每次放板与撤板时只碰金属板，不能触碰节点、天线或线缆。

### 5.4 人体批次

```bash
CSI_LAB_CONFIG="$CFG" ./csi_lab.sh batch batch_distance_01m.txt
```

批次顺序由固定随机种子生成；不得按状态或偏移重新排序。每组间隔 25 秒，先读取当前
trial 名，走到对应标记，再按回车提前开始。场内只能有一名参与者。

3 m、5 m 对应：

```bash
CSI_LAB_CONFIG="$PWD/configs/distance_03m.conf" ./csi_lab.sh batch batch_distance_baseline_same_session_03m.txt
CSI_LAB_CONFIG="$PWD/configs/distance_05m.conf" ./csi_lab.sh batch batch_distance_baseline_same_session_05m.txt

CSI_LAB_CONFIG="$PWD/configs/distance_03m.conf" ./csi_lab.sh batch batch_distance_03m.txt
CSI_LAB_CONFIG="$PWD/configs/distance_05m.conf" ./csi_lab.sh batch batch_distance_05m.txt
```

每次改变距离后必须重新经历静置、干跑、双空场和物理控制，不能直接接着跑人体批次。

## 6. 指标与防泄漏规则

每个 1 秒窗口提取：

1. `rssi_abs`：相对本 trial 前后空场 RSSI 中位数的绝对变化。
2. `spectrum`：AGC 归一化后的 CSI 频谱相对基线 L2 距离。
3. `motion`：约 25 ms 滞后的归一化 CSI 幅度变化能量。

对每条测试 trial，检测阈值来自**同距离其他 trial 的空场窗口**第 95 百分位；测试
trial 自己不参与阈值学习。连续窗口不能随机拆分训练/测试。

报告：

- 灵敏度/召回率 `TP/(TP+FN)`
- 特异度 `TN/(TN+FP)`
- 平衡准确率 `(灵敏度+特异度)/2`
- ROC-AUC
- trial 检出率（3 次重复中通过阈值的比例）

```bash
python3 scripts/distance_sensitivity.py \
  --results ../csi_results --node node1 \
  --output ../csi_results/distance_sensitivity_summary.csv

# node3 环境对照
python3 scripts/distance_sensitivity.py --results ../csi_results --node node3
```

3 次重复只够形成 pilot 曲线，置信区间会较宽。若某处恰好出现性能拐点，正式结论应把
该条件扩到至少 8 次重复，而不是把 54 条连续窗口当作独立样本。

## 7. 预期判读

- 1 m 中点静止应主要体现在 RSSI/频谱；运动同时提高 motion 分数。
- 3/5 m 中点 RSSI 可能因多径不下降，这是距离效应而非采集失败，前提是 `plate_rx`
  仍通过。
- 偏移增加后静止灵敏度应下降；运动可能因多普勒散射仍保持可检。
- 若 node1 与固定 node3 曲线相近，测到的可能是全局房间活动而非主链路特异响应。

原始数据全部保留；失败 trial 在元数据中标记，不删除。RGB-D 本实验不采集。
