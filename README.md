# 雀魂牌谱 AI 检讨复盘系统

Majsoul Analyzer 将雀魂四人麻将牌谱转换为 MJAI 事件，使用 Mortal 神经网络分析决策，并提供四家视角的逐步回放。

- 在线前端：[majsoul.ccwu.cc](https://majsoul.ccwu.cc)
- 后端创空间：[ModelScope / majsoul-analyzer](https://modelscope.cn/studios/Gxyh513/majsoul-analyzer)

线上使用情况以实际部署状态为准；受保护的远程服务需要访问令牌。

## 功能

- 导入雀魂牌谱链接或牌谱号；可使用内置示例牌谱。
- 在四家视角之间切换，查看牌河、立直、副露、杠、手切/摸切及结算。
- 显示各合法动作的 Mortal Q 值、候选排序和概率，以及整场和各局的检讨统计。
- 向听、进张、危险度及其他标签是独立的辅助统计，**不是 Mortal 的 Q 值**。
- 支持时间轴、播放速度、快捷键和实时 SSE 分析事件。流开始后，模型不可用、排队失败等通过 `error` 事件返回，不能再改变已发送的 HTTP 200；请求参数和鉴权等流开始前的错误仍使用相应 HTTP 状态。永久保存已下载的雀魂原始牌谱。

## 模型与结果来源

分析引擎迁移为只使用 `data/models/model.onnx`，即 [Mortal S42 ONNX](https://huggingface.co/xzhe121/Mortal-S42-onnx/tree/2f7ad48bc29dadd9805b1897f3c5b97b76c0d41a) 固定 revision `2f7ad48bc29dadd9805b1897f3c5b97b76c0d41a`。文件 SHA-256：

```text
30381bf21389e2d25d3edf48fed11bf772336ef3daeee67d1bdae7fb9dd30cb4
```

权重缺失、损坏或校验失败时，分析会报错，**不会回退到内置启发式引擎或其他模型**。动作概率由模型 Q 值经温度 softmax 得到，并非模型的胜率或校准置信度。相同公开牌谱的检讨结果可在后端持久缓存 24 小时；浏览器仅维护当前页面会话缓存。

Rating 使用每个有效决策的 Q 值差距：单手相对分为 `max(0, 1 - (最佳 Q - 实际 Q))`，整场 Rating 为相对分平均值的平方乘 100。最佳动作得满分，差距越小得分越接近满分，差距达到 1 时该手得 0 分。此分数是基于模型 Q 值的启发式指标，不代表胜率；它与旧版本的候选范围归一化分数不可直接比较。无法判定具体杠牌的决策不计入 Rating。

模型发布源：[xzhe121/Mortal-S42-onnx](https://huggingface.co/xzhe121/Mortal-S42-onnx)。ONNX 文件本身不能证明训练步数。

## 本地运行

项目需要 Python 3.10+、ONNX Runtime 和 `libriichi` 原生扩展。默认依赖安装 CPU 版本的 ONNX Runtime；Docker 和 Colab 从 Mortal 上游固定提交 `0cff2b52982be5b1163aa9a62fb01f03ce91e0d2` 编译兼容当前 Python 的 `libriichi.so`。本地使用的原生扩展也须与该版本及 Python 环境兼容。

1. 克隆仓库并安装依赖：

   ```bash
   git clone https://github.com/Gxyh513/majsoul-analyzer.git
   cd majsoul-analyzer
   pip install -r requirements.txt
   ```

2. 从[固定版本下载 model.onnx](https://huggingface.co/xzhe121/Mortal-S42-onnx/resolve/2f7ad48bc29dadd9805b1897f3c5b97b76c0d41a/model.onnx)，校验上述 SHA-256，并保存为 `data/models/model.onnx`。路径和文件名必须完全一致；启动后可通过 `/api/status` 查看校验状态。不要提交模型文件。
3. 如果需要通过雀魂账号拉取牌谱，将 `data/config.example.json` 复制为 `data/config.local.json`，再按需填写账号、密码或访问令牌。也可使用内置演示牌谱。
4. 启动服务：

   ```bash
   python run.py
   ```

   默认访问 `http://127.0.0.1:8000/`。Windows 也可使用 `start.bat`。

雀魂协议从随项目提供的 `data/cache/liqi-0.11.252.w.json` 本地加载并校验；程序不会自动检测版本或下载协议。需要升级时，手动更新协议文件以及 `server/majsoul/client.py` 中的固定协议版本、规范化 SHA-256 和客户端版本配置。规范化摘要对解析后的对象执行 `json.dumps(schema, sort_keys=True, separators=(",", ":")).encode("utf8")` 再计算 SHA-256。协议不可用时服务会记录明确错误，演示和 MJAI 输入仍可使用。可用 WebSocket 线路仍通过 `clientgate/routes` 获取，并在进程内短暂复用。

支持 gzip 的客户端通过 HTTP 流式压缩接收 SSE；每个事件及时刷新，完整结果不使用 Base64。浏览器通过 `transport_version=2` 明确请求共享回放快照传输，客户端在解析后还原为旧版结构；未请求 V2 的 SSE 调用者仍收到原来的完整 JSON。磁盘检讨缓存 V3 保存 gzip 压缩的紧凑快照，兼容读取 V2 完整 JSON 缓存（压缩或未压缩），并限制最大解压结果大小。SSE V2 命中时直接复用紧凑结构；旧 SSE 和非流 API 按需恢复完整结构。推理完成后无需等待可选磁盘写回：最多保留两个写回任务，同键请求可复用待写回结果，正常关闭等待写回结束。浏览器控制台默认静默；普通模式不采集详细 SSE 分块、长任务和可见性诊断，显式调试模式才启用。Cloudflare 代理默认对压缩流原样直通，因此不采集代理侧逐帧计时；需要这些计时时，可在浏览器控制台设置 `window.__PAIPU_DEBUG_PROXY__ = true` 后重新发起分析，临时改用未压缩流并保留原有代理诊断。设为 `false` 可恢复压缩。

## 使用与快捷键

输入牌谱链接后点击“开始分析”；也可输入 `demo` 使用示例牌谱。首次只分析当前玩家；点击左侧其他座位时按需重新分析相应视角，四家共享同一份已下载的原始牌谱。同一后端已验证的模型状态可复用 30 秒，并按需后台刷新；首次或过期状态仍须验证，状态请求最多等待 8 秒。点击对手手牌仅切换明牌/暗牌，不会触发分析。

| 按键 | 操作 |
| --- | --- |
| Space | 播放 / 暂停 |
| 鼠标滚轮 | 在牌桌区域向下前进 / 向上后退；其余区域正常滚动页面 |
| ← / → | 上一步 / 下一步 |
| ↑ / ↓ | 上一决策 / 下一决策 |
| , / . | 上一错误 / 下一错误 |
| [ / ] | 上一局 / 下一局 |
| Home / End | 本局开始 / 结束 |
| H | 切换对手手牌明暗 |
| Esc | 关闭弹窗 |

## 部署与远程访问

### Docker / ModelScope

`Dockerfile` 编译 Linux `libriichi`，下载并校验指定模型；构建需要网络和 Docker。示例：

```bash
docker build -t majsoul-analyzer:latest .
```

```bash
docker run -p 7860:7860 --name majsoul-analyzer -v majsoul-records:/mnt/workspace/majsoul-analyzer -e MAJSOUL_CACHE_ROOT=/mnt/workspace/majsoul-analyzer -e MAJSOUL_API_TOKEN -e MAJSOUL_ALLOWED_ORIGINS majsoul-analyzer:latest
```

运行前在当前 shell 中设置 `MAJSOUL_API_TOKEN` 和 `MAJSOUL_ALLOWED_ORIGINS`；不要把真实令牌写入仓库或公开日志。Docker/ModelScope 发布前应配置好环境变量并验证实际启动情况。不要将雀魂账号配置文件打包进镜像。

#### 雀魂账号池

本机可在被忽略的 `data/config.local.json` 中配置主账号和 `backup_accounts` 数组。ModelScope 等远程环境可在**密文变量** `MAJSOUL_ACCOUNTS_JSON` 中配置 JSON 数组，例如：

```json
[
  {"account": "账号一", "password": "密码一"},
  {"account": "账号二", "password": "密码二"}
]
```

示例仅展示格式，不要把真实账号写入仓库。每项可以提供 `account` 和 `password`，也可提供 `access_token`；可选字段包括 `nickname` 和 `account_id`。服务按数组顺序尝试可用账号，并对重复账号去重。需要增删账号时，直接编辑该密文变量并重新部署；该变量作为账号池的唯一来源，不会偷偷合并旧的本地账号。环境变量管理的账号令牌刷新只保留在运行内存，更新 ModelScope 密文变量后需要重新部署生效。此模式不支持通过登录接口记住新账号；请修改部署密文，或使用临时登录。请遵守雀魂服务的使用规则；不要继续使用已公开的账号凭据。

### 访问控制

- 后端业务 API 需要有效的 `MAJSOUL_API_TOKEN`；`/api/status` 仅用于公开健康状态。`MAJSOUL_ALLOWED_ORIGINS` 是跨来源浏览器白名单，不替代 API 身份认证。
- 直接连接自建后端、ModelScope Studio 或其他后端时，可信用户仍可通过页面 URL 片段 `#api_token=<令牌>` 或口令提示提供令牌。前端会从地址栏移除片段，并只在当前浏览器标签页按后端来源保存。令牌链接等同凭据，不要公开分享。
- API 不接受 URL 查询参数中的 `api_token`。此前可能出现在日志或浏览器存储中的令牌应按实际暴露情况轮换。
- `colab_launcher.py` 会为临时隧道生成访问令牌；其输出链接包含令牌，请勿公开分享。

### Cloudflare Pages

- Pages 前端通过同源 Pages Function 访问 ModelScope 后端；普通访客不需要手动输入 API 令牌。Function 固定代理到 `https://gxyh513-majsoul-analyzer.ms.show`，并在服务端注入 `X-Api-Token`。同源代理仅开放状态和分析接口；账号登录/登出、服务器本地牌谱、共享引擎配置与模型下载不会开放给匿名访问者。
- 在 Cloudflare Pages 项目的 **Preview 和 Production** 环境分别设置 Secret `MAJSOUL_API_TOKEN`，并在 ModelScope 后端设置相同的新随机值。不要把令牌写入 `wrangler.toml`、源码、URL、浏览器存储或日志；请在已登录的设置页面中输入，不要粘贴到聊天。
- 公开分析接口只支持牌谱链接；不接收 JSON 文件上传。后端已设置有限推理排队（最多 4 个等待任务），不设置每 IP 请求限流。
- Pages 静态输出目录为 `web/`，Pages Function 位于仓库根目录的 `functions/`。建议在 Cloudflare Pages 项目设置中将构建命令配置为 `python3 scripts/stamp_web_assets.py`（无需额外 Python 依赖），以便部署时按文件内容更新所有 JS/CSS URL 的版本参数；Docker 镜像在构建时会执行同一脚本。HTML 本身使用 `no-cache`，JS/CSS 版本 URL 可继续使用长期缓存。部署后应检查正式域名的 `/api/status` 版本，并完成一次分析验证。

## API

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET | `/api/status` | 查询模型和连接状态 |
| POST | `/api/analyze/stream` | SSE 流式检讨 |
| POST | `/api/analyze/paipu` | 检讨雀魂牌谱链接或 ID |
| POST | `/api/analyze/demo` | 检讨示例牌谱 |
| GET | `/api/records/local` | 列出后端本地牌谱 |
| POST | `/api/login` | 配置雀魂账号登录 |

公开牌谱首次分析需要解码原始记录、转换 MJAI 并运行 Mortal；对于已持久保存的 Protobuf 牌谱，后续相同原始字节、座位、模型及参数命中检讨缓存时，将直接读取已有结果，跳过重复的 Protobuf 解码、MJAI 转换和 ONNX 推理。缓存指纹还包含当前协议版本与转换/检讨代码摘要，代码更新后不会错误复用旧转换结果。历史 JSON 牌谱仍使用原有转换流程。检讨缓存独立保留 24 小时，软容量目标为 256 MiB 硬上限的 85%，并在写入或惰性清理时优先淘汰过期和最旧结果；磁盘剩余空间不足时跳过写入，不影响完成本次 AI 检讨。缓存读取或可信状态检查至多每小时惰性清理过期及超出软预算的文件，闲置时没有额外的后台清理任务。内置演示和内部私人 JSON 不持久化检讨结果。雀魂原始牌谱下载后会先持久化，保存失败时接口返回错误。

## 管理员账号池

网站管理员入口为 `/admin`（例如 `https://majsoul.ccwu.cc/admin`）。
在 ModelScope Studio 后端环境变量中配置 `MAJSOUL_ADMIN_KEY`，然后使用该密钥登录；Pages Functions 仍使用既有的 `MAJSOUL_API_TOKEN` 代理私有接口，公开网页不包含任何后台密钥。

管理员可添加、编辑、删除、启停和批量导入账号。**每条记录必须同时提供雀魂账号、密码和 Access Token**。
批量文本格式为每行 `账号|密码|Token|备注`，也支持 JSON 账号数组。
所有启用且空闲的账号平等随机参与牌谱下载，同一账号同时最多处理一个抓谱任务；Token 确认失效才使用密码登录并更新 Token。
牌谱网络请求超时优先换 WebSocket 线路，账号出现 540 限流则冷却 24 小时。
每小时针对空闲账号执行一次认证状态检测，不下载牌谱。

账号数据保存在 SQLite：优先使用魔搭已有的 `/mnt/workspace/majsoul-analyzer/admin_accounts.sqlite3`。
如果目录的挂载方式不能支持 SQLite，可以设置后端环境变量 `MAJSOUL_ADMIN_DB_PATH` 为其他可写位置。若默认持久化目录无法初始化，会记录告警并退回本地 `data/admin_accounts.sqlite3`（重启可能丢失）。
首次成功初始化会从旧配置导入符合三项必填条件的账号，之后以数据库为唯一账号池来源，删除账号不会被旧环境变量重新添加。
账号凭据在数据库中按原样保存，不会返回到普通 API 或输出到日志。请不要向他人公开后台或数据库文件。

## 数据与安全

原始牌谱备份、磁盘空间告警、线上只读 P50/P95 性能采样以及可选 ONNX CPU 基准参见 [备份与基准说明](docs/PERFORMANCE_AND_BACKUP.md)。备份工具不会自动导出账号库、凭据或缓存的 AI 检讨结果。

本地配置应放在被 Git 和 Docker 忽略的 `data/config.local.json`；下载的原始牌谱及解码所需协议文件保存在后端存储目录中，请保护并备份持久卷。完整的公开牌谱检讨结果缓存于持久化目录 `reviews/`（默认 `/mnt/workspace/majsoul-analyzer/reviews/`），保存 24 小时、软容量目标为 256 MiB 硬上限的 85%；内置演示及内部 JSON 输入不会写入。原始牌谱仍永久保存在 `records/`，不可与检讨缓存混淆。路径及恢复注意事项见 [原始牌谱存储](RECORD_STORAGE.md)。API 令牌与雀魂账号凭据是不同的秘密，均不应提交或公开。

**如果凭据曾进入 Git 历史，应立即在对应账号侧撤销或更换。**从当前文件删除凭据不会清除历史、远端副本或缓存；历史清理需另行规划，不应在未确认时强制改写仓库。

## ONNX CPU 参数

线程和批次可以通过服务端环境变量配置，默认行为不变：

| 环境变量 | 默认值 | 范围 | 含义 |
| --- | ---: | ---: | --- |
| `MAJSOUL_ONNX_INTRA_OP_THREADS` | 0 | 0–64 | 单个算子内部线程；0 使用 ONNX Runtime 自动值 |
| `MAJSOUL_ONNX_INTER_OP_THREADS` | 0 | 0–64 | 算子之间线程；0 使用 ONNX Runtime 自动值 |
| `MAJSOUL_ONNX_BATCH_SIZE` | 128 | 1–128 | 回放观测批次上限，不扩大原有默认内存预算 |

参数在进程启动时读取，修改后需重启。无效或空值会明确报配置错误，不会默默改变配置。两个线程参数均为 0 时不创建自定义 SessionOptions；默认模型、权重完整性校验、预热及推理并发均不改变。

没有一套线程配置适用于所有 ModelScope CPU。应先在隔离环境对照生产默认值与真实决策样本，确认 Q 值和动作一致，再决定是否更改环境变量；仓库不会自动运行性能基准或直接改变线上参数。可选基准见 [性能说明](docs/PERFORMANCE_AND_BACKUP.md)。

## 后端模块结构

`server/app.py` 负责应用启动及路由装配，存储路径与全局雀魂会话由独立的 `runtime_state.py` 管理，以避免业务模块反向导入 FastAPI 应用：

- **API 与安全**：`api_security.py`、`api_schemas.py`、`api_status.py`、`api_accounts.py`、`api_local_records.py`、`api_analysis.py`、`api_stream.py`。
- **检讨任务与缓存**：`game_data.py`、`review_jobs.py`、`public_review_cache.py`、`model_download.py`、`analysis_service.py`、`analysis_logging.py`、`sse_transport.py`。
- **牌谱 AI**：`engine/reviewer.py` 编排流程；`engine/review_replay.py` 构建回放与观测；`engine/review_scoring.py` 处理 Q 值、决策评分和汇总；`engine/actions.py` 处理动作映射。
- **雀魂客户端**：`majsoul/client.py` 负责网络会话；`majsoul/auth.py`、`majsoul/protocol_loader.py`、`majsoul/paipu_links.py`、`majsoul/record_codec.py` 分别负责认证、协议、链接和原始记录解码。
- **账号与自动注册**：`account_store.py`、`account_scheduler.py`、`account_credentials.py`、`account_pool.py`、`register_repository.py`、`register_runner.py`、`register_ingest.py`、`auto_register_scheduler.py`。

为兼容已有的入口，`app.py`、`reviewer.py`、`admin_accounts.py`、`account_register.py` 中仍保留部分名称的转发导入。测试目录及专用回归测试工作流已按维护要求移除；GitHub Actions 保留 ModelScope 发布工作流。

## 前端资源组织

首页仅预加载输入、引擎状态和加载/错误提示所需的脚本与样式，`web/app.js` 负责初始化。回放控制、牌桌、候选表和局分弹窗资源按需加载，与后端分析重叠，资源齐备后才显示回放。动态资源采用共享加载任务，失败后可重试；CSS 保持依赖顺序，不引入框架或改变牌桌布局。旧的 `web/styles.css` 继续保留作兼容入口。全部 38 个牌面 SVG 均已移除 Inkscape/Sodipodi 编辑器元数据和注释，不修改 SVG 标准绘图节点；文件总体积从 708,336 字节降至 644,544 字节，减少 63,792 字节（约 9.0%）。

仓库不再包含测试目录，也不会在 PR 或 main 更新时运行已删除的回归测试任务。ONNX 性能基准仍是独立的可选运维工具，不会自动执行。

## 致谢

- [Mortal](https://github.com/Equim-chan/Mortal)
- [KillerDucky](https://github.com/KillerDucky)
- [riichi-mahjong-tiles](https://github.com/FluffyStuff/riichi-mahjong-tiles)

第三方组件各自遵循其原有许可证；本仓库当前未提供项目级 LICENSE 文件，请在确定授权条款前不要假定本项目采用某种许可证。

## 运行期间性能汇总

通过带有效 API 令牌的 `GET /api/status` 可读取 `performance_recent`，包含最近最多 256 次分析请求的总耗时、排队、ONNX 推理和结果大小的 P50/P95。仅保存在当前服务进程的有界内存中，重启清空；不存储用户昵称、牌谱编号或 IP。匿名状态接口不包含这些统计。不同实例分别统计，不应将单进程分位数直接视为全站压测结果。

## 补充性能优化

- SSE 流式分析的进度等待同时监听进度队列和任务完成通知，不再每 250 毫秒轮询；保留 10 秒心跳。大型 complete 事件按 256 KiB 分块发送。
- SSE 分析解析器和诊断逻辑在首次发起分析时才加载，首页初始 API 脚本不再携带完整流式解析代码。
- 回放中手牌和牌河 DOM 只在对应 HTML 变化时替换；牌面异步解码，回放模块首次使用时按依赖顺序加载。
- 全部 38 个牌面 SVG 仅清理编辑器元数据与注释；原始绘图节点保持不变。
- 雀魂抓谱优先选择最久未尝试的空闲账号；每小时账号检测最多并发 3 个会话。网关 routes 发现合并并发请求；限流冷却条目与网关失效记录过期后回收；指定客户端的登录和换线重试都使用该客户端。
- 观测编码前先读取原生动作候选，跳过已确认无选择的立直后摸切及无可评后续动作；和牌、杠、流局与立直选择保持原有判定。新指标区分观测编码耗时、提前跳过次数和编码后跳过次数。
- 公开 Protobuf 牌谱任务直接复用原始字节摘要，不再为每个座位先序列化整场 MJAI；内部/历史 JSON 的事件指纹移到线程执行，并绑定当前检讨流水线版本。无生产者版本的旧缓存不再升级复用，首次访问会安全重算，旧文件仍由既有过期维护回收。
- 可以设置 `MAJSOUL_REVIEW_MAX_WAITERS`（1–32，默认 4）及 `MAJSOUL_REVIEW_QUEUE_TIMEOUT_SECONDS`（15–300 秒，默认 90）。默认 ONNX 推理并发仍为 1，避免未经负载评估增加内存压力。
