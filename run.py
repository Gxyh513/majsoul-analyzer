"""Entry point to launch the NAGA-style Mahjong Soul Paipu Analyzer web server."""
from __future__ import annotations

import argparse
import sys
import webbrowser
from threading import Timer

import uvicorn


def main():
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
            sys.stderr.reconfigure(encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass

    import os

    default_host = os.environ.get("HOST", "127.0.0.1")
    default_port = int(os.environ.get("PORT", "8000"))

    parser = argparse.ArgumentParser(description="雀魂牌谱 AI 检讨系统 (NAGA / Mortal 风格)")
    parser.add_argument("--host", default=default_host, help=f"监听地址 (默认 {default_host})")
    parser.add_argument("--port", type=int, default=default_port, help=f"监听端口 (默认 {default_port})")
    parser.add_argument("--no-browser", action="store_true", help="启动时不自动打开浏览器")
    args = parser.parse_args()

    url = f"http://{args.host}:{args.port}"
    print("=" * 68)
    print(" [Majsoul Analyzer] 雀魂牌谱 AI 检讨系统 (NAGA / Mortal 风格) 已启动")
    print(f" [Web UI] 浏览器访问地址: {url}")
    print(" [Engine] Mortal S42 ONNX 权重文件: data/models/model.onnx（需要通过 SHA-256 校验）")
    print("=" * 68)

    if not args.no_browser:
        Timer(1.2, lambda: webbrowser.open(url)).start()

    uvicorn.run("server.app:app", host=args.host, port=args.port, reload=False)


if __name__ == "__main__":
    main()
