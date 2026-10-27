# 雀魂牌谱 AI 检讨复盘系统

Majsoul Analyzer 将雀魂四人麻将牌谱转换为 MJAI 事件，使用 Mortal 神经网络分析决策，并提供四家视角的逐步回放。

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

输入牌谱链接或选择文件后点击“开始分析”；也可输入 `demo` 使用示例牌谱。分析完成后，点击对手手牌切换明牌/暗牌，或点击左侧座位切换视角。

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
docker run -p 7860:7860 --name majsoul-analyzer -e MAJSOUL_API_TOKEN=<自定义强随机令牌> -e MAJSOUL_ALLOWED_ORIGINS=https://example.com majsoul-analyzer:latest
```

将示例域名和令牌替换为自己的值；不要把真实令牌写入仓库或公开日志。Docker/ModelScope 发布前应配置好环境变量并验证实际启动情况。需要雀魂账号时，使用部署平台的私密环境变量 `MAJSOUL_ACCOUNT`、`MAJSOUL_PASSWORD` 或 `MAJSOUL_ACCESS_TOKEN`，不要将配置文件打包进镜像。

### 访问控制

- 未配置 `MAJSOUL_API_TOKEN` 时，仅允许本机回环地址的同源 API 访问；远程访问被拒绝。
- 配置令牌后，**所有 API 请求（包括本机）**都需携带 `Authorization: Bearer <令牌>`。跨来源浏览器前端还需设置 `MAJSOUL_ALLOWED_ORIGINS`，填写逗号分隔的精确来源（协议、主机和端口）。
- 可信浏览器可通过页面 URL 片段 `#api_token=<令牌>` 提供令牌。前端读取后会从地址栏移除片段，并将其保存在当前标签页的 sessionStorage 中、按后端来源隔离。令牌链接等同于凭据，只应通过安全渠道提供给受信任的人。
- `colab_launcher.py` 会为临时隧道生成访问令牌；其输出链接包含令牌，请勿公开分享。

### Cloudflare Pages

前端源码位于 `web/`。Pages 的构建命令可留空，输出目录为 `web`；远程 API 必须先正确配置令牌和来源白名单。仅部署静态页面并不会部署或启动后端。GitHub 更新和云平台部署是否成功，应以各平台的实际构建和运行状态为准。

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
