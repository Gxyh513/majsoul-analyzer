# 雀魂牌谱 AI 检讨系统 (Majsoul Analyzer)

基于深度学习神经网络（Akagi / Mortal）与 NAGA 风格交互界面的雀魂牌谱复盘检讨系统。

## 🌟 特性

- **现代化牌桌 UI**：紧凑自适应牌桌布局，支持点击切换对手手牌视角、高对比度切牌与副露高亮。
- **神经网络双引擎支持**：
  - **Akagi (赤木)**：基于 CUDA 加速的高强度日麻 AI 决策模型
  - **Mortal**：经典日麻分析模型
- **细粒度决策评估**：展示一致、微差、疑问、恶手等级，并提供打法候选项的得率分布与向听数 / 进张分析。
- **纯静态前端友好**：前端界面全部位于 `web/`，支持一键无缝托管至 **Cloudflare Pages**。

---

## 🚀 部署至 Cloudflare Pages

本项目前端支持通过 Cloudflare Pages 进行全球 CDN 高速托管。

### 方式：GitHub 自动联动部署（推荐）

1. 登录 [Cloudflare Dashboard](https://dash.cloudflare.com/)。
2. 进入 **Workers & Pages** -> **Create application** -> **Pages** -> **Connect to Git**。
3. 选择 GitHub 仓库 `Gxyh513/majsoul-analyzer`。
4. 构建配置（已由根目录 `wrangler.toml` 自动声明）：
   - **Framework preset**: None
   - **Build output directory**: `web`
5. 点击 **Save and Deploy** 即可生成公网访问地址（如 `https://majsoul-analyzer.pages.dev`）。后续每次 `git push` 将自动触发部署。

---

## 💻 本地运行后端分析服务

由于 AI 模型推理（PyTorch + CUDA）及雀魂协议握手运行在 Python 后端：

### 1. 环境准备
```bash
pip install -r requirements.txt # 或安装 fastapi uvicorn torch websockets
```

### 2. 账号与配置
复制模板文件：
```bash
cp data/config.example.json data/config.json
```
在 `data/config.json` 中配置您的雀魂登录凭证（已在 `.gitignore` 中忽略，安全防泄露）。

### 3. 启动服务
双击运行 `start.bat` 或在终端执行：
```bash
python run.py
```
服务将在 `http://localhost:8000` 启动，打开浏览器即可进入复盘系统。

---

## 🔒 安全说明
本项目已进行严格的敏感信息脱敏处理：
- 真实账户密码与 Token 均已从 Git 历史树中彻底净化。
- 敏感配置文件 `data/config.json` 默认被 `.gitignore` 忽略，请勿提交个人真实凭证。
