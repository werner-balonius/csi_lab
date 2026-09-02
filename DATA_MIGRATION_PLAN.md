# 数据迁移方案（待确认，尚未执行）

日期：2026-08-11
现状：node1 9.9 G / node3 20 G，合计 **29.9 G**；Mac 可用 288 G。

---

## 一、先看清约束

| 类型 | node1 | node3 | 合计 | 占比 |
|---|---:|---:|---:|---:|
| `.bin`（深度） | 7.6 G | 17 G | **24.6 G** | **82%** |
| `.h265`（彩色） | 603 M | 1.3 G | 1.9 G | 6% |
| `.log` | 336 M | 334 M | 670 M | 2% |
| `.csi`（原始） | 771 M | 769 M | 1.5 G | 5% |
| `.npz` | 428 M | 431 M | 859 M | 3% |
| `.npy` | 234 M | 235 M | 469 M | 2% |
| `.png` | 33 M | 33 M | 66 M | <1% |
| `.json`/`.txt` | ~1 M | ~2 M | 3 M | <1% |

单文件典型大小：`.bin` **661 M**、`.h265` 52 M、`.csi` 24 M、`.log` 12 M。
超过 100 MB 的文件：node1 **13 个**、node3 **26 个**。

## 二、为什么不建议直接进 GitHub

1. **单文件硬限制 100 MB。** 39 个 `.bin` 全部超标，普通 Git 直接推不上去。
2. **仓库软上限 1 GB，5 GB 会收到警告。** 29.9 G 远超。
3. **Git LFS 免费额度只有 1 GB 存储 + 1 GB/月流量。** 29.9 G 需要付费
   （约 5 USD/月每 50 G 数据包），且团队每次 clone 都消耗流量配额。
4. **`.h265` 含可识别人脸。** 队友已在 `docs/experiments/20260811_pilot/README.md`
   明确写明不上传，理由是隐私。推到远端（即使私有仓库）也与该决定冲突，
   且 Git 历史一旦写入就极难彻底移除。
5. **Git 对二进制无法增量压缩。** 每次改动都存整份，仓库会迅速膨胀且不可逆。

> 队友已经做了正确的分层：代码/文档/脱敏特征入库，原始数据留本地。
> 现有 `data/processed/20260811_pilot/`（484 KB 特征 + SHA256）就是这个思路。

## 三、推荐方案：分层备份（不全量入 GitHub）

### A 层 —— 进 GitHub（约 100 MB，安全）

| 内容 | 大小 | 说明 |
|---|---:|---|
| `metadata.txt` / `*_cam.json` | 3 M | 每 trial 的采集参数、RF 状态 |
| `alignment_report.json` | <1 M | 匹配率、时钟模型 |
| 100 ms 派生特征（扩展到全部 trial） | ~5 M | 沿用 `export_pilot_features.py` |
| `manifest.csv` + `SHA256SUMS` | <1 M | 清单与校验 |
| `*_quicklook.png`（可选，需先确认无人像） | 66 M | 质量速查图 |

这层可复现、可追溯、无隐私风险。

### B 层 —— 本地 + 外部备份（29.9 G）

**目标位置（按优先级）：**

1. **移动硬盘 / NAS** —— 最合适。一次性拷贝，无流量成本，容量无忧。
2. **学校提供的云盘**（OneDrive / SharePoint，学生通常有 1 TB）——
   适合团队共享，需确认机构对人像数据的合规要求。
3. **Mac 本地 `~/csi_archive/`** —— 288 G 可用，放得下，但**单点故障**，
   不能作为唯一副本。

**必须包含**：`.bin`、`.h265`、`.csi`、`.npz`、`.npy`、`.log`

### C 层 —— 可再生，不必备份

`.npy`（amp_clean）、`_quicklook.png` 可由 `.csi` 重新生成。
若空间紧张可只留 `.csi`，但重新生成需要节点环境，权衡后再定。

## 四、执行步骤（确认后再跑）

### 第 1 步：Mac 建归档目录并全量拉取

```bash
mkdir -p ~/csi_archive/{node1,node3}
rsync -av --info=progress2 node1:~/csi_data/ ~/csi_archive/node1/
rsync -av --info=progress2 node3:~/csi_data/ ~/csi_archive/node3/
```

用 `rsync` 而非 `scp`：可断点续传、可校验、可重复执行不重复传输。
预计 29.9 G / 百兆网 ≈ **45–60 分钟**。

### 第 2 步：校验完整性（关键，不可跳过）

```bash
# 逐文件比对大小与数量，任何不一致都必须查明再删
for n in node1 node3; do
  ssh $n 'cd ~/csi_data && find . -type f -printf "%s %p\n" | sort' \
    > /tmp/${n}_remote.txt
  (cd ~/csi_archive/$n && find . -type f -printf "%s %p\n" | sort) \
    > /tmp/${n}_local.txt
  diff /tmp/${n}_remote.txt /tmp/${n}_local.txt && echo "$n 一致"
done
```

macOS 的 `find` 无 `-printf`，实际执行时本地侧改用 `stat -f`。

### 第 3 步：生成清单与哈希

```bash
cd ~/csi_archive
find . -type f -exec shasum -a 256 {} + > SHA256SUMS
```

29.9 G 计算哈希约需 5–10 分钟。

### 第 4 步：拷贝到外部备份

移动硬盘或云盘。**在此之前不要删除节点上的任何数据。**

### 第 5 步：提取 A 层入 GitHub

```bash
# 元数据与报告（体积极小）
mkdir -p ~/csi_lab/data/metadata
find ~/csi_archive -name 'metadata.txt' -o -name '*_cam.json' \
     -o -name 'alignment_report.json' | while read f; do ... done

# 派生特征：复用队友的脚本，扩展到全部 trial
~/csienv/bin/python ~/csi_lab/scripts/export_pilot_features.py ...
```

### 第 6 步：确认两份副本无误后，再清理节点

```bash
# 只有在 Mac 归档 + 外部备份都校验通过后执行
ssh node1 'rm -rf ~/csi_data/*'
ssh node3 'rm -rf ~/csi_data/*'
```

清理后：node1 20 G → 30 G，node3 16 G → 36 G。

## 五、若坚持要全量进 GitHub

需要 Git LFS + 付费，且必须先解决隐私问题：

```bash
brew install git-lfs
git lfs install
git lfs track "*.bin" "*.csi" "*.npz"
# 不要 track *.h265（含人像）
```

成本：29.9 G ≈ 每月 3 USD（存储）+ 流量另计。
团队每人 clone 一次消耗 29.9 G 流量配额。

**我的判断：不划算，也不必要。** 研究数据的标准做法是
代码进版本控制、数据进专用存储，两者用哈希关联 —— 队友已经这么做了。

## 六、需要你决定

1. **外部备份介质**：移动硬盘 / 学校云盘 / 其他？（这是方案能否成立的前提）
2. **`.h265` 是否加密后备份**？含人像，涉及被试知情同意。
3. **`.npy` 和 `.png` 是否保留**？可再生，但重生成需要节点环境。
4. **节点数据何时清理**？建议两份副本校验通过后再动。
