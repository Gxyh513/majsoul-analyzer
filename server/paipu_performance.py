"""Request-scoped performance measurements for paipu processing."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
import math
import re
import threading
import time
from typing import Any


performance: ContextVar[dict[str, Any] | None] = ContextVar("paipu_performance", default=None)
review_inference: ContextVar[bool] = ContextVar("paipu_review_inference", default=False)


def _stats_lock(stats: dict[str, Any]):
    lock = stats.get("_lock")
    if lock is None:
        lock = stats.setdefault("_lock", threading.Lock())
    return lock


def set_value(name: str, value: Any) -> None:
    stats = performance.get()
    if stats is not None:
        with _stats_lock(stats):
            stats[name] = value


def get_value(name: str) -> Any:
    stats = performance.get()
    if stats is not None:
        with _stats_lock(stats):
            return stats.get(name)
    return None


def increment(name: str) -> None:
    stats = performance.get()
    if stats is not None:
        with _stats_lock(stats):
            stats[name] = stats.get(name, 0) + 1


def add_elapsed(name: str, started: float) -> None:
    stats = performance.get()
    if stats is not None:
        elapsed = (time.perf_counter() - started) * 1000
        with _stats_lock(stats):
            stats[name] = stats.get(name, 0.0) + elapsed


def snapshot(stats: dict[str, Any]) -> tuple[dict[str, Any], float]:
    with _stats_lock(stats):
        now = time.perf_counter()
        result = stats.copy()
        result["_active_stages"] = {
            stage: tuple(starts) for stage, starts in stats.get("_active_stages", {}).items()
        }
    return result, now


_TIMINGS = {
    "uuid_parse": "UUID解析",
    "majsoul_fetch": "雀魂抓取",
    "record_acquisition_total": "原始牌谱获取总耗时",
    "protobuf_decode": "Protobuf解码",
    "protobuf_decode_actual": "Protobuf实际解码",
    "json_parse": "JSON解析",
    "mjai_convert": "MJAI转换",
    "model_prepare": "AI模型准备耗时",
    "initial_model_prepare": "初始模型准备阶段耗时",
    "model_download_wait": "模型下载等待耗时",
    "model_load_initialization": "模型加载及初始化耗时",
    "background_model_load_wait": "后台模型加载等待耗时",
    "loaded_model_get": "获取已加载模型耗时",
    "model_lock_wait": "模型锁等待耗时",
    "inference_queue_wait": "AI推理排队等待",
    "inference_permit_acquired": "AI推理许可获得时间",
    "review_total": "AI检讨流程耗时",
    "onnx_inference": "ONNX实际推理耗时",
    "serialization": "完成结果序列化耗时",
    "compression": "complete压缩耗时",
    "base64_encoding": "complete Base64编码耗时",
    "progress_100_generated": "100%事件生成时间",
    "complete_generated": "complete事件生成时间",
    "progress_to_complete_generated": "100%至complete事件生成间隔",
    "asgi_to_progress_100_generated": "ASGI请求至100%事件生成",
    "asgi_to_complete_generated": "ASGI请求至complete事件生成",
    "result_return": "结果返回",
    "local_record_check": "本地记录检查",
    "local_file_read": "本地文件读取",
    "concurrent_task_wait": "并发任务等待",
    "file_lock_wait": "文件锁等待",
    "majsoul_connection_prepare": "雀魂连接准备",
    "majsoul_homepage_request": "雀魂首页请求耗时",
    "majsoul_version_request": "version.json请求耗时",
    "majsoul_routes_request": "雀魂routes线路请求耗时",
    "protobuf_local_cache_read": "Protobuf协议本地缓存读取耗时",
    "protobuf_schema_initialize": "Protobuf JSON解析及协议初始化耗时",
    "protobuf_resource_index_request": "协议资源索引请求耗时",
    "protobuf_schema_download": "协议文件下载耗时",
    "websocket_connect": "WebSocket连接",
    "majsoul_route_handshake": "雀魂线路握手",
    "majsoul_login": "雀魂登录",
    "fetch_game_record_request": "fetchGameRecord请求",
    "majsoul_record_request": "雀魂牌谱请求",
    "external_data_download": "外部数据下载",
    "raw_record_save": "原始牌谱保存",
}
_SIZES = {
    "raw_record": "原始数据大小",
    "mjai": "MJAI数据大小",
    "raw_complete_event": "原始complete事件字节数",
    "original_json": "原始检讨JSON字节数",
    "gzip": "gzip压缩后字节数",
    "base64": "Base64编码后字节数",
    "transmitted": "complete传输字节数",
}
_COUNTS = {
    "model_load": "AI模型加载次数",
    "model_reuse": "AI模型复用次数",
    "onnx_calls": "ONNX推理调用次数",
    "majsoul_connection_prepare_failures": "雀魂连接准备失败次数",
    "websocket_failures": "WebSocket连接失败次数",
    "route_handshake_failures": "雀魂线路握手失败次数",
    "login_failures": "雀魂登录失败次数",
    "paipu_request_failures": "雀魂牌谱请求失败次数",
    "external_data_download_failures": "外部数据下载失败次数",
}
_STATUS_VALUES = {
    "mode": ("mode", {"准备中": "preparing", "按需分析": "on_demand"}),
    "record_source": ("牌谱获取方式", {
        "本地直接读取": "local", "下载任务": "download_task",
        "进程内锁后读取本地": "local_after_process_lock", "文件锁后读取本地": "local_after_file_lock",
        "雀魂下载": "majsoul_download", "等待并发任务": "concurrent_task",
    }),
    "record_result": ("牌谱获取结果", {
        "进行中": "in_progress", "已取消": "cancelled", "失败": "failure", "成功": "success",
    }),
    "connection_result": ("雀魂连接结果", {"进行中": "in_progress", "失败": "failure", "成功": "success"}),
    "compression": ("complete压缩状态", {
        "未协商": "not_negotiated", "数据较小": "below_threshold", "成功": "success",
        "压缩后未变小，回退原始": "not_smaller_fallback", "压缩失败，回退原始": "failed_fallback",
    }),
    "last_majsoul_failure": ("最近雀魂失败类型", {
        "未配置账号": "no_account", "抓取限流": "rate_limited", "连接准备或网络失败": "connection_or_network",
        "网络失败": "network", "登录失败": "login", "牌谱不存在或无权访问": "not_found_or_forbidden",
        "登录失效": "authentication", "雀魂接口失败": "majsoul_api",
    }),
}


def build_report(stats: dict[str, Any], started: float) -> dict[str, Any]:
    """Build an allowlisted, request-scoped report without credentials or raw data."""
    values, now = snapshot(stats)
    active = values.get("_active_stages", {})
    timings = {}
    for name, stage in _TIMINGS.items():
        value = values.get(stage)
        running = active.get(stage, ())
        if type(value) not in (int, float) or (type(value) is float and not math.isfinite(value)):
            value = 0.0 if running else None
        if running and value is not None:
            value += sum((now - start) * 1000 for start in running)
        timings[name] = value
    timings["majsoul_download_total"] = timings["majsoul_fetch"]
    timings["total"] = (now - started) * 1000
    sizes = {name: value if type(value := values.get(stage)) is int else None for name, stage in _SIZES.items()}
    counts = {name: value if type(value := values.get(stage)) is int else None for name, stage in _COUNTS.items()}
    status = {name: options.get(value) if isinstance(value := values.get(stage), str) else None
              for name, (stage, options) in _STATUS_VALUES.items()}
    record_result = values.get("牌谱获取结果")
    http_error = re.fullmatch(r"失败\(HTTP([1-5][0-9]{2})\)", record_result) if isinstance(record_result, str) else None
    if http_error:
        status["record_result"] = "http_error"
    status["record_http_status"] = int(http_error.group(1)) if http_error else None
    for name, stage in (("local_record_hit", "是否命中本地记录"), ("waited_for_file_lock", "是否等待文件锁")):
        value = values.get(stage)
        status[name] = value if type(value) is bool else None
    status["last_failed_stage"] = next((name for name, stage in _TIMINGS.items()
                                         if stage == values.get("最后失败阶段")), None)
    raw = sizes["original_json"]
    compressed = sizes["gzip"]
    ratio = compressed / raw if raw and compressed is not None else None
    request_id = values.get("request_id")
    if not isinstance(request_id, str) or not re.fullmatch(r"[0-9a-f]{32}", request_id):
        request_id = None
    return {
        "event": "paipu_performance",
        "schema_version": 1,
        "request_id": request_id,
        "timings_ms": timings,
        "sizes_bytes": sizes,
        "counts": counts,
        "status": status,
        "ratios": {"gzip_to_original": ratio},
        "active_stages": [name for name, stage in _TIMINGS.items() if active.get(stage)],
        "measurement_scopes": {
            "majsoul_fetch": "account selection through fetch completion, including connection and login; also called majsoul download total",
            "record_acquisition_total": "local check through record acquisition or interruption",
            "majsoul_connection_prepare": "entire prepare call; sub-stages overlap and must not be summed",
            "majsoul_routes_request": "HTTP request and response read, accumulated across route attempts",
            "protobuf_schema_initialize": "JSON parsing and protocol initialization, excluding file read and download",
            "fetch_game_record_request": "WebSocket send and response wait",
            "majsoul_record_request": "fetchGameRecord call including response decoding",
            "model_prepare": "download-status wait and accumulated model access; may overlap review_total",
            "initial_model_prepare": "streaming request model download wait and first access, including thread scheduling",
            "model_download_wait": "download status wait including background validation, loading and polling",
            "background_model_load_wait": "overlap of request wait and background initialization, included in model_download_wait",
            "loaded_model_get": "accumulated model reuse calls, including model lock wait",
            "review_total": "entire review_game call including preprocessing, postprocessing and model access",
            "onnx_inference": "session.run calls included in review_total, excluding initialization warmup",
            "serialization": "public result filtering and review JSON serialization, excluding compression",
            "result_return": "serialization and optional gzip and base64 encoding",
            "progress_to_complete_generated": "server event generation interval including send wait",
            "generator_timebase": "inference_permit_acquired, progress_100_generated and complete_generated are relative to stream generator start",
            "asgi_timebase": "asgi_to_progress_100_generated and asgi_to_complete_generated are relative to ASGI request arrival",
            "gzip_to_original": "gzip bytes divided by original review JSON bytes",
        },
    }


@contextmanager
def measure(stage: str, *, missing_stage: str | None = None, failure_count: str | None = None):
    stats = performance.get()
    if stats is None:
        yield
        return
    lock = _stats_lock(stats)
    with lock:
        started = time.perf_counter()
        active = stats.setdefault("_active_stages", {})
        active.setdefault(stage, []).append(started)
        if failure_count is not None:
            stats.setdefault(failure_count, 0)
    recorded_stage = stage
    failed = False
    try:
        yield
    except FileNotFoundError:
        if missing_stage is not None:
            recorded_stage = missing_stage
        else:
            failed = True
        raise
    except Exception:
        failed = True
        raise
    finally:
        finished = time.perf_counter()
        with lock:
            stats[recorded_stage] = stats.get(recorded_stage, 0.0) + (finished - started) * 1000
            if failed and failure_count is not None:
                stats[failure_count] += 1
                stats["最后失败阶段"] = stage
            active[stage].remove(started)
            if not active[stage]:
                del active[stage]
