"""Yakuman, round and result description labels for replay presentation."""
from __future__ import annotations

from typing import Any


BAKAZE_CN = {"E": "东", "S": "南", "W": "西", "N": "北"}
SEAT_WIND_CN = ["东", "南", "西", "北"]
# Mahjong Soul fan IDs, not Tenhou IDs: 雀魂API 定数一覧にゃ.
# https://wikiwiki.jp/majsoul-api/定数一覧にゃ
MAJSOUL_YAKU_NAMES_CN = {
    35: "天和", 36: "地和", 37: "大三元", 38: "四暗刻", 39: "字一色",
    40: "绿一色", 41: "清老头", 42: "国士无双", 43: "小四喜", 44: "四杠子",
    45: "九莲宝灯", 46: "八连庄", 47: "纯正九莲宝灯", 48: "四暗刻单骑",
    49: "国士无双十三面", 50: "大四喜", 51: "燕返し", 52: "槓振り", 53: "十二落抬",
    54: "五門斉", 55: "三連刻", 56: "一色三順", 57: "一筒摸月", 58: "九筒撈魚",
    59: "人和", 60: "大車輪", 61: "大竹林", 62: "大数隣", 63: "石の上にも三年", 64: "大七星",
}


def _hora_score_description(event: dict[str, Any]) -> str:
    """Format Mahjong Soul yakuman values as multipliers, not han/fu."""
    if not event.get("yiman"):
        han, fu = event.get("han", event.get("count")), event.get("fu")
        score = f"{fu}符{han}番 " if fu and han else ""
        return f"累计役满 {score}" if event.get("title_id") == 11 else score

    fans = []
    for fan in event.get("fans") or []:
        if not isinstance(fan, dict):
            continue
        try:
            value = int(fan.get("val", 0))
        except (TypeError, ValueError):
            continue
        if value > 0:
            fans.append((fan, value))

    # For yiman the protocol's count, converted to han, is the total. Fan
    # entries can be missing, so only use their sum when no total is available.
    count = event.get("count", event.get("han"))
    title_id = event.get("title_id")
    if isinstance(count, int) and not isinstance(count, bool) and count > 0:
        multiplier = count
    elif isinstance(title_id, int) and 5 <= title_id <= 10:
        multiplier = title_id - 4
    else:
        multiplier = sum(value for _, value in fans) or 1
    title = f"{multiplier}倍役满" if multiplier > 1 else "役满"

    names = [
        fan.get("name") or MAJSOUL_YAKU_NAMES_CN.get(fan.get("id")) or f"未知役种（ID {fan.get('id', '?')}）"
        for fan, _ in fans
    ]
    return f"{title}{'（' + '、'.join(str(name) for name in names) + '）' if names else ''} "


