# 雀魂 AI 牌谱检讨复盘系统 (Majsoul Analyzer)

[![Cloudflare Pages](https://img.shields.io/badge/Cloudflare_Pages-Online-orange?logo=cloudflare)](https://majsoul.ccwu.cc)
[![Python](https://img.shields.io/badge/Python-3.10%20|%203.11%20|%203.12-blue?logo=python)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-Framework-009688?logo=fastapi)](https://fastapi.tiangolo.com)
[![PyTorch](https://img.shields.io/badge/PyTorch-Deep%20Learning-EE4C2C?logo=pytorch)](https://pytorch.org)
[![Open In Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/Gxyh513/majsoul-analyzer/blob/main/colab_run.ipynb)

基于 **Mortal** 深度神经网络的高段位日麻牌谱检讨分析系统。系统针对雀魂王座之间与玉之间对局设计，提供精确的向听进张推演、大牌面决策对比与现代化全设备自适应复盘界面。

🌐 **在线检讨体验**：[https://majsoul.ccwu.cc](https://majsoul.ccwu.cc)

---

## 🌟 核心亮点与特色

### 🀄 现代化高清晰牌桌 UI
- **标准日麻牌河布局**：严格规范每行 6 张、共 3 行的规整牌河展现，布局规整绝无重叠错位。
- **立直宣言牌真实横置**：精准呈现立直横置切牌状态，并智能标记手切、摸切与被鸣牌轨迹。
- **高清晰矢量 SVG 牌图**：采用规范 Riichi Mahjong 矢量牌面渲染，在各类高分屏与视网膜屏幕上始终清晰锐利。

### 📊 大尺寸切牌与 AI 决策对比面板
- **竖向大牌面高对比呈现**：上方大卡片展示 **AI 推荐打法**，下方卡片直观对比**实战打出**，牌面一目了然。
- **恶手 / 疑问手醒目标注**：基于 AI 得率评分自动区分「一致（最佳）」、「微差」、「疑问」与「恶手（Blunder）」等级，并辅以高饱和度视觉告警。
- **向听数与进张数深度推演**：实时计算每项候选舍牌后的向听状态（听牌/一向听/二向听等）与有效进张张数（Ukeire）。
- **候选打法评分分布**：清晰列出 Top 5 候选舍牌的 AI 胜率/得率推荐度柱状数据，辅助深度领会 AI 攻守博弈抉择。

### 📱 全设备与移动端自适应
- 采用弹性自适应容器与高对比度暗色主题，完美适配桌面宽屏、平板电脑以及移动端智能手机。

### ⚡ 职业级高效复盘操控
- **局数快速跳转**：顶部集成局数下拉选单，东风场/南风场各小局一键直达，告别繁琐翻页。
- **自动巡航播放**：支持一键开启/暂停自动播放（850ms 逐手巡航），沉浸式还原实战对局节奏。
- **明牌 / 暗牌即刻切换**：单击牌桌对手手牌区域或按下快捷键 `H`，即可随时在「四家明牌上帝视角」与「单人手牌实战视角」之间秒切。
- **恶手快速步进**：支持按键直达上一处/下一处恶手，快速聚焦重大失误点。

---

## ⌨️ 常用快捷键速查表

在检讨复盘界面中，您可以通过以下键盘快捷键进行高效盲操：

| 快捷键 | 功能说明 | 对应操作 |
| :--- | :--- | :--- |
| `空格` (Space) | **自动播放 / 暂停** | 850ms 节奏逐手巡航播放 |
| `←` / `→` | **后退 / 前进一步** | 逐巡前进或后退 1 手 |
| `↑` / `↓` | **上 / 下一切牌** | 快速在玩家自身的切牌决策点之间跳转 |
| `,` / `.` 或 `PageUp` / `PageDown` | **上一恶手 / 下一恶手** | 毫秒级跳转到下一个 AI 判定的恶手局面 |
| `[` / `]` | **上一局 / 下一局** | 切换东场/南场各个半庄小局 |
| `Home` / `End` | **本局开头 / 终局** | 跳至配牌开局或流局/和牌终局 |
| `H` 或 `单击对手手牌` | **切换明牌 / 暗牌** | 切换别家手牌的可见性 |
| `牌桌滚轮滚动` | **前后步进** | 鼠标滚轮向上/向下滚动即可前进/后退 |
| `Esc` | **关闭弹窗** | 关闭局数明细或引擎设置弹窗 |

---

## 🌐 在线体验与前端托管

本项目前端为**纯静态架构**（零打包构建依赖），位于 `web/` 目录中，支持全球 CDN 部署。

### 官方演示站点
访问地址：[https://majsoul.ccwu.cc](https://majsoul.ccwu.cc)

### 部署至 Cloudflare Pages（免运维极速托管）
1. 登录 [Cloudflare Dashboard](https://dash.cloudflare.com/)。
2. 进入 **Workers & Pages** -> **Create application** -> **Pages** -> **Connect to Git**。
3. 选择关联本 GitHub 仓库。
4. 构建配置（已通过根目录 `wrangler.toml` 预设）：
   - **Framework preset**: None
   - **Build output directory**: `web`
5. 点击 **Save and Deploy** 即可获得专属全球加速域名。后续 `git push` 将自动触发增量发布。

---

## ☁️ 云端运行 AI 引擎（脱离本地环境）

若希望无需本地开机即可随时进行牌谱 AI 检讨，可选择以下免费云端方案：

### 方案一：ModelScope (魔搭社区) / Docker 容器托管
1. 前往 [ModelScope 创空间](https://modelscope.cn/studios) 或容器平台创建新应用。
2. 选择使用 **Docker 镜像** 或连接 GitHub 代码仓库。
3. 本项目根目录下已提供全自动 [Dockerfile](file:///c:/Users/22/.gemini/antigravity/scratch/majsoul-analyzer/Dockerfile)，集成了轻量级 PyTorch 与 Rust 编译环境。
4. 构建完成后，在平台获取公开分配的 HTTPS 域名。
5. 打开 [https://majsoul.ccwu.cc](https://majsoul.ccwu.cc)，点击右上角 **⚙ 模型/服务设置**，填入该后端 URL 即可连接。

### 方案二：Hugging Face Spaces（24小时全天候免费托管）
1. 登录 [Hugging Face](https://huggingface.co/) 进入 [Spaces](https://huggingface.co/spaces)。
2. 点击 **Create new Space**：
   - **Space SDK**: 选择 **Docker** -> **Blank**
   - **Hardware**: 选择免费的 **CPU Basic (2 vCPU, 16GB RAM)**
3. 将本项目推送到该 Space（或在 Space 设置中直接关联 GitHub 仓库）。
4. 部署就绪后即可获得公开 API 服务地址（形如 `https://<用户名>-majsoul.hf.space`）。

### 方案三：Google Colab（免费 T4 GPU 算力一键启动）
1. 打开 [Google Colab 笔记本](https://colab.research.google.com/)，在菜单栏中配置硬件加速器为 **T4 GPU**。
2. 新建代码单元格并执行：
   ```bash
   !curl -sSL https://raw.githubusercontent.com/Gxyh513/majsoul-analyzer/main/colab_launcher.py | python
   ```
3. 执行后终端将输出临时公网访问域名（例如 `https://xxxx.trycloudflare.com`）。
4. 将该地址填入前端界面右上角的**服务设置**中，即可享受秒级 GPU 推理检讨。

---

## 💻 本地运行 Python 分析后端

如果您拥有本地计算设备（支持 NVIDIA CUDA 或纯 CPU 推理）：

### 1. 环境准备
确保已安装 Python 3.10 或更高版本：
```bash
# 安装依赖项
pip install -r requirements.txt
```
*(注：若使用 GPU 加速，请根据显卡配置安装匹配的 CUDA 版本 PyTorch)*

### 2. 账号与配置
复制配置模板：
```bash
# Windows
copy data\config.example.json data\config.json

# Linux / macOS
cp data/config.example.json data/config.json
```
在 `data/config.json` 中配置您的雀魂登录凭证（用于抓取个人对局牌谱记录，该文件已被 `.gitignore` 保护）。

### 3. 启动服务
- **Windows 用户**：直接双击 `start.bat`。
- **命令行启动**：
  ```bash
  python run.py
  ```
服务将在本地 `http://localhost:8000` 启动，浏览器访问即可立即使用。

---

## 🔒 安全与隐私说明

- **脱敏合规**：所有个人账户凭据与 Token 均已从代码历史中严格净化。
- **本地凭证保护**：用户专属配置文件 `data/config.json` 及抓取的对局缓存默认包含在 `.gitignore` 中，严禁上传至公共代码仓库。
- **纯前端直连**：前端支持任意兼容标准的后置分析端点切换，不收集任何用户隐私数据。

---

## 📄 架构与开源致谢

本项目基于日麻开源社区的卓越贡献开发构建：
- **[Mortal](https://github.com/Equim-chan/Mortal)**：经典的日麻深度学习与 `libriichi` 状态机推演引擎。
- **[KillerDucky](https://github.com/KillerDucky)**：优秀的日麻复盘排版灵感来源。
- **[riichi-mahjong-tiles](https://github.com/FluffyStuff/riichi-mahjong-tiles)**：高质量矢量日麻牌图资源。
