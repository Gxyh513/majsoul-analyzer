import json
import math
import time

from server.paipu_performance import _COUNTS, _SIZES, _TIMINGS, build_report


EXPECTED_TIMINGS = {
    "UUID解析", "雀魂抓取", "原始牌谱获取总耗时", "Protobuf解码", "Protobuf实际解码", "JSON解析", "MJAI转换",
    "AI模型准备耗时", "初始模型准备阶段耗时", "模型下载等待耗时", "模型加载及初始化耗时", "后台模型加载等待耗时",
    "获取已加载模型耗时", "模型锁等待耗时", "AI推理排队等待", "AI推理许可获得时间", "AI检讨流程耗时",
    "ONNX实际推理耗时", "完成结果序列化耗时", "complete压缩耗时", "complete Base64编码耗时", "100%事件生成时间",
    "complete事件生成时间", "100%至complete事件生成间隔", "ASGI请求至100%事件生成", "ASGI请求至complete事件生成",
    "complete ASGI发送开始时间", "complete ASGI发送完成时间", "complete ASGI发送等待耗时",
    "结果返回", "本地记录检查", "本地文件读取", "并发任务等待", "文件锁等待", "雀魂连接准备", "雀魂首页请求耗时",
    "version.json请求耗时", "雀魂routes线路请求耗时", "Protobuf协议本地缓存读取耗时", "Protobuf JSON解析及协议初始化耗时",
    "协议资源索引请求耗时", "协议文件下载耗时", "WebSocket连接", "雀魂线路握手", "雀魂登录", "fetchGameRecord请求",
    "雀魂牌谱请求", "外部数据下载", "原始牌谱保存",
}
EXPECTED_SIZES = {
    "原始数据大小": "原始数据大小",
    "MJAI数据大小": "MJAI数据大小",
    "原始complete事件大小": "原始complete事件字节数",
    "原始检讨JSON大小": "原始检讨JSON字节数",
    "gzip压缩后大小": "gzip压缩后字节数",
    "Base64编码后大小": "Base64编码后字节数",
    "complete传输大小": "complete传输字节数",
}
EXPECTED_COUNTS = {
    "AI模型加载次数", "AI模型复用次数", "ONNX推理调用次数", "雀魂连接准备失败次数", "WebSocket连接失败次数",
    "雀魂线路握手失败次数", "雀魂登录失败次数", "雀魂牌谱请求失败次数", "外部数据下载失败次数",
}
EXPECTED_STATUS_OPTIONS = {
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
EXPECTED_STATUS_KEYS = set(EXPECTED_STATUS_OPTIONS) | {
    "牌谱获取HTTP状态码", "是否命中本地记录", "是否等待文件锁", "最后失败阶段", "是否使用Base64",
}
EXPECTED_TOP_LEVEL = {
    "事件", "schema版本", "请求ID", "总耗时_ms", "耗时_ms", "数据大小_KB", "计数", "状态",
    "压缩比例", "当前进行中阶段", "计时口径",
}


def assert_all_json_keys_are_chinese(value):
    if isinstance(value, dict):
        for key, child in value.items():
            assert isinstance(key, str)
            assert any("\u4e00" <= char <= "\u9fff" for char in key), key
            assert_all_json_keys_are_chinese(child)
    elif isinstance(value, list):
        for child in value:
            assert_all_json_keys_are_chinese(child)


def test_report_preserves_all_metrics_and_scopes_without_summing_stages():
    assert len(_TIMINGS) == len(EXPECTED_TIMINGS)
    assert set(_TIMINGS.values()) == EXPECTED_TIMINGS
    assert _SIZES == EXPECTED_SIZES
    assert len(_COUNTS) == len(EXPECTED_COUNTS)
    assert set(_COUNTS.values()) == EXPECTED_COUNTS

    stats = {stage: index + 0.125 for index, stage in enumerate(sorted(EXPECTED_TIMINGS))}
    stats.update({
        "原始数据大小": 128, "MJAI数据大小": 1049, "原始complete事件字节数": 2501,
        "原始检讨JSON字节数": 2003, "gzip压缩后字节数": 1003,
        "Base64编码后字节数": 7000, "complete传输字节数": 1029,
    })
    stats.update({stage: index for index, stage in enumerate(sorted(EXPECTED_COUNTS))})
    stats.update({
        "request_id": "a" * 32, "mode": "按需分析", "牌谱获取方式": "雀魂下载", "牌谱获取结果": "成功",
        "雀魂连接结果": "成功", "complete压缩状态": "成功", "complete ASGI发送结果": "完成",
        "是否命中本地记录": False, "是否等待文件锁": True, "最近雀魂失败类型": "网络失败",
        "最后失败阶段": "雀魂登录", "是否使用Base64": True,
    })
    started = time.perf_counter()
    report = build_report(stats, started)

    assert set(report) == EXPECTED_TOP_LEVEL
    assert report["事件"] == "牌谱性能统计"
    assert report["schema版本"] == 2
    assert report["请求ID"] == "a" * 32
    assert report["总耗时_ms"] >= 0
    assert set(report["耗时_ms"]) == EXPECTED_TIMINGS | {"雀魂下载总耗时"}
    for stage in EXPECTED_TIMINGS - {"complete Base64编码耗时"}:
        assert report["耗时_ms"][stage] == stats[stage]
    assert report["耗时_ms"]["complete Base64编码耗时"] is None
    assert report["耗时_ms"]["雀魂下载总耗时"] == stats["雀魂抓取"]
    assert report["耗时_ms"]["原始牌谱获取总耗时"] == stats["原始牌谱获取总耗时"]

    assert report["数据大小_KB"] == {
        "原始数据大小": 0.13, "MJAI数据大小": 1.02, "原始complete事件大小": 2.44,
        "原始检讨JSON大小": 1.96, "gzip压缩后大小": 0.98,
        "Base64编码后大小": None, "complete传输大小": 1.0,
    }
    assert all(type(value) is float for value in report["数据大小_KB"].values() if value is not None)
    assert report["计数"] == {stage: stats[stage] for stage in EXPECTED_COUNTS}
    assert all(type(value) is int for value in report["计数"].values())
    assert report["状态"] == {
        "模式": "按需分析", "牌谱获取方式": "雀魂下载", "牌谱获取结果": "成功", "雀魂连接结果": "成功",
        "complete压缩状态": "成功", "最近雀魂失败类型": "网络失败", "complete ASGI发送结果": "完成",
        "牌谱获取HTTP状态码": None, "是否命中本地记录": False, "是否等待文件锁": True,
        "最后失败阶段": "雀魂登录", "是否使用Base64": False,
    }
    assert report["压缩比例"] == {
        "complete压缩比例": 1003 / 2501,
        "gzip与原始检讨JSON比例": 1003 / 2003,
    }
    assert report["当前进行中阶段"] == []
    assert set(report["计时口径"]) >= EXPECTED_TIMINGS | {"总耗时_ms", "雀魂下载总耗时"}
    for explanation in report["计时口径"].values():
        assert isinstance(explanation, str) and any("\u4e00" <= char <= "\u9fff" for char in explanation)
    assert "不应相加" in report["计时口径"]["雀魂连接准备"]
    assert "不代表客户端已收到" in report["计时口径"]["complete ASGI发送完成时间"]
    assert "同步刷新" in report["计时口径"]["complete传输大小"]
    assert "中断时仅计已成功交给ASGI的部分" in report["计时口径"]["complete传输大小"]
    assert "中断时仅计已成功交给ASGI的部分" in report["计时口径"]["gzip压缩后大小"]
    assert "不含ASGI发送等待" in report["计时口径"]["结果返回"]
    assert_all_json_keys_are_chinese(report)
    assert json.loads(json.dumps(report, ensure_ascii=False, allow_nan=False)) == report


def test_all_missing_metrics_are_null_except_known_booleans_and_empty_stage_list():
    report = build_report({}, time.perf_counter())
    assert report["请求ID"] is None
    assert all(value is None for value in report["耗时_ms"].values())
    assert all(value is None for value in report["数据大小_KB"].values())
    assert all(value is None for value in report["计数"].values())
    assert set(report["状态"]) == EXPECTED_STATUS_KEYS
    assert all(value is None for key, value in report["状态"].items() if key != "是否使用Base64")
    assert report["状态"]["是否使用Base64"] is False
    assert report["压缩比例"] == {"complete压缩比例": None, "gzip与原始检讨JSON比例": None}
    assert report["当前进行中阶段"] == []
    assert math.isfinite(report["总耗时_ms"])


def test_status_values_are_chinese_allowlisted_and_failed_stages_keep_original_names():
    for output_name, (source_name, options) in EXPECTED_STATUS_OPTIONS.items():
        for option in options:
            report = build_report({source_name: option}, time.perf_counter())
            assert report["状态"][output_name] == option
        report = build_report({source_name: '账号="secret"'}, time.perf_counter())
        assert report["状态"][output_name] is None

    for stage in EXPECTED_TIMINGS:
        report = build_report({"最后失败阶段": stage}, time.perf_counter())
        assert report["状态"]["最后失败阶段"] == stage

    for code in (100, 403, 404, 500, 599):
        report = build_report({"牌谱获取结果": f"失败(HTTP{code})"}, time.perf_counter())
        assert report["状态"]["牌谱获取结果"] == "HTTP错误"
        assert report["状态"]["牌谱获取HTTP状态码"] == code
        assert type(report["状态"]["牌谱获取HTTP状态码"]) is int
    for invalid in ("失败(HTTP099)", "失败(HTTP600)", "失败(HTTP403)secret"):
        report = build_report({"牌谱获取结果": invalid}, time.perf_counter())
        assert report["状态"]["牌谱获取结果"] is None
        assert report["状态"]["牌谱获取HTTP状态码"] is None


def test_asgi_and_http_gzip_values_use_the_same_raw_frame_scope():
    stats = {
        "原始complete事件字节数": 2050, "原始检讨JSON字节数": 2000,
        "gzip压缩后字节数": 480, "complete传输字节数": 480,
        "complete压缩耗时": 2.75, "结果返回": 5.25,
        "complete ASGI发送开始时间": 80.5, "complete ASGI发送完成时间": 110.75,
        "complete ASGI发送等待耗时": 30.25, "complete ASGI发送结果": "完成",
        "complete压缩状态": "成功", "complete Base64编码耗时": 9.0, "Base64编码后字节数": 600,
    }
    report = build_report(stats, time.perf_counter())
    assert report["耗时_ms"]["complete ASGI发送开始时间"] == 80.5
    assert report["耗时_ms"]["complete ASGI发送完成时间"] == 110.75
    assert report["耗时_ms"]["complete ASGI发送等待耗时"] == 30.25
    assert report["耗时_ms"]["complete压缩耗时"] == 2.75
    assert report["耗时_ms"]["结果返回"] == 5.25
    assert report["耗时_ms"]["complete Base64编码耗时"] is None
    assert report["数据大小_KB"]["原始complete事件大小"] == 2.0
    assert report["数据大小_KB"]["gzip压缩后大小"] == 0.47
    assert report["数据大小_KB"]["complete传输大小"] == 0.47
    assert report["数据大小_KB"]["Base64编码后大小"] is None
    assert report["压缩比例"]["complete压缩比例"] == 480 / 2050
    assert report["压缩比例"]["gzip与原始检讨JSON比例"] == 480 / 2000
    assert report["状态"]["是否使用Base64"] is False
    assert report["状态"]["complete ASGI发送结果"] == "完成"

    uncompressed = build_report({
        "原始complete事件字节数": 2050, "complete传输字节数": 2050,
        "complete压缩状态": "未启用", "complete ASGI发送结果": "中断",
    }, time.perf_counter())
    assert uncompressed["数据大小_KB"]["complete传输大小"] == 2.0
    assert uncompressed["数据大小_KB"]["gzip压缩后大小"] is None
    assert uncompressed["耗时_ms"]["complete压缩耗时"] is None
    assert uncompressed["状态"]["complete压缩状态"] == "未启用"
    assert uncompressed["状态"]["complete ASGI发送结果"] == "中断"
    assert all(value is None for value in uncompressed["压缩比例"].values())

    interrupted = build_report({
        "complete压缩状态": "中断", "complete ASGI发送结果": "中断",
        "原始complete事件字节数": 2048, "gzip压缩后字节数": 512, "complete传输字节数": 512,
    }, time.perf_counter())
    assert interrupted["状态"]["complete压缩状态"] == "中断"
    assert interrupted["状态"]["complete ASGI发送结果"] == "中断"
    assert interrupted["数据大小_KB"]["gzip压缩后大小"] == 0.5
    assert interrupted["数据大小_KB"]["complete传输大小"] == 0.5
    assert interrupted["压缩比例"]["complete压缩比例"] == 0.25


def test_running_stages_preserve_accumulated_call_timing():
    started = time.perf_counter() - 2.0
    report = build_report({
        "_active_stages": {
            "雀魂登录": [started, started + 1.0],
            "complete Base64编码耗时": [started],
            "未知的私有阶段": [started],
        },
        "雀魂登录": 12.5,
        "牌谱获取结果": "失败(HTTP403)",
    }, started)
    assert math.isclose(report["耗时_ms"]["雀魂登录"], 2 * report["总耗时_ms"] - 1000 + 12.5, abs_tol=1e-9)
    assert report["耗时_ms"]["complete Base64编码耗时"] is None
    assert set(report["当前进行中阶段"]) == {"雀魂登录", "complete Base64编码耗时"}
    assert report["状态"]["牌谱获取结果"] == "HTTP错误"
    assert report["状态"]["牌谱获取HTTP状态码"] == 403


def test_untrusted_text_and_invalid_numbers_never_leak_or_break_json():
    secret = 'user@example.com "秘密"\\token\n'
    stats = {
        "request_id": secret, "mode": secret, "牌谱获取方式": secret, "牌谱获取结果": secret,
        "最近雀魂失败类型": secret, "最后失败阶段": secret, "complete ASGI发送结果": secret,
        "complete压缩比例": secret, "extra": {"credential": secret},
        "原始数据大小": 2**53 - 1, "ONNX推理调用次数": 2**53 - 1,
        "雀魂抓取": float("nan"), "MJAI转换": float("inf"), "JSON解析": True,
        "原始检讨JSON字节数": True, "gzip压缩后字节数": 10**400,
        "AI模型加载次数": False, "AI模型复用次数": -1,
        "是否命中本地记录": 1, "是否等待文件锁": secret,
        "_active_stages": {
            secret: [0.0], "雀魂登录": [secret, True, float("nan"), float("inf"), time.perf_counter() + 60],
        },
    }
    report = build_report(stats, float("nan"))
    encoded = json.dumps(report, ensure_ascii=False, allow_nan=False)
    parsed = json.loads(encoded)
    assert secret not in encoded
    assert all(part not in encoded for part in ("user@example.com", "秘密", "token"))
    assert parsed["请求ID"] is None
    assert parsed["总耗时_ms"] is None
    assert parsed["状态"]["模式"] is None
    assert parsed["状态"]["牌谱获取方式"] is None
    assert parsed["状态"]["牌谱获取结果"] is None
    assert parsed["状态"]["最近雀魂失败类型"] is None
    assert parsed["状态"]["最后失败阶段"] is None
    assert parsed["状态"]["complete ASGI发送结果"] is None
    assert parsed["状态"]["是否命中本地记录"] is None
    assert parsed["状态"]["是否等待文件锁"] is None
    assert parsed["耗时_ms"]["雀魂抓取"] is None
    assert parsed["耗时_ms"]["MJAI转换"] is None
    assert parsed["耗时_ms"]["JSON解析"] is None
    assert parsed["数据大小_KB"]["原始数据大小"] == 8796093022208.0
    assert parsed["数据大小_KB"]["原始检讨JSON大小"] is None
    assert parsed["数据大小_KB"]["gzip压缩后大小"] is None
    assert parsed["计数"]["ONNX推理调用次数"] == 2**53 - 1
    assert parsed["计数"]["AI模型加载次数"] is None
    assert parsed["计数"]["AI模型复用次数"] is None
    assert parsed["当前进行中阶段"] == []
    assert all(value is None for value in parsed["压缩比例"].values())


def test_zero_denominators_and_malformed_active_stages_are_safe():
    report = build_report({
        "原始complete事件字节数": 0, "原始检讨JSON字节数": 0, "gzip压缩后字节数": 0,
        "_active_stages": "not a stage dictionary",
    }, time.perf_counter())
    assert report["数据大小_KB"]["原始complete事件大小"] == 0.0
    assert report["数据大小_KB"]["gzip压缩后大小"] == 0.0
    assert report["压缩比例"] == {"complete压缩比例": None, "gzip与原始检讨JSON比例": None}
    assert report["当前进行中阶段"] == []
    json.dumps(report, ensure_ascii=False, allow_nan=False)
