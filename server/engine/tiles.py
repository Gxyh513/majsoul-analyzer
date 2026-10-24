"""Tile helpers (mjai notation).

mjai tiles: 1m..9m, 1p..9p, 1s..9s, E S W N P F C, red fives 5mr 5pr 5sr.
34-index: 0-8 m, 9-17 p, 18-26 s, 27-33 E S W N P F C.
Mortal action index for discards: 0-33 as above, 34/35/36 = 5mr/5pr/5sr.
"""
from __future__ import annotations

HONORS = ["E", "S", "W", "N", "P", "F", "C"]
HONOR_CN = {"E": "东", "S": "南", "W": "西", "N": "北", "P": "白", "F": "发", "C": "中"}
SUIT_CN = {"m": "万", "p": "筒", "s": "索"}
NUM_CN = "零一二三四五六七八九"

ALL_TILES_34 = [f"{n}{s}" for s in "mps" for n in range(1, 10)] + HONORS


def tid(t: str) -> int:
    """mjai tile -> 34 index (red five maps to the normal five)."""
    if t in HONORS:
        return 27 + HONORS.index(t)
    n = int(t[0])
    return "mps".index(t[1]) * 9 + n - 1


def tname(i: int) -> str:
    return ALL_TILES_34[i]


def is_aka(t: str) -> bool:
    return len(t) == 3 and t.endswith("r")


def deaka(t: str) -> str:
    return t[:2] if is_aka(t) else t


def action_idx_of_tile(t: str) -> int:
    if is_aka(t):
        return 34 + "mps".index(t[1])
    return tid(t)


def tile_of_action_idx(i: int) -> str:
    if i < 34:
        return tname(i)
    return "5" + "mps"[i - 34] + "r"


def is_yaochu(i: int) -> bool:
    return i >= 27 or i % 9 in (0, 8)


def dora_from_marker(marker: str) -> int:
    i = tid(marker)
    if i < 27:
        base = i - i % 9
        return base + (i % 9 + 1) % 9
    if i <= 30:  # winds E S W N
        return 27 + (i - 27 + 1) % 4
    return 31 + (i - 31 + 1) % 3  # dragons P F C


def tile_cn(t: str) -> str:
    if t in HONOR_CN:
        return HONOR_CN[t]
    s = SUIT_CN[t[1]]
    n = "赤五" if is_aka(t) else NUM_CN[int(t[0])]
    return n + s


def ms_to_mjai(t: str) -> str:
    """Mahjong Soul tile ('0m', '5p', '1z'...) -> mjai."""
    n, s = t[0], t[1]
    if s == "z":
        return HONORS[int(n) - 1]
    if n == "0":
        return f"5{s}r"
    return f"{n}{s}"


def sort_key(t: str):
    i = tid(t)
    return (i, 1 if is_aka(t) else 0)


ACTION_NAMES_CN = {
    37: "立直",
    38: "吃(低)",
    39: "吃(中)",
    40: "吃(高)",
    41: "碰",
    42: "杠",
    43: "和了",
    44: "九种九牌流局",
    45: "跳过",
}


def action_label(i: int) -> str:
    if i <= 36:
        return "切 " + tile_cn(tile_of_action_idx(i))
    return ACTION_NAMES_CN.get(i, str(i))
