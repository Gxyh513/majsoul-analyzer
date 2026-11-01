# 牌谱备份、磁盘监控与 ONNX 基准

这些是可选的运维工具，**不会自动执行，也不会修改生产 ONNX 线程数、模型或回放格式**。

## 备份原始牌谱

在仓库根目录、能读取 ModelScope 持久卷的环境中执行：

```bash
python -m scripts.backup_records --output /安全的备份位置/records-20261101.tar.gz
```

默认读取 `MAJSOUL_CACHE_ROOT` 对应的 `records/`；可用 `--records /path/to/records` 指定来源。仅包含 UUID 命名的 `.pb` 和历史 `.json` 原始牌谱，附 SHA-256 清单。不会包含账号库、账号密码、Token、文件锁或 `reviews/` 结果缓存。

不会覆盖已有备份。备份文件内含牌谱及可能的玩家信息，应存放在**非公开且最好离线的安全存储**，并定期试验恢复及校验哈希；仅把备份留在同一持久卷无法防止卷损坏。恢复前应先保留原始文件并验证 `manifest.json`，不要直接覆盖生产记录。

## 后端磁盘空间

可信身份调用 `GET /api/status` 的 `storage` 字段包含缓存占用、剩余磁盘空间以及 `low_disk_space`。低于 512 MiB 时标为 `true`。该字段仅是提醒，不会自动删掉永久原始牌谱，也不会自动清理备份。

## ONNX CPU 性能基准

在安装原生 `libriichi` 并拥有已校验的固定 `model.onnx` 的机器上，在仓库根目录运行：

```bash
python -m scripts.benchmark_onnx --model data/models/model.onnx --threads 1 2 4 --batches 16 64 128 --runs 3
```

输出每种组合的中位推理延迟及每决策耗时；同一输入会检查各线程配置的输出数值一致。先监测真实牌谱的 P50/P95 和峰值 CPU/内存，再根据基准决定是否调整默认配置。**不要仅凭合成批量结果直接改变生产线程数**。

## 依赖可重复性

当前仓库固定 Mortal 原生源码提交和 ONNX 模型 SHA-256，但 Python 依赖仍是版本范围，因此运行环境仍可能受到依赖升级影响。正式固定精确版本应使用在生产构建上确认兼容的 `pip freeze`、完整测试通过结果与安全更新计划，不应盲目填入未经验证的版本号。
