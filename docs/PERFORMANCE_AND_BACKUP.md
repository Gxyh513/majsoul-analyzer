# 牌谱备份、磁盘监控与 ONNX 基准

这些是可选的运维工具，**不会自动执行，也不会修改生产 ONNX 线程数、模型或回放格式**。

## 备份原始牌谱

在仓库根目录、能读取 ModelScope 持久卷的环境中执行：

```bash
python -m scripts.backup_records --output /安全的备份位置/records-20261101.tar.gz
```

默认读取 `MAJSOUL_CACHE_ROOT` 对应的 `records/`；可用 `--records /path/to/records` 指定来源。仅包含 UUID 命名的 `.pb` 和历史 `.json` 原始牌谱，附 SHA-256 清单。不会包含账号库、账号密码、Token、文件锁或 `reviews/` 结果缓存。

不会覆盖已有备份。备份文件内含牌谱及可能的玩家信息，应存放在**非公开且最好离线的安全存储**；仅把备份留在同一持久卷无法防止卷损坏。

离线完整性校验（不向磁盘恢复牌谱）：

```bash
python -m scripts.verify_records_backup --archive /安全位置/records-20261101.tar.gz
```

定期在独立的**不存在的新目录**演练恢复：

```bash
python -m scripts.verify_records_backup --archive /安全位置/records-20261101.tar.gz --restore-to /tmp/paipu-restore-20261101
```

恢复工具校验 SHA-256、清单文件名与归档成员、拒绝符号链接、路径穿越和意外内容；不会覆盖原始牌谱、账号数据库或分析结果缓存，也不会自动更改生产目录。演练后妥善清理临时恢复数据；仍须由运维方把离线备份存到受保护的独立存储。

## 后端磁盘空间

可信身份调用 `GET /api/status` 的 `storage` 字段包含缓存占用、剩余磁盘空间以及 `low_disk_space`。低于 512 MiB 时标为 `true`。该字段仅是提醒，不会自动删掉永久原始牌谱，也不会自动清理备份。

## 分析 API 性能基线（仅本机）

在运行了固定 Mortal 模型的**本机测试环境**执行，不允许把生产域名传给压测工具：

```bash
python -m scripts.benchmark_api --url http://127.0.0.1:7860 --requests 8 --concurrency 2
```

工具限制为最多 32 次请求、4 个并发，使用内置演示输入（不包含私人 JSON 或真实抓谱）。
报告成功数、失败数、耗时 P50/P95、整轮吞吐量；不输出令牌、UUID、昵称、IP。
内置演示的吞吐量不代表线上公开牌谱抓取/转换的端到端性能。GitHub Actions 使用隔离容器进行 4 次请求、最多 2 并发的真实 ONNX 本机端到端基准，并采集一次容器 CPU/内存读数；该读数是瞬时采样，**不是 CPU 或内存峰值，也不能等同 ModelScope 线上指标**。
可信 `/api/status` 同时提供最近请求阶段分位数、成功/失败数量、当前排队及 Linux RSS（进程内，重启清空）。
不经实际运行不可虚报 ModelScope 生产 P95 或内存改善。

## ONNX CPU 性能基准

在安装原生 `libriichi` 并拥有已校验的固定 `model.onnx` 的机器上，在仓库根目录运行：

```bash
python -m scripts.benchmark_onnx --model data/models/model.onnx --threads 1 2 4 --batches 16 64 128 --runs 3
```

也可加入 `--real-demo`，从仓库内置 MJAI 演示事件通过真实 libriichi 状态机采集观测和动作掩码，
再比较配置（不接触用户上传私人 JSON）：

```bash
python -m scripts.benchmark_onnx --real-demo --threads 1 2 4 --batches 16 64 --runs 3
```

输出每种组合的中位推理延迟和每决策耗时；现在检查**全部 batch 输出**，而非只检查一个固定状态。
GitHub Actions 在独立 CI 主机上记录这些数据。CI 主机的最快配置不一定适用于 ModelScope 实际 CPU，
须结合线上 CPU/RSS/P95 负载和实际硬件决定是否修改生产默认值。
先监测真实牌谱的 P50/P95 和峰值 CPU/内存，再根据基准决定是否调整默认配置。**不要仅凭合成批量结果直接改变生产线程数**。

## 回放快照内存与传输体积

回放服务端在相邻步骤中复用未改变的手牌、牌河和副露列表（只读快照）。
最终仍完整输出原有 JSON 字段和事件序号，gzip、前端协议与规则均不改变；
**这项优化针对 Python 对象分配，不应误报为线路字节减少**。

在含已校验 Mortal 模型的机器上用仓库内置演示牌谱测量：

```bash
python -m scripts.benchmark_replay
```

输出回放步数、完整 JSON 字节数、gzip 字节数、Python tracemalloc 峰值和总耗时。
仅用内置演示数据，不读取用户上传内容，也不将私人 JSON 落盘。生产上改善大小需真实测试验证。

## 依赖可重复性

生产 Docker 构建现在安装 `requirements.lock.txt` 中的 **28 个精确版本**，来自 2026-11-01
完整模型构建与测试通过的 CI 运行 41271373222，在安装测试工具前的 Python 3.12 镜像中执行 `pip freeze` 采集。
`requirements.txt` 保留为上层依赖声明。每次更新锁文件必须在相同 Python 3.12 / Linux 目标环境验证、
检查依赖安全性，再跑完整模型与麻将回放测试。CI 对照镜像实际 `pip freeze` 和锁文件，防止漂移。
这锁定了版本，但尚未锁 wheel 文件哈希与基础镜像 digest，不应声称跨平台位级可重现。
