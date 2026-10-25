# 雀魂牌谱 AI 检讨复盘系统 (Majsoul Analyzer)

[![Cloudflare Pages](https://img.shields.io/badge/Cloudflare_Pages-Online-orange?logo=cloudflare)](https://majsoul.ccwu.cc)
[![Python](https://img.shields.io/badge/Python-3.10%20|%203.11%20|%203.12-blue?logo=python)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-Framework-009688?logo=fastapi)](https://fastapi.tiangolo.com)
[![PyTorch](https://img.shields.io/badge/PyTorch-Deep%20Learning-EE4C2C?logo=pytorch)](https://pytorch.org)

基于 Mortal 深度神经网络的日麻牌谱检讨分析系统。针对雀魂四人麻将对局设计，提供向听进张推演、舍牌决策概率分布对比以及全设备自适应的牌桌复盘界面。

在线检讨站点：[https://majsoul.ccwu.cc](https://majsoul.ccwu.cc)

---

## 核心特性

### 牌桌与牌河还原
- 标准日麻牌河：每行 6 张、共 3 行规范排列，手切、摸切与被鸣牌轨迹清晰标注。
- 立直状态真实还原：立直宣言牌真实横置，副露与鸣牌组合准确贴合对应方位。
- 矢量 SVG 牌面：采用规范的 Riichi Mahjong 矢量牌面渲染，适配高分辨率显示屏。

### 舍牌决策对比与推演
- 决策对比面板：直观展示模型推荐打法与实战打出牌的差异。
- 恶手与疑问手分级：依据得率与胜率波动自动标注最佳、微差、疑问手与恶手。
- 向听与进张推演：实时推演各项候选打法切出后的向听状态及有效进张数（Ukeire）。
- Top 5 候选概率分布：列出前 5 项候选打法的模型打率与权重柱状图。

### 交互与操控
- 局数快速跳转：支持半庄内各局一键切换。
- 自动巡航播放：内置 850ms 逐手自动回放。
- 明牌与暗牌切换：支持在四家全明牌视角与单人手牌实战视角之间切换（快捷键 H）。
- 快捷键快速导航：支持按键在关键切牌与恶手节点间快速跳转。

---

## 常用快捷键

| 快捷键 | 功能说明 | 对应操作 |
| :--- | :--- | :--- |
| 空格 (Space) | 自动播放 / 暂停 | 逐手巡航播放 |
| ← / → | 后退 / 前进一步 | 逐巡步进 1 手 |
| ↑ / ↓ | 上 / 下一切牌 | 在本家自身决策点之间跳转 |
| , / . 或 PageUp / PageDown | 上一恶手 / 下一恶手 | 快速定位模型判定的恶手局面 |
| [ / ] | 上一局 / 下一局 | 切换半庄小局 |
| Home / End | 本局开头 / 终局 | 跳转至配牌或终局结算 |
| H 或 单击对手手牌 | 切换明牌 / 暗牌 | 切换对手手牌可见状态 |
| 滚轮滚动 | 前后步进 | 鼠标滚轮向上/向下滚动单步微调 |
| Esc | 关闭弹窗 | 关闭得分表或设置弹窗 |

---

## 前端托管与部署

前端为纯静态单页架构（零打包构建依赖），源码位于 `web/` 目录中。

- 演示地址：[https://majsoul.ccwu.cc](https://majsoul.ccwu.cc)

### 部署至 Cloudflare Pages
1. 登录 Cloudflare Dashboard，进入 Workers & Pages -> Create application -> Pages -> Connect to Git。
2. 关联本代码仓库。
3. 构建参数设置：
   - Framework preset: None
   - Build output directory: `web`
4. 点击 Save and Deploy 即可完成全球 CDN 部署。

---

## 云端运行后端容器

若希望通过远程服务器或容器平台提供 AI 推理计算：

### ModelScope (魔搭社区) / Docker 容器部署
1. 前往 ModelScope 创空间新建应用，选择 Docker 部署或关联 GitHub 仓库。
2. 仓库根目录已包含完整 Dockerfile，集成 Python、PyTorch 与 libriichi Rust 依赖环境。
3. 部署完成后获取分配的公网 HTTPS 地址。
4. 打开前端网页，点击右上角「模型/服务设置」，填入该后端 URL 即可直连复盘。

---

## 本地运行 Python 分析后端

若在具备 NVIDIA GPU 或现代 CPU 的本地设备上运行后端：

### 1. 环境准备
需要 Python 3.10 或更高版本：
```bash
pip install -r requirements.txt
```
*(如需 GPU 加速，请根据本地显卡与驱动安装对应 CUDA 版本的 PyTorch)*

### 2. 账号凭据配置（可选）
如需直接抓取雀魂国服个人牌谱记录，复制配置模板并填入账号信息：
```bash
# Windows
copy data\config.example.json data\config.json

# Linux / macOS
cp data/config.example.json data/config.json
```
*(注：`data/config.json` 及对局缓存已默认加入 `.gitignore`，不会被提交)*

### 3. 启动服务
- Windows 用户：双击 `start.bat`。
- 命令行启动：
  ```bash
  python run.py
  ```
服务默认监听 `http://localhost:8000`，本地访问即可开始分析。

---

## 安全与隐私说明

- 凭据脱敏：接口不回传任何账号密码与敏感 Token。
- 本地存储保护：个人配置与分析记录均存放在本地目录，严禁上传至公共代码仓库。
- 跨域与纯静态：前端支持接入任意兼容 API 协议的后端推理节点，不收集任何用户私人数据。

---

## 架构与开源致谢

- [Mortal](https://github.com/Equim-chan/Mortal)：日麻深度学习与 libriichi 状态机推演引擎。
- [KillerDucky](https://github.com/KillerDucky)：日麻复盘排版与布局参考。
- [riichi-mahjong-tiles](https://github.com/FluffyStuff/riichi-mahjong-tiles)：矢量日麻牌图资源。
