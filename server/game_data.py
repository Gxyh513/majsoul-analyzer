"""Demo, MJAI and immutable Mahjong Soul record resolution to game events."""
from __future__ import annotations

import asyncio
import json
import re
import uuid
from typing import Any

from fastapi import HTTPException

from .majsoul.client import MajsoulError, parse_paipu, parse_paipu_seat
from .majsoul.convert import ConvertError, game_meta, to_mjai
from .record_store import CorruptRecordError, RawRecord, RecordStorageError
from .runtime_state import DEMO_PATH, RECORDS_DIR, LEGACY_RECORDS_DIRS, ms_client
from .admin_accounts import manager as admin_manager
from .app_config import load_config
from .record_store import get_or_download_record as _get_or_download_record
from .account_pool import fetch_record_with_failover
from .paipu_performance import measure as _measure, performance as _performance



async def _record_mjai_size(events: list[dict]) -> None:
    stats = _performance.get()
    if stats is not None:
        try:
            stats["MJAI数据大小"] = await asyncio.to_thread(
                lambda: len(json.dumps(events, ensure_ascii=False, separators=(",", ":")).encode("utf8"))
            )
        except Exception:  # noqa: BLE001
            pass


def select_game_seat(meta: dict, target_actor: int | None, url_acc_id: int | None = None) -> int:
    if target_actor is not None:
        return target_actor if 0 <= target_actor <= 3 else 0
    acc_ids = meta.get("account_ids", [])
    # A randomly assigned downloader account must not determine the reviewed player's seat.
    candidates = (url_acc_id,) if admin_manager() else (url_acc_id, ms_client.account_id, load_config().get("account_id"))
    for account_id in candidates:
        if account_id and account_id in acc_ids:
            return acc_ids.index(account_id)
    return 0


async def get_or_download_record(game_uuid: str) -> RawRecord | dict:
    try:
        return await _get_or_download_record(
            game_uuid, RECORDS_DIR, fetch_record_with_failover, legacy_dirs=LEGACY_RECORDS_DIRS
        )
    except RecordStorageError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=500, detail="无法访问原始牌谱存储目录。") from exc


async def resolve_game_data(
    paipu: str | None = None, content: Any | None = None, target_actor: int | None = None,
) -> tuple[str, list[dict], dict, int]:
    raw_input = (paipu or "").strip()
    if content is None and (not raw_input or raw_input == "demo" or raw_input.startswith("demo-")):
        if not DEMO_PATH.is_file():
            raise HTTPException(status_code=404, detail="内置演示牌谱文件不存在")
        text = await asyncio.to_thread(DEMO_PATH.read_text, encoding="utf8")
        with _measure("JSON解析"):
            events = await asyncio.to_thread(json.loads, text)
        meta = {"uuid": "demo-241024-majsoul-4p", "names": ["一姬 (演示)", "二阶堂美树", "八木唯", "斋藤治"],
                "room": "玉之间·四人南 (内置演示)", "start_time": 1729760000}
        await _record_mjai_size(events)
        return "demo", events, meta, select_game_seat(meta, target_actor)

    if content is not None:
        if isinstance(content, str):
            text = content.strip()
            try:
                with _measure("JSON解析"):
                    content = await asyncio.to_thread(json.loads, text)
            except json.JSONDecodeError:
                try:
                    with _measure("JSON解析"):
                        content = await asyncio.to_thread(
                            lambda: [json.loads(line) for line in text.splitlines() if line.strip()]
                        )
                except ValueError as exc:
                    raise HTTPException(status_code=400, detail="无法解析 JSON / JSONL 格式。") from exc
        try:
            if isinstance(content, dict) and "head" in content and "actions" in content:
                source_uuid = content.get("head", {}).get("uuid")
                if source_uuid and (not isinstance(source_uuid, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,119}", source_uuid)):
                    raise HTTPException(status_code=400, detail="牌谱 UUID 格式无效")
                with _measure("MJAI转换"):
                    events, meta = await asyncio.to_thread(lambda: (to_mjai(content), game_meta(content)))
                game_id = "upload-" + uuid.uuid4().hex
            elif isinstance(content, list) and content and isinstance(content[0], dict) and "type" in content[0]:
                events = content
                names = next((list(event["names"]) for event in events if event.get("type") == "start_game" and event.get("names")), ["东家", "南家", "西家", "北家"])
                game_id = "mjai-" + uuid.uuid4().hex
                meta = {"uuid": game_id, "names": names, "start_time": None}
            else:
                raise HTTPException(status_code=400, detail="不支持的 JSON 结构：请上传雀魂原始牌谱 JSON 或 mjai 事件数组/JSONL。")
        except (ConvertError, ValueError, TypeError, KeyError, IndexError) as exc:
            raise HTTPException(status_code=400, detail="牌谱数据无法解析，请检查文件。") from exc
        await _record_mjai_size(events)
        return game_id, events, meta, select_game_seat(meta, target_actor)

    try:
        with _measure("UUID解析"):
            game_uuid, url_acc_id = parse_paipu(raw_input)
    except (MajsoulError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="无法识别的牌谱 ID，请检查输入。") from exc
    stats = _performance.get()
    if stats is not None:
        stats["牌谱获取结果"] = "进行中"
    try:
        with _measure("原始牌谱获取总耗时"):
            stored = await get_or_download_record(game_uuid)
    except asyncio.CancelledError:
        if stats is not None:
            stats["牌谱获取结果"] = "已取消"
        raise
    except HTTPException as exc:
        if stats is not None:
            stats["牌谱获取结果"] = f"失败(HTTP{exc.status_code})"
        raise
    except Exception:
        if stats is not None:
            stats["牌谱获取结果"] = "失败"
        raise
    else:
        if stats is not None:
            stats["牌谱获取结果"] = "成功"
    try:
        if isinstance(stored, RawRecord):
            stats = _performance.get()
            if stats is not None:
                stats["原始数据大小"] = len(stored.response) + len(stored.detail)
                # Keep the exact reviewed source identity in request-local memory.
                # Do not re-read the record after conversion (it could change).
                prefetched = stats.pop("_prefetched_raw_digest", None)
                if (
                    isinstance(prefetched, tuple)
                    and len(prefetched) == 3
                    and prefetched[0] == game_uuid
                    and prefetched[1] == stored
                ):
                    digest = prefetched[2]
                else:
                    from .public_review_cache import _public_raw_source_digest
                    digest = await asyncio.to_thread(_public_raw_source_digest, stored)
                stats["_public_raw_source"] = (game_uuid, digest)
            with _measure("Protobuf解码"):
                record = await asyncio.to_thread(ms_client.decode_record, stored, game_uuid)
        else:
            record = stored
        with _measure("MJAI转换"):
            events, meta = await asyncio.to_thread(lambda: (to_mjai(record), game_meta(record)))
    except (CorruptRecordError, MajsoulError) as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except (ConvertError, ValueError, TypeError, KeyError, IndexError) as exc:
        raise HTTPException(status_code=400, detail="原始牌谱无法转换，请检查牌谱或协议版本。") from exc
    await _record_mjai_size(events)
    url_seat = parse_paipu_seat(raw_input)
    seat = target_actor if target_actor is not None else url_seat
    return game_uuid, events, meta, select_game_seat(meta, seat, url_acc_id if url_seat is None else None)


