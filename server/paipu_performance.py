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
        active = stats.get("_active_stages")
        result["_active_stages"] = {
            stage: tuple(starts) for stage, starts in active.items()
            if type(stage) is str and type(starts) in (list, tuple)
        } if type(active) is dict else {}
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
    "complete_asgi_send_start": "complete ASGI发送开始时间",
    "complete_asgi_send_end": "complete ASGI发送完成时间",
    "complete_asgi_send_wait": "complete ASGI发送等待耗时",
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
# 输出中的大小名称不再使用“字节数”，其数值统一为 KB。
_SIZES = {
    "原始数据大小": "原始数据大小",
    "MJAI数据大小": "MJAI数据大小",
    "原始complete事件大小": "原始complete事件字节数",
    "原始检讨JSON大小": "原始检讨JSON字节数",
    "gzip压缩后大小": "gzip压缩后字节数",
    "Base64编码后大小": "Base64编码后字节数",
    "complete传输大小": "complete传输字节数",
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
    "模式": ("mode", {"准备中", "按需分析"}),
    "牌谱获取方式": ("牌谱获取方式", {
        "本地直接读取", "下载任务", "进程内锁后读取本地", "文件锁后读取本地", "雀魂下载", "等待并发任务",
    }),
    "牌谱获取结果": ("牌谱获取结果", {"进行中", "已取消", "失败", "成功"}),
    "雀魂连接结果": ("雀魂连接结果", {"进行中", "失败", "成功"}),
    "complete压缩状态": ("complete压缩状态", {
        "未协商", "数据较小", "成功", "压缩后未变小，回退原始", "压缩失败，回退原始",
        "未启用", "未压缩", "已压缩", "进行中", "失败", "中断",
    }),
    "最近雀魂失败类型": ("最近雀魂失败类型", {
        "未配置账号", "抓取限流", "连接准备或网络失败", "网络失败", "登录失败",
        "牌谱不存在或无权访问", "登录失效", "雀魂接口失败",
    }),
    "complete ASGI发送结果": ("complete ASGI发送结果", {"完成", "中断"}),
}

# 各阶段可能相互包含；这些口径不是可相加的耗时分解。
_MEASUREMENT_SCOPES = {
    "总耗时_ms": "从本次分析或流生成器启动至报告快照的实际经过时间；不由各阶段相加，也不代表客户端收到数据。",
    "UUID解析": "解析输入中的牌谱标识所用时间。",
    "雀魂抓取": "从选择账号至抓取完成，包含连接和登录；与雀魂下载总耗时是同一指标。",
    "雀魂下载总耗时": "雀魂抓取的别名，不是额外耗时。",
    "原始牌谱获取总耗时": "从本地检查开始至获取原始牌谱或中断，包含其内部等待、下载及保存。",
    "Protobuf解码": "原始牌谱解码调用的整体耗时，包含其中的实际解码。",
    "Protobuf实际解码": "实际的Protobuf封包解析和消息解码调用累计耗时。",
    "JSON解析": "牌谱及相关输入的JSON解析累计耗时。",
    "MJAI转换": "原始牌谱转换为MJAI事件及元数据的耗时。",
    "AI模型准备耗时": "模型下载状态等待及模型访问的累计耗时；可能与AI检讨流程耗时重叠。",
    "初始模型准备阶段耗时": "流式请求等待模型下载并首次访问模型的阶段，包含线程调度。",
    "模型下载等待耗时": "等待模型下载状态，包含后台校验、加载和轮询。",
    "模型加载及初始化耗时": "本请求记录的模型加载和初始化耗时。",
    "后台模型加载等待耗时": "请求等待与后台模型初始化的重叠时段，已包含在模型下载等待耗时中。",
    "获取已加载模型耗时": "获取已加载模型的累计耗时，包含模型锁等待。",
    "模型锁等待耗时": "获取模型锁的累计等待时间。",
    "AI推理排队等待": "等待获取AI推理许可的时间。",
    "AI推理许可获得时间": "获得推理许可的时刻，相对于流生成器或本次非流式分析启动。",
    "AI检讨流程耗时": "整个检讨调用的耗时，包含预处理、后处理和模型访问。",
    "ONNX实际推理耗时": "检讨中ONNX的session.run调用累计耗时，包含在AI检讨流程中，不含初始化预热。",
    "完成结果序列化耗时": "公开结果过滤和检讨JSON序列化的耗时，不含压缩。",
    "complete压缩耗时": "HTTP gzip对完整SSE complete帧执行同步压缩的活动耗时，不含ASGI发送等待；未启用gzip时为null。旧路径压缩的是检讨JSON。",
    "complete Base64编码耗时": "为兼容保留的旧指标；新方案不使用Base64，固定为null，而非零耗时。",
    "100%事件生成时间": "100%进度事件生成的时刻，相对于流生成器启动。",
    "complete事件生成时间": "complete事件生成的时刻，相对于流生成器启动。",
    "100%至complete事件生成间隔": "两个服务端事件生成时刻的间隔，包含其间的发送等待。",
    "ASGI请求至100%事件生成": "从ASGI请求到达到100%进度事件生成的时间。",
    "ASGI请求至complete事件生成": "从ASGI请求到达到complete事件生成的时间。",
    "complete ASGI发送开始时间": "complete开始交给ASGI发送的时刻，距ASGI请求到达的毫秒数。",
    "complete ASGI发送完成时间": "complete的ASGI发送调用结束的时刻，距ASGI请求到达的毫秒数，不代表客户端已收到；是否中断见发送结果。",
    "complete ASGI发送等待耗时": "complete的ASGI发送调用开始至结束的等待耗时，包括中断时已等待的时间，不代表客户端接收耗时。",
    "结果返回": "新方案中为结果序列化与complete压缩的活动耗时之和，不含ASGI发送等待；旧路径还可能包含Base64编码。",
    "本地记录检查": "检查本地牌谱记录的累计耗时，包含未找到文件的检查。",
    "本地文件读取": "成功读取本地牌谱文件的累计耗时。",
    "并发任务等待": "等待已有并发牌谱下载任务的时间。",
    "文件锁等待": "获取跨进程文件锁的等待时间。",
    "雀魂连接准备": "连接准备调用的整体耗时；其中子阶段相互包含，不应相加。",
    "雀魂首页请求耗时": "雀魂首页请求耗时；未采集时为null。",
    "version.json请求耗时": "版本文件请求耗时；未采集时为null。",
    "雀魂routes线路请求耗时": "线路HTTP请求及响应读取的耗时，多次尝试时累计。",
    "Protobuf协议本地缓存读取耗时": "读取本地Protobuf协议缓存文件的耗时。",
    "Protobuf JSON解析及协议初始化耗时": "协议JSON解析及协议初始化，不含文件读取和下载。",
    "协议资源索引请求耗时": "协议资源索引请求耗时；未采集时为null。",
    "协议文件下载耗时": "协议文件下载耗时；未采集时为null。",
    "WebSocket连接": "建立WebSocket连接的累计耗时。",
    "雀魂线路握手": "雀魂线路握手请求与等待响应的耗时。",
    "雀魂登录": "雀魂登录尝试的累计耗时。",
    "fetchGameRecord请求": "牌谱请求的WebSocket发送和等待响应，不含响应解码。",
    "雀魂牌谱请求": "fetchGameRecord调用的整体耗时，包含响应解码。",
    "外部数据下载": "从外部地址下载牌谱数据的耗时。",
    "原始牌谱保存": "原始牌谱写入及持久化的耗时。",
    "流生成器时间基准": "AI推理许可获得时间、100%事件生成时间和complete事件生成时间相对于流生成器启动；非流式许可时刻相对于分析启动。",
    "ASGI请求时间基准": "ASGI请求至事件生成及complete ASGI发送开始、完成时间均相对于ASGI请求到达；发送完成不等于客户端收到。",
    "complete压缩比例": "SSE complete帧在共享gzip流中产生的压缩字节数除以原始帧字节数；含同步刷新开销，不含其他帧、TCP及代理开销；中断时分子仅为已成功交给ASGI的部分；旧路径仅压缩JSON时分子分母范围不同。",
    "gzip与原始检讨JSON比例": "保留旧报告口径：gzip压缩后字节数除以原始检讨JSON字节数；新HTTP gzip按完整帧压缩，分子分母范围不同。",
    "压缩比例单位": "比例是无量纲数值，1表示100%；缺少字节数或分母为零时为null。",
    "complete传输大小": "无gzip时为原始SSE complete帧大小；启用gzip时为该帧在共享gzip流中产生的字节数，含同步刷新开销，不含其他帧、TCP及代理开销；完成前中断时仅计已成功交给ASGI的部分。",
    "gzip压缩后大小": "启用gzip时与complete传输大小的采集范围一致；中断时仅计已成功交给ASGI的部分；未启用gzip时为null。",
    "大小单位": "大小以1024字节为1 KB，四舍五入到小数点后两位；JSON数值可能省略末尾的零。",
    "重叠时段": "不同阶段可能重叠，不可相加推算总耗时；同一阶段的多次调用按原有口径分别累计。"
}


def _finite_number(value: Any) -> int | float | None:
    if type(value) not in (int, float):
        return None
    try:
        return value if math.isfinite(value) else None
    except OverflowError:
        return None


def _nonnegative_integer(value: Any) -> int | None:
    # 过大的整数既不可能是实际计数，也可能使JSON编码或浮点转换失败。
    if type(value) is int and value >= 0 and value.bit_length() <= 1023:
        return value
    return None


def _kilobytes(value: Any) -> float | None:
    value = _nonnegative_integer(value)
    if value is None:
        return None
    # 整数运算实现两位小数的四舍五入，避免二进制浮点的边界误差。
    return ((value * 100 + 512) // 1024) / 100


def _compression_ratio(values: dict[str, Any], original_stage: str) -> float | None:
    original = _nonnegative_integer(values.get(original_stage))
    compressed = _nonnegative_integer(values.get("gzip压缩后字节数"))
    if original and compressed is not None:
        return compressed / original
    return None


def build_report(stats: dict[str, Any], started: float) -> dict[str, Any]:
    """仅输出白名单中的指标，不输出凭据、账号或原始牌谱。"""
    values, now = snapshot(stats)
    active = values["_active_stages"]
    timings = {}
    active_stages = []
    for stage in _TIMINGS.values():
        running = [start for item in active.get(stage, ())
                   if (start := _finite_number(item)) is not None and start <= now]
        if running:
            active_stages.append(stage)
        if stage == "complete Base64编码耗时":
            timings[stage] = None
            continue
        value = _finite_number(values.get(stage))
        if value is not None and value < 0:
            value = None
        if running:
            elapsed = _finite_number(sum((now - start) * 1000 for start in running))
            value = _finite_number((value or 0) + elapsed) if elapsed is not None else None
        timings[stage] = value
    timings["雀魂下载总耗时"] = timings["雀魂抓取"]

    sizes = {name: _kilobytes(values.get(stage)) for name, stage in _SIZES.items()}
    sizes["Base64编码后大小"] = None
    counts = {stage: _nonnegative_integer(values.get(stage)) for stage in _COUNTS.values()}
    status = {
        name: value if type(value := values.get(stage)) is str and value in options else None
        for name, (stage, options) in _STATUS_VALUES.items()
    }
    record_result = values.get("牌谱获取结果")
    http_error = re.fullmatch(r"失败\(HTTP([1-5][0-9]{2})\)", record_result) if type(record_result) is str else None
    if http_error:
        status["牌谱获取结果"] = "HTTP错误"
    status["牌谱获取HTTP状态码"] = int(http_error.group(1)) if http_error else None
    for stage in ("是否命中本地记录", "是否等待文件锁"):
        value = values.get(stage)
        status[stage] = value if type(value) is bool else None
    status["最后失败阶段"] = next((stage for stage in _TIMINGS.values()
                                    if stage == values.get("最后失败阶段")), None)
    status["是否使用Base64"] = False

    request_id = values.get("request_id")
    if type(request_id) is not str or not re.fullmatch(r"[0-9a-f]{32}", request_id):
        request_id = None
    start = _finite_number(started)
    total = _finite_number((now - start) * 1000) if start is not None and start <= now else None
    return {
        "事件": "牌谱性能统计",
        "schema版本": 2,
        "请求ID": request_id,
        "总耗时_ms": total,
        "耗时_ms": timings,
        "数据大小_KB": sizes,
        "计数": counts,
        "状态": status,
        "压缩比例": {
            "complete压缩比例": _compression_ratio(values, "原始complete事件字节数"),
            "gzip与原始检讨JSON比例": _compression_ratio(values, "原始检讨JSON字节数"),
        },
        "当前进行中阶段": active_stages,
        "计时口径": dict(_MEASUREMENT_SCOPES),
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
