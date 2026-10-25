# 雀魂牌谱 AI 检讨复盘系统 (Majsoul Analyzer)

<p align="center">
  <a href="https://majsoul.ccwu.cc">
    <img src="web/avatar.png" width="100" height="100" alt="Majsoul Analyzer Logo" style="border-radius: 50%;">
  </a>
</p>

<p align="center">
  <strong>基于深度学习的日本麻将高精度复盘检讨系统 · 专为雀魂四人麻将设计</strong>
</p>

<p align="center">
  <a href="https://majsoul.ccwu.cc"><img src="https://img.shields.io/badge/Production-majsoul.ccwu.cc-00f0ff?style=flat-square&logo=cloudflare" alt="Online Demo"></a>
  <a href="https://modelscope.cn/studios/Gxyh513/majsoul-analyzer"><img src="https://img.shields.io/badge/ModelScope-创空间运行中-624aff?style=flat-square" alt="ModelScope Studio"></a>
  <a href="https://python.org"><img src="https://img.shields.io/badge/Python-3.10%20|%203.11%20|%203.12-3776AB?style=flat-square&logo=python" alt="Python"></a>
  <a href="https://pytorch.org"><img src="https://img.shields.io/badge/PyTorch-2.x-EE4C2C?style=flat-square&logo=pytorch" alt="PyTorch"></a>
  <a href="https://fastapi.tiangolo.com"><img src="https://img.shields.io/badge/FastAPI-High%20Performance-009688?style=flat-square&logo=fastapi" alt="FastAPI"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-green?style=flat-square" alt="License"></a>
</p>

---

## 项目简介

**Majsoul Analyzer** 是一套现代化、开箱即用的雀魂日麻牌谱分析复盘系统。系统通过解析雀魂对局原始协议（Protobuf），依托深度残差神经网络对整场半庄的每一巡打法进行量化评估，提供与日麻高段位 AI 的切牌概率对比、向听进张推演、全庄得点变动回溯及多重视角复盘。

- **在线检讨站点**：[https://majsoul.ccwu.cc](https://majsoul.ccwu.cc)
- **后端服务托管**：ModelScope (魔搭社区) 容器化独立计算节点

---

## 核心特性

### 1. 双强模型与动态引擎切换
- **Mortal-V4-582500**：582,000 步高强度强化学习训练，风格激进、攻守敏锐，偏向高压做牌与精准进攻。
- **Mortal 核心 (v3)**：天凤十段水平基准模型，风格均衡细腻、注重防守与顺位战局观。
- 支持在复盘界面实时查看模型权重分布与推理置信度。

### 2. SSE 实时流式极速分析
- 后端采用异步事件流（Server-Sent Events）驱动，无需漫长等待整包返回。
- 牌谱下载、MJAI 事件转换、批量神经网络推理全生命周期进度实时直观呈现。
- 内置对局与检讨缓存机制，相同牌谱二次加载秒级呈现。

### 3. 专业级日麻牌桌与牌河还原
- **标准牌河结构**：严谨还原标准日麻牌河（每行 6 张、共 3 行规范排列）。
- **打牌痕迹标注**：手切、摸切、被鸣牌原位留空及灰色高亮标记一目了然。
- **立直宣言与副露**：真实呈现立直横置牌与鸣牌组合方向（下家、对家、上家方向精准贴合）。
- **矢量 SVG 渲染**：高精度 Riichi Mahjong 矢量牌面，无损适配各类高刷屏与移动设备。

### 4. 深度决策拆解与向听推演
- **四级评价体系**：根据与 AI 最佳切牌的得率损耗自动标注 **模型一致**、**微差**、**疑问手** 与 **恶手**。
- **Top 5 候选概率对比**：完整展示当前巡目模型打率排名前 5 的决策建议及权重柱状图。
- **向听与进张推演**：切出任意候选牌后，实时推演最新向听数及剩余有效进张张数（Ukeire）。

### 5. 多人视角与全庄得点表
- **四家自由切换**：随时切换至东南西北任意玩家视角，实时重算该玩家的一致率、恶手率与评分。
- **全庄结算明细**：集成局数得点变动模态框，直观展示全庄各局和牌类型（荣和/自摸/流局）、庄家本场数及各家点数流向。
- **隐私保护模式**：一键隐藏玩家 ID 与真实昵称，录屏分享更安全。

---

## 常用快捷键

针对日麻高阶玩家设计了全键盘操作流，单手即可完成高效率复盘：

| 快捷键 | 功能说明 | 对应操作 |
| :---: | :--- | :--- |
| **Space** | 自动播放 / 暂停 | 以 850ms/步 逐巡自动巡航回放 |
| **← / →** | 后退 / 前进一步 | 逐巡步进 1 手 |
| **↑ / ↓** | 上 / 下一切牌 | 直接在本家自身决策点之间快速跳转 |
| **, / .** | 上一恶手 / 下一恶手 | 快速定位 AI 判定的恶手/疑问手局面 |
| **[ / ]** | 上一局 / 下一局 | 切换半庄各小局（东1局、东2局……） |
| **Home / End** | 本局起点 / 终局 | 一键跳转至开局配牌或终局结算 |
| **H** | 切换明牌 / 暗牌 | 切换对手手牌可见状态（还原实战视野） |
| **M** | 切换出牌音效 | 开启或关闭真实清脆的切牌落桌音效 |
| **鼠标滚轮** | 前后步进 | 鼠标悬停牌桌任意处滚轮缩放与步进 |
| **Esc** | 关闭弹窗 | 快捷关闭局数得点表或模型设置窗口 |

---

## 本地运行与部署

### 方式一：直接使用在线前端（最推荐）
访问 [https://majsoul.ccwu.cc](https://majsoul.ccwu.cc)，粘贴雀魂牌谱链接即可直接复盘，无需安装任何环境。

---

### 方式二：本地运行 Python 后端

#### 1. 克隆代码与安装依赖
建议使用 Python 3.10 或更高版本：
```bash
git clone https://github.com/Gxyh513/majsoul-analyzer.git
cd majsoul-analyzer

# 安装基础依赖
pip install -r requirements.txt
```
*(若有 NVIDIA GPU，建议根据 CUDA 版本安装对应的 PyTorch 以获得 10 倍以上的推理加速)*

#### 2. 配置雀魂下载账号（可选）
如果需要通过后端直接拉取雀魂国服牌谱，可配置专用下载账号：
```bash
# Windows
copy data\config.example.json data\config.json

# Linux / macOS
cp data/config.example.json data/config.json
```
编辑 `data/config.json` 填入账号密码（系统支持配置备用小号自动轮换）。

#### 3. 启动服务
- **Windows 用户**：直接双击根目录下的 `start.bat`。
- **命令行用户**：
  ```bash
  python run.py
  ```
服务默认启动于 `http://localhost:8000`。浏览器访问该地址或使用前端配置该 API 即可。

---

### 方式三：容器化云端部署 (ModelScope / Docker)

仓库已内置生产级 `Dockerfile`，集成完整 Python、PyTorch、libriichi Rust 底层加速及预置权重：

```bash
# 构建镜像
docker build -t majsoul-analyzer:latest .

# 启动容器并暴露 7860 端口
docker run -d -p 7860:7860 --name majsoul-analyzer majsoul-analyzer:latest
```

在 ModelScope (魔搭社区) 创空间中，直接关联本 GitHub 仓库，选择 Docker 运行环境即可实现零配置自动化上线。

---

### 方式四：前端静态托管 (Cloudflare Pages)

前端为纯原生静态架构（零打包、零 Node 构建依赖），源码位于 `web/` 目录：
1. 登录 Cloudflare Dashboard，进入 **Workers & Pages** -> **Create application** -> **Pages**。
2. 关联本 GitHub 仓库。
3. 构建配置：
   - **Framework preset**: `None`
   - **Build command**: *(留空)*
   - **Build output directory**: `web`
4. 点击 **Save and Deploy** 即可获得全球 Edge CDN 加速的检讨站点。

---

## API 接口速查

后端基于 FastAPI 提供高性能异步接口，前端或其他客户端均可直连：

| 方法 | 路径 | 描述 |
| :---: | :--- | :--- |
| `GET` | `/api/status` | 查询后端健康状态、AI 模型加载情况与雀魂账号登录态 |
| `POST` | `/api/analyze/stream` | SSE 流式检讨接口，实时推送推理进度与完整复盘数据 |
| `POST` | `/api/analyze/paipu` | 针对指定牌谱 URL / 牌谱 ID 的全量异步检讨接口 |
| `POST` | `/api/analyze/json` | 上传本地已下载的雀魂原始 JSON 牌谱进行复盘 |
| `POST` | `/api/analyze/demo` | 快速加载演示牌谱（供脱机离线测试） |
| `POST` | `/api/engine/config` | 切换默认激活的 AI 模型（`akagi` / `mortal`） |
| `POST` | `/api/login` | 雀魂国服账号登录 / 获取 Access Token |

---

## 隐私与安全性

1. **零数据留存**：前端为纯静态单页架构，不收集任何用户隐私数据，所有牌谱分析记录仅缓存在用户本地会话与隔离的后端存储中。
2. **安全审计**：生产代码已进行全量安全审计，排除了任何个人敏感信息或开发临时凭证。

---

## 致谢与开源协议

本项目得益于以下优秀开源项目的启发与支持：
- [Mortal](https://github.com/Equim-chan/Mortal) - 卓越的日麻深度强化学习模型与 libriichi 状态推演引擎。
- [KillerDucky](https://github.com/KillerDucky) - 优雅直观的日麻复盘界面布局与交互参考。
- [riichi-mahjong-tiles](https://github.com/FluffyStuff/riichi-mahjong-tiles) - 高精度日麻矢量 SVG 牌面资源。

本项目采用 [MIT License](LICENSE) 开源。欢迎提交 Issue 与 Pull Request 共同改进！
