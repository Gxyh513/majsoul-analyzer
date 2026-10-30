"""Shanten / ukeire calculation for 34-index tile count arrays."""
from __future__ import annotations

def _suit_opts(counts: tuple) -> frozenset:
    """All Pareto-optimal (mentsu, taatsu, pair) for a 9-tile suit (numbers)."""
    i = 0
    while i < 9 and counts[i] == 0:
        i += 1
    if i == 9:
        return frozenset({(0, 0, 0)})
    c = list(counts)
    res = set()

    def sub(delta_idx, m, t, p):
        cc = list(c)
        for j in delta_idx:
            cc[j] -= 1
        for (m2, t2, p2) in _suit_opts(tuple(cc)):
            if p + p2 <= 1:
                res.add((m + m2, t + t2, p + p2))

    if c[i] >= 3:
        sub([i, i, i], 1, 0, 0)
    if i + 2 < 9 and c[i + 1] and c[i + 2]:
        sub([i, i + 1, i + 2], 1, 0, 0)
    if c[i] >= 2:
        sub([i, i], 0, 0, 1)  # as pair
        sub([i, i], 0, 1, 0)  # as taatsu (shanpon part)
    if i + 1 < 9 and c[i + 1]:
        sub([i, i + 1], 0, 1, 0)
    if i + 2 < 9 and c[i + 2]:
        sub([i, i + 2], 0, 1, 0)
    sub([i], 0, 0, 0)  # isolated
    return frozenset(_pareto(res))


def _pareto(s):
    out = []
    for a in s:
        dominated = False
        for b in s:
            if b != a and b[0] >= a[0] and b[1] >= a[1] and b[2] >= a[2]:
                dominated = True
                break
        if not dominated:
            out.append(a)
    return out


def _honor_opts(counts) -> list:
    m = t = p_opts = 0
    pairs = 0
    for x in counts:
        if x >= 3:
            m += 1
        elif x == 2:
            pairs += 1
    # either one pair used as jantou, rest as taatsu
    opts = [(m, pairs, 0)]
    if pairs:
        opts.append((m, pairs - 1, 1))
    return opts


def shanten_regular(c: list[int], n_melds: int = 0) -> int:
    parts = [
        _suit_opts(tuple(c[0:9])),
        _suit_opts(tuple(c[9:18])),
        _suit_opts(tuple(c[18:27])),
        _honor_opts(c[27:34]),
    ]
    states = {(0, 0, 0)}
    for opts in parts:
        nxt = set()
        for (m, t, p) in states:
            for (m2, t2, p2) in opts:
                if p + p2 <= 1:
                    nxt.add((m + m2, t + t2, p + p2))
        states = set(_pareto(nxt))
    best = 8
    for (m, t, p) in states:
        mt = m + n_melds
        te = min(t, 4 - mt)
        # when no pair, having 5 blocks means one block must become the pair
        s = 8 - 2 * mt - te - p
        best = min(best, s)
    return best


def shanten_chiitoi(c: list[int]) -> int:
    pairs = sum(1 for x in c if x >= 2)
    kinds = sum(1 for x in c if x >= 1)
    return 6 - pairs + max(0, 7 - kinds)


YAOCHU = [0, 8, 9, 17, 18, 26, 27, 28, 29, 30, 31, 32, 33]


def shanten_kokushi(c: list[int]) -> int:
    kinds = sum(1 for i in YAOCHU if c[i] >= 1)
    pair = any(c[i] >= 2 for i in YAOCHU)
    return 13 - kinds - (1 if pair else 0)


def shanten(c: list[int], n_melds: int = 0) -> int:
    s = shanten_regular(c, n_melds)
    if n_melds == 0:
        s = min(s, shanten_chiitoi(c), shanten_kokushi(c))
    return s


def ukeire(c: list[int], n_melds: int, visible: list[int], cur: int | None = None):
    """Return (total_count, [tile_idx...]) of tiles that reduce shanten.

    ``c`` is a hand of 3n+1 tiles. ``visible`` counts tiles visible to the
    player (including own hand)."""
    if cur is None:
        cur = shanten(c, n_melds)
    total = 0
    tiles = []
    for i in range(34):
        if visible[i] >= 4 and c[i] >= 4:
            continue
        c[i] += 1
        if shanten(c, n_melds) < cur:
            left = max(0, 4 - visible[i])
            if left > 0 or True:
                total += left
                tiles.append(i)
        c[i] -= 1
    return total, tiles


def is_agari(c: list[int], n_melds: int = 0) -> bool:
    return shanten(c, n_melds) == -1
