# 2026-08-11：5 m 小型动作 Pilot

## 结果

计划中的 **15 条有效主样本已全部完成**：`empty`、`walk_link2`、
`arm_wave`、`leg_lift`、`sit_to_stand` 各 3 条。另有 1 条座椅布局专用空场，
以及 1 条因左侧木门开启而明确排除但保留在本地的原始试验。

- 样本清单：[`manifest.csv`](manifest.csv)
- 逐 trial 文字质量报告：[`evaluations/`](evaluations/)
- 无座椅布局：[`layout_no_chair.md`](layout_no_chair.md)
- 座椅布局：[`layout_chair.md`](layout_chair.md)
- 隐私安全派生数据：[`../../../data/processed/20260811_pilot/`](../../../data/processed/20260811_pilot/)

## 有效样本数量

| 类别 | 有效数量 | 布局 |
|---|---:|---|
| `empty` | 3 | 5 m 等边三角形，无座椅 |
| `walk_link2` | 3 | 5 m 等边三角形，无座椅 |
| `arm_wave` | 3 | 5 m 等边三角形，无座椅 |
| `leg_lift` | 3 | 5 m 等边三角形，无座椅 |
| `sit_to_stand` | 3 | 5 m 等边三角形，固定 50 cm 座椅 |
| `empty_chair` | 1 | 座椅布局校准，不计入主 15 条 |

## 采集质量

- 有效主样本双端匹配率约为 98.99%–99.48%，均高于 95% 门槛。
- 每条动作试验均保留 234 个数据子载波，包序单调。
- 动作试验均为 1501–1502 个深度帧，30 FPS，帧序号缺口 0；彩色视频可解码。
- 每条有效动作均由 RGB 时间线确认前空场、动作和后空场。
- 座椅试验中椅子无可见移动，左侧木门保持关闭。

## 初步信号结果

下表使用每类三条有效试验的范围。无座椅动作以 `empty_05m_pilot_02` 为基线，
坐站以 `empty_chair_05m_pilot_01` 为基线。

| 动作 | node1 RSSI 倍率 | node3 RSSI 倍率 | node3 CSI 100 ms 变化倍率，天线 0 | 初步观察 |
|---|---:|---:|---:|---|
| `walk_link2` | 0.93–0.96× | **2.43–2.75×** | **3.67–4.69×** | Link2 全身运动响应最强 |
| `arm_wave` | 0.70–0.78× | **1.76–1.94×** | **2.34–2.84×** | 上肢动作可重复检测 |
| `leg_lift` | 0.73–0.86× | **1.64–1.76×** | **2.48–2.64×** | 下肢动作可重复检测 |
| `sit_to_stand` | 0.75–0.85× | 0.94–0.99× | **1.33–1.47×** | RSSI 基本无增益，CSI 保留弱响应 |

这些结果支持 RSSI 用于强运动检测与分段、CSI 用于更弱动作区分。三条/类且只有
一名参与者，尚不足以声称动作识别性能或泛化能力。

## 分析限制

1. `sit_to_stand` 使用带座椅布局，其他动作不带座椅。必须使用 trial 内前后空场
   做基线归一化、时间差分或高通特征，不能让模型学习静态座椅差异。
2. 座椅专用空场目前只有 1 条，只适合 Pilot 校准。
3. `leg_lift_05m_pilot_02` 左侧木门开启，`manifest.csv` 中为 `valid=no`，
   不得进入训练或验证。
4. 后续验证必须按 trial 分组，不能随机拆分同一连续试验的时间窗。

## Git 数据分层

普通 Git 主分支只保存代码、布局、质量结论、清单、去身份化元数据和 100 ms
派生特征。以下原始文件不上传：

- `*_depth.bin`：单个约 660–790 MiB，超过 GitHub 100 MB 单文件限制；
- `*_color.h265` 和质量截图：包含可识别参与者画面；
- 原始 `.csi` 与完整 `aligned_csi.npz`：本轮合计体积较大，继续由本地数据目录保管。

派生特征可由 `scripts/export_pilot_features.py` 从本地 `aligned_csi.npz` 重新生成，
`trial_metadata.json` 同时保存每个本地对齐文件的 SHA-256 和字节数以便核验。

## 下一步

暂不扩大采集。先完成 trial 内基线归一化、PCA/UMAP、feature importance，及
RSSI-only、CSI amplitude、CSI amplitude+phase、RSSI+CSI 的按 trial 分组消融。
