"""Google Colab / 云端 Linux GPU 一键启动脚本
在 Google Colab (选择 T4 GPU 运行时) 中执行：
!curl -sSL https://raw.githubusercontent.com/Gxyh513/majsoul-analyzer/main/colab_launcher.py | python
"""
import os
import re
import subprocess
import sys
import time

print("=" * 65)
print(" 🚀 正在初始化 雀魂牌谱 AI 检讨系统 (云端 GPU 加速环境)...")
print("=" * 65)

# 1. Clone repository if not present
if not os.path.isdir("majsoul-analyzer"):
    print("📦 拉取最新代码...")
    subprocess.run(["git", "clone", "https://github.com/Gxyh513/majsoul-analyzer.git"], check=True)
    os.chdir("majsoul-analyzer")
else:
    os.chdir("majsoul-analyzer")

# 2. Install Python dependencies
print("📥 安装 Python 依赖...")
subprocess.run([sys.executable, "-m", "pip", "install", "-q", "-r", "requirements.txt"], check=True)

# 3. Compile Linux native libriichi.so if not exists
so_path = os.path.join("server", "engine", "libriichi.so")
if not os.path.isfile(so_path):
    print("⚙️ 编译 Linux libriichi 核心计算库 (使用 Rust)...")
    try:
        subprocess.run(["cargo", "build", "-p", "libriichi", "--release"], cwd="vendor/Mortal", check=True)
        rel_so = os.path.join("vendor", "Mortal", "target", "release", "libriichi.so")
        if os.path.isfile(rel_so):
            import shutil
            shutil.copyfile(rel_so, so_path)
            print("✅ libriichi.so 编译成功！")
    except Exception as e:
        print(f"⚠️ Cargo 编译提示: {e}，尝试继续...")

# 4. Download AI weights if missing
models_dir = os.path.join("data", "models")
os.makedirs(models_dir, exist_ok=True)
mortal_path = os.path.join(models_dir, "mortal.pth")
if not os.path.isfile(mortal_path):
    print("🧠 下载 Mortal (v3) 神经网络权重...")
    subprocess.run(["curl", "-sSL", "-o", mortal_path, "https://huggingface.co/VoidShine/mortal-298k/resolve/main/mortal_298k.pth"])

akagi_path = os.path.join(models_dir, "akagi.pth")
if not os.path.isfile(akagi_path):
    print("🔥 下载 赤木 Akagi (v4 582k) 强化模型权重...")
    subprocess.run(["curl", "-sSL", "-o", akagi_path, "https://huggingface.co/Yuchen1457/mortal-582500/resolve/main/mortal_582500.pth"])

# 5. Download cloudflared for public tunneling
if not os.path.isfile("cloudflared"):
    print("🌐 配置 Cloudflare 安全隧道...")
    subprocess.run(["curl", "-sSL", "-o", "cloudflared", "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64"], check=True)
    subprocess.run(["chmod", "+x", "cloudflared"], check=True)

# 6. Start tunnel and capture public URL
print("🚀 启动公网隧道...")
tunnel = subprocess.Popen(["./cloudflared", "tunnel", "--url", "http://127.0.0.1:8000"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

tunnel_url = None
start_t = time.time()
while time.time() - start_t < 30:
    line = tunnel.stdout.readline()
    if not line:
        break
    m = re.search(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com", line)
    if m:
        tunnel_url = m.group(0)
        break

if tunnel_url:
    one_click_url = f"https://majsoul-analyzer.pages.dev/?backend={tunnel_url}"
    print("\n" + "=" * 65)
    print(" 🎉 云端 GPU AI 引擎已成功启动！")
    print(f" 👉 您的云端 API 服务地址为:  {tunnel_url}")
    print(f" 🔗 点击直接进入复盘网页 (已自动连接):")
    print(f"    👉 {one_click_url}")
    print("=" * 65 + "\n")
else:
    print("⚠️ 隧道地址解析超时，请检查日志。")

# 7. Start backend service
subprocess.run([sys.executable, "run.py", "--no-browser"])
