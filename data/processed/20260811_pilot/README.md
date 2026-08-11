# 2026-08-11 Pilot 派生数据

本目录只包含适合普通 Git 的去身份化派生数据：

- `pilot_100ms_features.csv.gz`：16 条有效记录（15 条主样本 + 1 条座椅空场）
  的 100 ms RSSI/CSI 幅度特征；
- `trial_metadata.json`：17 条记录的清单、去路径/去节点账号元数据、对齐质量，
  以及本地 `aligned_csi.npz` 的 SHA-256 和字节数；
- `SHA256SUMS`：上述两个派生文件自身的校验和。

`protocol_phase` 仅依据统一协议给出 `9–25 s` 的候选动作窗，**不是 RGB-D 人工真值**。
建模前应根据相机重新标注实际进入、动作和退出边界。

重新生成：

```bash
./.venv/bin/python scripts/export_pilot_features.py \
  --results-root "/path/to/csi_results"
```

本目录不包含原始深度、视频、完整复数 CSI 或可识别参与者图像。
