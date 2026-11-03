# 牌谱备份、磁盘监控与 ONNX 基准

这些是可选的运维工具，**不会自动执行，也不会修改生产 ONNX 线程数、模型或回放格式**。

## 备份原始牌谱

在仓库根目录、能读取 ModelScope 持久卷的环境中执行：

```bash
python -m scripts.backup_records --output /安全的备份位置/records-20261101.tar.gz
```

默认读取 `MAJSOUL_CACHE_ROOT` 对应的 `records/`；可用 `--records /path/to/records` 指定来源。仅包含 UUID 命名的 `.pb` 和历史 `.json` 原始牌谱，附 SHA-256 清单。不会包含账号库、账号密码、Token、文件锁或 `reviews/` 结果缓存。

备份时直接对写入归档的原始字节计算 SHA-256，**发布前自动完整校验归档**；如果校验失败，正式备份文件不会出现。发布采用同一目录临时文件的原子硬链接，目标文件若已存在或同时被其他任务创建，绝不会覆盖（备份目标文件系统须支持硬链接，否则安全失败）。此校验确保归档可读且与清单一致，但不是跨多个原始文件的数据库式一致性快照。\n\n不会覆盖已有备份。备份文件内含牌谱及可能的玩家信息，应存放在**非公开且最好离线的安全存储**；仅把备份留在同一持久卷无法防止卷损坏。

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

外部调度器可按需定时调用**只读健康检查**（令牌只从环境变量读取，不在命令行传输）：

```bash
MAJSOUL_API_TOKEN=你的服务访问令牌 python -m scripts.check_service_health --url https://你的后端域名
```

退出码 0 表示健康、1 表示不可用/鉴权失败、2 表示低磁盘空间或模型不可用；结果只输出匿名布尔状态。
远程必须 HTTPS；不会上传或保存私人 JSON，也不包含 IP 限流。
此命令仅供你配置在独立的外部调度器中，**ModelScope 休眠时自身定时任务无法保证执行**；
GitHub Actions `monitor-service.yml` 已安排每小时公开状态监测（支持手动运行），仅判断站点及 Mortal 模型就绪；工作流失败可在 Actions 中查看，不含独立告警收件人或备份计划。外部独立备份、恢复演练和告警收件人仍需另行配置。

## 分析 API 性能基线（仅本机）

在运行了固定 Mortal 模型的**本机测试环境**执行，不允许把生产域名传给压测工具：

```bash
python -m scripts.benchmark_api --url http://127.0.0.1:7860 --requests 8 --concurrency 2
```

工具限制为最多 32 次请求、4 个并发，使用内置演示输入（不包含私人 JSON 或真实抓谱）。
报告成功数、失败数、耗时 P50/P95、整轮吞吐量；不输出令牌、UUID、昵称、IP。
内置演示的吞吐量不代表线上公开牌谱抓取/转换的端到端性能。性能基准需要在隔离的本地环境手动运行；不要把结果等同于 ModelScope 线上的 CPU、内存和延迟指标。
可信 `/api/status` 同时提供最近请求阶段分位数、成功/失败数量、当前排队及 Linux RSS（进程内，重启清空）。
不经实际运行不可虚报 ModelScope 生产 P95 或内存改善。

## 生产环境只读性能采样

生产后端的可信 `GET /api/status` 已包含最近最多 256 次分析的阶段 P50/P95、推理队列、当前进程 RSS 和检讨缓存空间信息。可以在**自己能够安全提供服务端 API Token 的可信终端**执行：

```bash
python -m scripts.sample_service_performance --url https://gxyh513-majsoul-analyzer.ms.show --samples 3 --interval-seconds 30
```

只从环境变量 `MAJSOUL_API_TOKEN` 获取 Token；不要把 Token 放进 URL、命令行参数、仓库或日志。该工具最多采样 12 次，多次采样至少间隔 5 秒，只发送状态 GET 请求，不触发牌谱抓取或 AI 推理；只输出经过白名单筛选的汇总数值，不打印账号、UUID、昵称、密钥、请求原文或服务端原始 JSON。无 Token 或权限不足时会明确失败，**不会把匿名健康状态误认为可信性能指标**。

`latest.latency_ms` 是最后一次读取时后端进程的**滚动窗口** P50/P95，并非本采样工具统计的全时段分位数；如果最近没有分析请求，`status=no_recent_requests`，不能据此得出性能结论。`max_observed_rss_bytes` 和 `max_observed_queue_waiting` 只是多次读数的最大值，**不代表内存峰值或整段时间内的最大队列**，也没有 CPU 峰值数据。进程重启或 ModelScope 休眠会清空进程内历史。应结合 ModelScope 平台提供的 CPU、内存监控和真实业务访问时段对照分析；不要对生产地址运行批量压测、凭空宣布优化收益或依照 CI 最快配置直接调整 ONNX 线程。

## 公开检讨缓存命中优化

已保存的公开原始牌谱 `.pb` 文件现在通过原始 Protobuf 字节摘要、协议 SHA、相关转换/检讨代码摘要、模型、座位和分析参数定位结果缓存。命中时只读取并校验原始记录封装与检讨缓存，不再重复 Protobuf 业务解码、MJAI 转换或 ONNX 推理。默认座位仍按牌谱链接所含玩家 ID 与已缓存元信息核对，避免返回其他玩家视角；不完整、变更、过期或损坏的缓存都会回退正常分析。已有的事件指纹检讨缓存仍可在首次正常转换后命中，并按需写入新的原始指纹缓存文件；旧文件再由 24 小时过期清理和容量淘汰回收，无需全量迁移。私人 JSON 和演示输入不进入这一磁盘快速路径。

## 公开检讨缓存维护

`reviews/` 与永久的 `records/` 分离。24 小时 TTL、256 MiB 上限及低磁盘拒绝写入仍然生效。为了避免空闲缓存持续占用空间，缓存读取和可信 `/api/status` 检查会**每个进程/目录最多每小时**进行一次惰性清理；保存缓存时也会清理。只匹配检讨缓存文件名，不访问或清理永久牌谱、账号数据库及私人 JSON。没有访问、状态检查或写入时**不会自动唤醒后台清理**，多进程各自有独立的清理计时。

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
这些性能基准需要在隔离环境手动采集；不同机器的结果不一定适用于 ModelScope 实际 CPU，
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
曾由 CI 运行 41271373222 的 Python 3.12 生产镜像执行 `pip freeze` 采集。
`requirements.txt` 保留为上层依赖声明。每次更新锁文件必须在相同 Python 3.12 / Linux 目标环境验证、
检查依赖安全性，并重新构建生产镜像、验证固定 ONNX 模型初始化。版本锁定依赖维护者与镜像构建流程共同校验，更新时应对照实际 `pip freeze` 与锁文件，防止漂移。
这锁定了版本，但尚未锁 wheel 文件哈希与基础镜像 digest，不应声称跨平台位级可重现。
