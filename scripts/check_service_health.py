"""Read-only alert-ready backend health check, suitable for an external scheduler.

Never prints credentials, network responses, account identities or user JSON.
Exit 0 healthy, 1 unavailable/untrusted, 2 low disk / model unavailable.
"""
from __future__ import annotations

import argparse
import json
import os
from urllib.parse import urlsplit

import httpx


def validate_url(url: str):
    parsed = urlsplit(url)
    if (parsed.username or parsed.password or parsed.query or parsed.fragment
            or parsed.path not in ("", "/") or not parsed.hostname
            or parsed.scheme not in ("http", "https")):
        raise ValueError("只接受无凭据、无附加路径的服务根地址")
    if parsed.scheme == "http" and parsed.hostname not in ("127.0.0.1", "::1", "localhost"):
        raise ValueError("访问远程状态必须使用 HTTPS 保护 API Token")


def inspect_status(body) -> tuple[int, dict]:
    if not isinstance(body, dict) or body.get("ok") is not True:
        return 1, {"status": "unavailable"}
    storage, engine = body.get("storage"), body.get("engine")
    if not isinstance(storage, dict) or not isinstance(engine, dict):
        return 1, {"status": "untrusted_or_missing_metrics"}
    low_disk = storage.get("low_disk_space")
    model_ready = engine.get("model_ready", engine.get("has_mortal_weights"))
    if type(low_disk) is not bool or type(model_ready) is not bool:
        return 1, {"status": "metrics_invalid"}
    warning = low_disk or not model_ready
    return (2 if warning else 0), {
        "status": "warning" if warning else "healthy",
        "low_disk_space": low_disk,
        "model_ready": model_ready,
    }


def check_health(base_url: str, token: str, *, client: httpx.Client | None = None):
    validate_url(base_url)
    try:
        if client is not None:
            response = client.get("/api/status", headers={"X-Api-Token": token} if token else {})
        else:
            with httpx.Client(base_url=base_url, timeout=10, trust_env=False) as http:
                response = http.get("/api/status", headers={"X-Api-Token": token} if token else {})
        if response.status_code != 200:
            return 1, {"status": "unavailable"}
        return inspect_status(response.json())
    except (httpx.HTTPError, ValueError):
        return 1, {"status": "unavailable"}


def main():
    parser = argparse.ArgumentParser(description="只读检查服务模型和磁盘，便于外部定时告警")
    parser.add_argument("--url", default="http://127.0.0.1:7860")
    args = parser.parse_args()
    exit_code, report = check_health(args.url, os.environ.get("MAJSOUL_API_TOKEN", ""))
    print(json.dumps(report, ensure_ascii=False))
    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
