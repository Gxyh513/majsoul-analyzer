"""Mahjong Soul share URL and opaque paipu ID parsing."""
from __future__ import annotations

import re
from .errors import MajsoulError


def decode_log_id(s: str) -> str:
    out = []
    for i, ch in enumerate(s):
        if "0" <= ch <= "9":
            o = ord(ch) - 48
        elif "a" <= ch <= "z":
            o = ord(ch) - 97 + 10
        else:
            out.append(ch)
            continue
        o = (o + 55 - i) % 36
        out.append(chr(o + 48) if o < 10 else chr(o - 10 + 97))
    return "".join(out)


def decode_account_id(n: int) -> int:
    return (((n - 1358437) ^ 86216345) - 1117113) // 7


def parse_paipu(text: str) -> tuple[str, int | None]:
    """Accept a full share link or a raw id. Returns (game_uuid, account_id|None)."""
    text = text.strip()
    m = re.search(r"paipu=([^&#\s]+)", text)
    pid = m.group(1) if m else text
    seps = pid.split("_")
    uuid_ = seps[0]
    target = None
    if len(seps) >= 3 and seps[2] == "2":
        uuid_ = decode_log_id(uuid_)
    if len(seps) >= 2 and seps[1]:
        if seps[1][0] == "a":
            target = decode_account_id(int(seps[1][1:]))
        elif seps[1].isdigit():
            target = int(seps[1])
    if not re.fullmatch(r"\d{6}-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", uuid_):
        raise MajsoulError(f"无法识别的牌谱 ID: {pid}")
    return uuid_, target


def parse_paipu_seat(text: str) -> int | None:
    match = re.search(r"paipu=([^&#\s]+)", text)
    parts = (match.group(1) if match else text.strip()).split("_")
    if len(parts) > 1 and parts[1] in ("0", "1", "2", "3"):
        return int(parts[1])
    return None


