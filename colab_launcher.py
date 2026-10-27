"""Google Colab / 云端 Linux GPU 一键启动脚本
在 Google Colab (选择 T4 GPU 运行时) 中执行：
!curl -sSL https://raw.githubusercontent.com/Gxyh513/majsoul-analyzer/main/colab_launcher.py | python
"""
import os
import hashlib
import secrets
import re
import shutil
import subprocess
import sys
import time

print("=" * 65)
print(" 🚀 正在初始化 雀魂牌谱 AI 检讨系统 (云端 GPU 加速环境)...")
print("=" * 65)

# 1. Determine project directory
if os.path.isfile("run.py") and os.path.isdir("vendor/Mortal"):
    root_dir = os.path.abspath(os.getcwd())
elif os.path.isdir("majsoul-analyzer") and os.path.isfile("majsoul-analyzer/run.py"):
    root_dir = os.path.abspath("majsoul-analyzer")
else:
    print("📦 拉取最新代码...")
    subprocess.run(["git", "clone", "https://github.com/Gxyh513/majsoul-analyzer.git"], check=True)
    root_dir = os.path.abspath("majsoul-analyzer")

os.chdir(root_dir)
subprocess.run(["git", "pull", "origin", "main"], check=False)

# 2. Install Python dependencies
print("📥 安装 Python 依赖...")
subprocess.run([sys.executable, "-m", "pip", "install", "-q", "-r", "requirements.txt"], check=True)

# 3. Ensure Rust/Cargo is installed & compile Linux native libriichi.so
cargo_bin = os.path.expanduser("~/.cargo/bin")
if cargo_bin not in os.environ.get("PATH", ""):
    os.environ["PATH"] = f"{cargo_bin}:{os.environ.get('PATH', '')}"

so_path = os.path.join(root_dir, "server", "engine", "libriichi.so")
if not os.path.isfile(so_path):
    mortal_cargo = os.path.join(root_dir, "vendor", "Mortal", "Cargo.toml")
    if not os.path.isfile(mortal_cargo):
        print("📦 拉取 Mortal 核心源码库...")
        shutil.rmtree(os.path.join(root_dir, "vendor", "Mortal"), ignore_errors=True)
        subprocess.run(["git", "clone", "--depth", "1", "https://github.com/Equim-chan/Mortal.git", os.path.join(root_dir, "vendor", "Mortal")], check=True)

    if shutil.which("cargo") is None:
        print("🦀 安装 Rust/Cargo 编译工具链...")
        subprocess.run("curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y -q", shell=True, check=True)
        if cargo_bin not in os.environ.get("PATH", ""):
            os.environ["PATH"] = f"{cargo_bin}:{os.environ.get('PATH', '')}"

    print("⚙️ 编译 Linux libriichi 核心计算库 (使用 Rust)...")
    env = os.environ.copy()
    env["PATH"] = f"{cargo_bin}:{env.get('PATH', '')}"
    try:
        subprocess.run(["cargo", "build", "-p", "libriichi", "--lib", "--release"], cwd=os.path.join(root_dir, "vendor", "Mortal"), env=env, check=True)
        found = False
        for root, dirs, files in os.walk(os.path.join(root_dir, "vendor", "Mortal", "target", "release")):
            for f in files:
                if f.endswith(".so") and "riichi" in f:
                    src = os.path.join(root, f)
                    shutil.copyfile(src, so_path)
                    print(f"✅ {f} -> {so_path} 拷贝成功！")
                    found = True
                    break
            if found:
                break
    except Exception as e:
        print(f"⚠️ Cargo 编译提示: {e}")

# Verify libriichi import
try:
    subprocess.run([sys.executable, "-c", "import sys; sys.path.insert(0, 'server/engine'); import libriichi; print('✅ libriichi 核心库已正常加载！')"], check=True)
except Exception:
    print("⚠️ libriichi 验证未通过，可能影响 AI 检讨执行")

# 4. Download AI weights if missing
models_dir = os.path.join("data", "models")
os.makedirs(models_dir, exist_ok=True)
model_path = os.path.join(models_dir, "mortal_582500.pth")
expected_sha = "738e0d6e3c0ce9671629554ad39abd147d2ffbac676e80b194c83f2acc0fea20"
def model_sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()

if not os.path.isfile(model_path) or model_sha256(model_path) != expected_sha:
    print("🧠 下载并校验 Mortal-V4-582500 权重...")
    temp_path = model_path + ".tmp"
    try:
        subprocess.run(["curl", "-fSL", "--retry", "3", "-o", temp_path,
                        "https://huggingface.co/Yuchen1457/mortal-582500/resolve/main/mortal_582500.pth"], check=True)
        if model_sha256(temp_path) != expected_sha:
            raise RuntimeError("模型权重 SHA-256 校验失败")
        os.replace(temp_path, model_path)
    finally:
        if os.path.isfile(temp_path):
            os.unlink(temp_path)

# Remote access is authenticated; the token stays in the URL fragment, not
# the backend URL query or HTTP request logs.
api_token = os.environ.get("MAJSOUL_API_TOKEN") or secrets.token_urlsafe(32)
os.environ["MAJSOUL_API_TOKEN"] = api_token
os.environ.setdefault("MAJSOUL_ALLOWED_ORIGINS", "https://majsoul-analyzer.pages.dev")

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
    web_url = f"https://majsoul-analyzer.pages.dev/?backend={tunnel_url}#api_token={api_token}"
    print("\n" + "=" * 65)
    print(" 🎉 云端 GPU AI 引擎已成功启动！")
    print(f" 👉 您的云端 API 服务地址为:  {tunnel_url}")
    print("\n 🔗 点击直接进入复盘网页 (已自动连接):")
    print(f"    👉 {web_url}")
    print("=" * 65 + "\n")
else:
    print("⚠️ 隧道地址解析超时，请检查日志。")

# 7. Start backend service
subprocess.run([sys.executable, "run.py", "--no-browser"])
