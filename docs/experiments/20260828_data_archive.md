# 原始数据归档与本地清理（2026-08-28）

## 结果

项目私有 OneDrive 的 `csi_lab_backup` 现包含五个按阶段分类的原始数据归档：

- `20260806_07_early`
- `20260811_pilot`
- `20260814_link_selectivity`
- `20260820_link_integrity`
- `20260824_28_distance_diagnostics`

最后一个目录为本次增量上传，共 9 项、2.66 GB。它包含 2026-08-24 的 3 m probe、
2026-08-26 的 1 m 失败空场门控，以及 2026-08-28 的连续漂移诊断。三个归档均带
SHA-256，另附机器可读 trial 清单和可直接阅读的诊断说明。

## 数据边界

- 8 月 24–28 日数据均为诊断数据，不是正式人体训练集，不得计入距离准确率或灵敏度。
- 8 月 28 日 300 秒连续空场是高价值负结果：它排除了跨 trial 射频重置是主要根因。
- 历史 8 月 11 日媒体含可识别参与者的 RGB-D 录像，只能在项目授权范围内使用。

## 本地清理

确认远端名称、项目数、文件大小及校验文件存在后，永久删除：

- `csi_results/` 中 60 个已归档原始 trial；
- `/Users/d-low/csi_results` 中 5 个旧重复副本及其目录；
- 本地 `20260820_link_integrity` 归档；
- 本次 2.7 GB 上传暂存目录。

保留 Git 仓库、`csi_results` 索引和评估说明以及布局照片。清理后 Mac 可用空间约
89 GiB。原始数据无法从本机废纸篓恢复，需要从项目私有 OneDrive 取回并校验。

公开仓库不记录私有 OneDrive 共享链接；本机取回入口记录在
`/Users/d-low/Desktop/Group Project/csi_results/ONEDRIVE_ARCHIVE_20260828.md`。
