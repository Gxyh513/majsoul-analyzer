# 雀魂牌谱 AI 检讨复盘系统

Majsoul Analyzer 将雀魂四人麻将牌谱转换为 MJAI 事件，使用 Mortal 神经网络分析决策，并提供四家视角的逐步回放。

- 在线前端：[majsoul.ccwu.cc](https://majsoul.ccwu.cc)
- 后端创空间：[ModelScope / majsoul-analyzer](https://modelscope.cn/studios/Gxyh513/majsoul-analyzer)

线上使用情况以实际部署状态为准；受保护的远程服务需要访问令牌。

## 功能

- 导入雀魂牌谱链接、雀魂原始 JSON 或 MJAI JSON/JSONL；可使用内置示例牌谱。
- 在四家视角之间切换，查看牌河、立直、副露、杠、手切/摸切及结算。
- 显示各合法动作的 Mortal Q 值、候选排序和概率，以及整场和各局的检讨统计。
- 向听、进张、危险度及其他标签是独立的辅助统计，**不是 Mortal 的 Q 值**。
- 支持时间轴、播放速度、快捷键和 SSE 分析进度；支持本地牌谱与检讨结果缓存。

## 模型与结果来源

当前分析引擎**只使用** `data/models/mortal_582500.pth`。加载时检查 Mortal V4、40 个残差块、192 通道、训练步数 582500、严格网络结构，以及发布文件的 SHA-256：

```text
738e0d6e3c0ce9671629554ad39abd147d2ffbac676e80b194c83f2acc0fea20
```

权重缺失、损坏或校验失败时，分析会报错，**不会回退到内置启发式引擎或其他模型**。动作概率由模型 Q 值经温度 softmax 得到，并非模型的胜率或校准置信度。后端和浏览器只使用带有匹配模型指纹与结果版本的检讨缓存；升级后旧缓存可能需要重新分析。

权重发布源：[Yuchen1457/mortal-582500](https://huggingface.co/Yuchen1457/mortal-582500)。

## 本地运行

项目需要 Python 3.10+、PyTorch 和 `libriichi` 原生扩展。仓库中的 `server/engine/libriichi.pyd` 面向 Windows；Linux 需要编译兼容当前 Python 的 `libriichi.so`，可参考 Dockerfile 或 Colab 启动器。CUDA 可用时使用 GPU，否则使用 CPU。

1. 克隆仓库并安装依赖：

   ```bash
   git clone https://github.com/Gxyh513/majsoul-analyzer.git
   cd majsoul-analyzer
   pip install -r requirements.txt
   ```

2. 将上述发布源中的权重保存为 `data/models/mortal_582500.pth`。路径和文件名必须完全一致；启动后可通过 `/api/status` 查看校验状态。不要提交权重文件。
3. 如果需要通过雀魂账号拉取牌谱，将 `data/config.example.json` 复制为 `data/config.local.json`，再按需填写账号、密码或访问令牌。也可直接导入本地牌谱。
4. 启动服务：

   ```bash
   python run.py
   ```

   默认访问 `http://127.0.0.1:8000/`。Windows 也可使用 `start.bat`。

## 使用与快捷键

输入牌谱链接或选择文件后点击“开始分析”；也可输入 `demo` 使用示例牌谱。首次只分析当前玩家；点击左侧其他座位时才分析相应视角，已验证的结果会复用缓存。点击对手手牌仅切换明牌/暗牌，不会触发分析。JSON/MJAI 导入的原始内容只在当前页面会话中用于切换视角。

| 按键 | 操作 |
| --- | --- |
| Space | 播放 / 暂停 |
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
docker run -p 7860:7860 --name majsoul-analyzer -e MAJSOUL_API_TOKEN -e MAJSOUL_ALLOWED_ORIGINS majsoul-analyzer:latest
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

- Pages 前端通过同源 Pages Function 访问 ModelScope 后端；普通访客不需要手动输入 API 令牌。Function 固定代理到 `https://gxyh513-majsoul-analyzer.ms.show`，并在服务端注入 `X-Api-Token`。同源代理仅开放状态和分析接口；账号登录/登出、服务器本地牌谱、共享引擎配置、模型下载与缓存管理不会开放给匿名访问者。
- 在 Cloudflare Pages 项目的 **Preview 和 Production** 环境分别设置 Secret `MAJSOUL_API_TOKEN`，并在 ModelScope 后端设置相同的新随机值。不要把令牌写入 `wrangler.toml`、源码、URL、浏览器存储或日志；请在已登录的设置页面中输入，不要粘贴到聊天。
- 匿名 JSON 分析请求体上限为 4 MiB。按产品选择，不设每 IP 请求限额或等待队列上限；公开接口可被持续调用，可能让共享推理资源一直繁忙或占用等待队列内存。若出现滥用，应另行启用 Cloudflare 限流或队列控制。
- Pages 静态输出目录为 `web/`，Pages Function 位于仓库根目录的 `functions/`。部署后应检查正式域名的 `/api/status` 版本，并完成一次分析验证。

## API

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET | `/api/status` | 查询模型和连接状态 |
| POST | `/api/analyze/stream` | SSE 流式检讨 |
| POST | `/api/analyze/paipu` | 检讨雀魂牌谱链接或 ID |
| POST | `/api/analyze/json` | 检讨原始 JSON 或 MJAI 事件 |
| POST | `/api/analyze/demo` | 检讨示例牌谱 |
| GET | `/api/records/local` | 列出后端本地牌谱 |
| POST | `/api/login` | 配置雀魂账号登录 |

## 数据与安全

本地配置应放在被 Git 和 Docker 忽略的 `data/config.local.json`；牌谱和分析结果也可能保存在浏览器缓存与后端 `data/` 中，请按部署环境保护并清理。API 令牌与雀魂账号凭据是不同的秘密，均不应提交或公开。

**如果凭据曾进入 Git 历史，应立即在对应账号侧撤销或更换。**从当前文件删除凭据不会清除历史、远端副本或缓存；历史清理需另行规划，不应在未确认时强制改写仓库。

## 开发验证

```bash
python -m pytest tests -q
```

```bash
node --check web/app.js
```

部分测试需要已安装的原生扩展、Node.js 或指定模型权重；符号链接测试还依赖系统权限。

## 致谢

- [Mortal](https://github.com/Equim-chan/Mortal)
- [KillerDucky](https://github.com/KillerDucky)
- [riichi-mahjong-tiles](https://github.com/FluffyStuff/riichi-mahjong-tiles)

第三方组件各自遵循其原有许可证；本仓库当前未提供项目级 LICENSE 文件，请在确定授权条款前不要假定本项目采用某种许可证。
