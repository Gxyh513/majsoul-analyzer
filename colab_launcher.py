"""Google Colab / 云端 Linux 一键启动脚本（ONNX Runtime CPU）
在 Google Colab 中执行：
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
print(" 🚀 正在初始化 雀魂牌谱 AI 检讨系统 (云端 CPU 环境)...")
print("=" * 65)

# 1. Determine project directory
if os.path.isfile("run.py"):
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
mortal_dir = os.path.join(root_dir, "vendor", "Mortal")
mortal_commit = "0cff2b52982be5b1163aa9a62fb01f03ce91e0d2"
if not os.path.isdir(os.path.join(mortal_dir, ".git")):
    print("📦 拉取 Mortal 核心源码库...")
    subprocess.run(["git", "init", mortal_dir], check=True)
subprocess.run(["git", "-C", mortal_dir, "fetch", "--depth", "1",
                "https://github.com/Equim-chan/Mortal.git", mortal_commit], check=True)
subprocess.run(["git", "-C", mortal_dir, "checkout", "--detach", "FETCH_HEAD"], check=True)
# Do not build a locally modified checkout as if it were the pinned source.
subprocess.run(["git", "-C", mortal_dir, "diff", "--quiet"], check=True)
subprocess.run(["git", "-C", mortal_dir, "diff", "--cached", "--quiet"], check=True)

if shutil.which("cargo") is None:
    print("🦀 安装 Rust/Cargo 编译工具链...")
    subprocess.run("curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y -q", shell=True, check=True)
    if cargo_bin not in os.environ.get("PATH", ""):
        os.environ["PATH"] = f"{cargo_bin}:{os.environ.get('PATH', '')}"

print("⚙️ 编译 Linux libriichi 核心计算库 (使用 Rust)...")
env = os.environ.copy()
env["PATH"] = f"{cargo_bin}:{env.get('PATH', '')}"
subprocess.run(["cargo", "build", "-p", "libriichi", "--lib", "--release"], cwd=mortal_dir, env=env, check=True)
shutil.copyfile(os.path.join(mortal_dir, "target", "release", "libriichi.so"), so_path)

# Verify libriichi import
try:
    subprocess.run([sys.executable, "-c", "import sys; sys.path.insert(0, 'server/engine'); import libriichi; print('✅ libriichi 核心库已正常加载！')"], check=True)
except Exception:
    print("⚠️ libriichi 验证未通过，可能影响 AI 检讨执行")

# 4. Download AI weights if missing
models_dir = os.path.join("data", "models")
os.makedirs(models_dir, exist_ok=True)
model_path = os.path.join(models_dir, "model.onnx")
expected_sha = "30381bf21389e2d25d3edf48fed11bf772336ef3daeee67d1bdae7fb9dd30cb4"
def model_sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()

if not os.path.isfile(model_path) or model_sha256(model_path) != expected_sha:
    print("🧠 下载并校验 Mortal S42 ONNX 模型...")
    temp_path = model_path + ".tmp"
    try:
        subprocess.run(["curl", "-fSL", "--retry", "3", "-o", temp_path,
                        "https://huggingface.co/xzhe121/Mortal-S42-onnx/resolve/2f7ad48bc29dadd9805b1897f3c5b97b76c0d41a/model.onnx"], check=True)
        if model_sha256(temp_path) != expected_sha:
            raise RuntimeError("模型权重 SHA-256 校验失败")
        subprocess.run([sys.executable, "-c", "import sys; from server.engine.onnx_mortal import OnnxMortal; OnnxMortal(sys.argv[1])", temp_path], check=True)
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
    print(" 🎉 云端 AI 引擎正在启动！")
    print(f" 👉 您的云端 API 服务地址为:  {tunnel_url}")
    print("\n 🔗 点击直接进入复盘网页 (已自动连接):")
    print(f"    👉 {web_url}")
    print("=" * 65 + "\n")
else:
    print("⚠️ 隧道地址解析超时，请检查日志。")

# 7. Start backend service
subprocess.run([sys.executable, "run.py", "--no-browser"])
