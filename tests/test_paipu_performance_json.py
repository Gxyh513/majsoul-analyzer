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
    "结果返回", "本地记录检查", "本地文件读取", "并发任务等待", "文件锁等待", "雀魂连接准备", "雀魂首页请求耗时",
    "version.json请求耗时", "雀魂routes线路请求耗时", "Protobuf协议本地缓存读取耗时", "Protobuf JSON解析及协议初始化耗时",
    "协议资源索引请求耗时", "协议文件下载耗时", "WebSocket连接", "雀魂线路握手", "雀魂登录", "fetchGameRecord请求",
    "雀魂牌谱请求", "外部数据下载", "原始牌谱保存",
}
EXPECTED_SIZES = {
    "原始数据大小", "MJAI数据大小", "原始complete事件字节数", "原始检讨JSON字节数", "gzip压缩后字节数",
    "Base64编码后字节数", "complete传输字节数",
}
EXPECTED_COUNTS = {
    "AI模型加载次数", "AI模型复用次数", "ONNX推理调用次数", "雀魂连接准备失败次数", "WebSocket连接失败次数",
    "雀魂线路握手失败次数", "雀魂登录失败次数", "雀魂牌谱请求失败次数", "外部数据下载失败次数",
}


def test_report_preserves_all_original_metrics_without_summing_overlapping_stages():
    assert set(_TIMINGS.values()) == EXPECTED_TIMINGS
    assert set(_SIZES.values()) == EXPECTED_SIZES
    assert set(_COUNTS.values()) == EXPECTED_COUNTS
    stats = {stage: index + 0.125 for index, stage in enumerate(_TIMINGS.values())}
    stats.update({stage: index + 10000 for index, stage in enumerate(_SIZES.values())})
    stats.update({stage: index for index, stage in enumerate(_COUNTS.values())})
    stats.update({"request_id": "a" * 32, "mode": "按需分析", "牌谱获取方式": "雀魂下载",
                  "牌谱获取结果": "成功", "雀魂连接结果": "成功", "complete压缩状态": "成功",
                  "是否命中本地记录": False, "是否等待文件锁": True, "最近雀魂失败类型": "网络失败",
                  "最后失败阶段": "雀魂登录"})
    report = build_report(stats, time.perf_counter())
    for name, stage in _TIMINGS.items():
        assert report["timings_ms"][name] == stats[stage]
    for name, stage in _SIZES.items():
        assert report["sizes_bytes"][name] == stats[stage]
    for name, stage in _COUNTS.items():
        assert report["counts"][name] == stats[stage]
    assert report["timings_ms"]["majsoul_download_total"] == stats["雀魂抓取"]
    assert report["status"] == {
        "mode": "on_demand", "record_source": "majsoul_download", "record_result": "success",
        "connection_result": "success", "compression": "success", "last_majsoul_failure": "network",
        "record_http_status": None, "local_record_hit": False, "waited_for_file_lock": True,
        "last_failed_stage": "majsoul_login",
    }
    assert report["ratios"]["gzip_to_original"] == stats["gzip压缩后字节数"] / stats["原始检讨JSON字节数"]
    assert report["measurement_scopes"]["onnx_inference"].startswith("session.run")
    assert json.loads(json.dumps(report, ensure_ascii=False, allow_nan=False)) == report


def test_unavailable_values_are_null_and_untrusted_text_never_leaks():
    secret = 'user@example.com "秘密"\\token\n'
    stats = {"request_id": secret, "牌谱获取方式": secret, "最近雀魂失败类型": secret,
             "最后失败阶段": secret, "原始数据大小": 2**53 - 1, "ONNX推理调用次数": 2**53 - 1,
             "雀魂抓取": float("nan"), "MJAI转换": float("inf"), "extra": secret}
    report = build_report(stats, time.perf_counter())
    encoded = json.dumps(report, ensure_ascii=False, allow_nan=False)
    parsed = json.loads(encoded)
    assert secret not in encoded
    assert parsed["request_id"] is None
    assert parsed["status"]["record_source"] is None
    assert parsed["status"]["last_majsoul_failure"] is None
    assert parsed["status"]["last_failed_stage"] is None
    assert parsed["timings_ms"]["majsoul_fetch"] is None
    assert parsed["timings_ms"]["mjai_convert"] is None
    assert parsed["timings_ms"]["onnx_inference"] is None
    assert parsed["sizes_bytes"]["raw_record"] == 2**53 - 1
    assert parsed["counts"]["onnx_calls"] == 2**53 - 1
    assert not any(isinstance(value, str) and value == "N/A" for value in parsed["timings_ms"].values())
    assert math.isfinite(parsed["timings_ms"]["total"])


def test_running_stage_and_http_error_remain_explicit():
    now = time.perf_counter()
    report = build_report({"_active_stages": {"雀魂登录": [now - 0.02]}, "牌谱获取结果": "失败(HTTP403)"}, now)
    assert report["timings_ms"]["majsoul_login"] >= 20
    assert report["active_stages"] == ["majsoul_login"]
    assert report["status"]["record_result"] == "http_error"
    assert report["status"]["record_http_status"] == 403
