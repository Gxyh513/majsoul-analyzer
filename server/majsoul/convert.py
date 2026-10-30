"""Convert a Mahjong Soul game record (as produced by ``MajsoulClient.fetch_record``)
into a list of mjai events understood by Mortal / libriichi.
"""
from __future__ import annotations

from ..engine.tiles import deaka, ms_to_mjai


class ConvertError(Exception):
    pass


def _seat_names(head: dict) -> tuple[list[str], list[int]]:
    names = ["AI"] * 4
    ids = [0] * 4
    for acc in head.get("accounts", []):
        seat = int(acc.get("seat", 0))
        if 0 <= seat < 4:
            names[seat] = acc.get("nickname") or f"玩家{seat}"
            ids[seat] = int(acc.get("account_id", 0))
    return names, ids


def game_meta(record: dict) -> dict:
    head = record["head"]
    names, ids = _seat_names(head)
    result = []
    for p in head.get("result", {}).get("players", []):
        result.append(
            {
                "seat": int(p.get("seat", 0)),
                "score": int(p.get("part_point_1", 0)),
                "total_point": int(p.get("total_point", 0)),
            }
        )
    mode = head.get("config", {}).get("mode", {})
    return {
        "uuid": head.get("uuid"),
        "start_time": head.get("start_time"),
        "end_time": head.get("end_time"),
        "names": names,
        "account_ids": ids,
        "result": result,
        "mode": mode.get("mode"),
        "category": head.get("config", {}).get("category"),
    }


def to_mjai(record: dict) -> list[dict]:
    head = record["head"]
    names, _ = _seat_names(head)
    events: list[dict] = [{"type": "start_game", "names": names}]

    hands: list[list[str]] = [[], [], [], []]
    pons: list[dict[str, list[str]]] = [{}, {}, {}, {}]
    dora_count = 0
    pending_reach: int | None = None
    last_discarder: int | None = None
    last_kan_actor: int | None = None
    in_kyoku = False

    def emit_doras(doras: list[str]):
        nonlocal dora_count
        while len(doras) > dora_count:
            events.append({"type": "dora", "dora_marker": ms_to_mjai(doras[dora_count])})
            dora_count += 1

    def accept_reach():
        nonlocal pending_reach
        if pending_reach is not None:
            events.append({"type": "reach_accepted", "actor": pending_reach})
            pending_reach = None

    def take(seat: int, tile: str):
        hands[seat].append(tile)

    def remove(seat: int, tile: str):
        try:
            hands[seat].remove(tile)
        except ValueError as e:
            raise ConvertError(f"玩家 {seat} 手牌中找不到 {tile}: {sorted(hands[seat])}") from e

    def end_kyoku():
        nonlocal in_kyoku
        events.append({"type": "end_kyoku"})
        in_kyoku = False

    for act in record["actions"]:
        name, d = act["name"], act["data"]

        if name == "RecordNewRound":
            if in_kyoku:
                end_kyoku()
            if not d.get("tiles3"):
                raise ConvertError("暂不支持三人麻将牌谱（Mortal 仅支持四麻）")
            chang, ju = int(d.get("chang", 0)), int(d.get("ju", 0))
            tiles = [[ms_to_mjai(t) for t in d.get(f"tiles{i}", [])] for i in range(4)]
            first_tsumo = None
            for i in range(4):
                if len(tiles[i]) == 14:
                    first_tsumo = tiles[i][13]
                    tiles[i] = tiles[i][:13]
                if len(tiles[i]) != 13:
                    raise ConvertError(f"配牌数量异常: seat {i} {len(tiles[i])}")
            doras = list(d.get("doras") or ([d["dora"]] if d.get("dora") else []))
            if not doras:
                raise ConvertError("缺少宝牌指示牌")
            hands = [list(t) for t in tiles]
            pons = [{}, {}, {}, {}]
            dora_count = 1
            pending_reach = None
            last_discarder = None
            last_kan_actor = None
            in_kyoku = True
            events.append(
                {
                    "type": "start_kyoku",
                    "bakaze": "ESWN"[chang],
                    "dora_marker": ms_to_mjai(doras[0]),
                    "kyoku": ju + 1,
                    "honba": int(d.get("ben", 0)),
                    "kyotaku": int(d.get("liqibang", 0)),
                    "oya": ju,
                    "scores": [int(x) for x in d.get("scores", [25000] * 4)],
                    "tehais": tiles,
                }
            )
            emit_doras(doras)
            if first_tsumo is not None:
                events.append({"type": "tsumo", "actor": ju, "pai": first_tsumo})
                take(ju, first_tsumo)

        elif name == "RecordDealTile":
            seat = int(d.get("seat", 0))
            accept_reach()
            emit_doras(d.get("doras", []))
            t = ms_to_mjai(d["tile"])
            events.append({"type": "tsumo", "actor": seat, "pai": t})
            take(seat, t)
            last_kan_actor = None

        elif name == "RecordDiscardTile":
            seat = int(d.get("seat", 0))
            t = ms_to_mjai(d["tile"])
            emit_doras(d.get("doras", []))
            is_reach = bool(d.get("is_liqi") or d.get("is_wliqi"))
            if is_reach:
                events.append({"type": "reach", "actor": seat})
            events.append({"type": "dahai", "actor": seat, "pai": t, "tsumogiri": bool(d.get("moqie"))})
            remove(seat, t)
            if is_reach:
                pending_reach = seat
            last_discarder = seat
            last_kan_actor = None

        elif name == "RecordChiPengGang":
            last_kan_actor = None
            seat = int(d.get("seat", 0))
            accept_reach()
            ts = [ms_to_mjai(t) for t in d["tiles"]]
            froms = [int(f) for f in d.get("froms", [])]
            called_i = next((i for i, f in enumerate(froms) if f != seat), len(ts) - 1)
            pai = ts[called_i]
            target = froms[called_i] if froms else last_discarder
            consumed = [t for i, t in enumerate(ts) if i != called_i]
            typ = int(d.get("type", 0))
            kind = {0: "chi", 1: "pon", 2: "daiminkan"}.get(typ)
            if kind is None:
                raise ConvertError(f"未知副露类型 {typ}")
            for c in consumed:
                remove(seat, c)
            events.append({"type": kind, "actor": seat, "target": target, "pai": pai, "consumed": consumed})
            if kind == "pon":
                pons[seat][deaka(pai)] = [pai] + consumed

        elif name == "RecordAnGangAddGang":
            seat = int(d.get("seat", 0))
            accept_reach()
            t = ms_to_mjai(d["tiles"])
            base = deaka(t)
            typ = int(d.get("type", 0))
            if typ == 3:  # ankan
                consumed = [x for x in hands[seat] if deaka(x) == base]
                if len(consumed) != 4:
                    raise ConvertError(f"暗杠牌数异常: {consumed}")
                for c in consumed:
                    remove(seat, c)
                events.append({"type": "ankan", "actor": seat, "consumed": consumed})
                last_kan_actor = seat
                emit_doras(d.get("doras", []))
            elif typ == 2:  # kakan
                pon = pons[seat].get(base)
                if pon is None:
                    raise ConvertError(f"加杠找不到对应的碰: {t}")
                cand = [x for x in hands[seat] if deaka(x) == base]
                if not cand:
                    raise ConvertError(f"加杠手牌中无 {t}")
                pai = t if t in cand else cand[0]
                remove(seat, pai)
                events.append({"type": "kakan", "actor": seat, "pai": pai, "consumed": pon})
                last_kan_actor = seat
                # dora for kakan is revealed later (handled by emit_doras on discard/deal)
            else:
                raise ConvertError(f"未知杠类型 {typ}")

        elif name == "RecordBaBei":
            raise ConvertError("暂不支持三人麻将牌谱（拔北）")

        elif name == "RecordHule":
            # A ron on the riichi declaration tile cancels the riichi.
            if pending_reach is not None:
                hules = d.get("hules", [])
                if all(h.get("zimo") for h in hules):
                    accept_reach()
                pending_reach = None
            deltas = [int(x) for x in d.get("delta_scores", [0, 0, 0, 0])]
            for i, h in enumerate(d.get("hules", [])):
                seat = int(h.get("seat", 0))
                if h.get("zimo"):
                    target = seat
                else:
                    target = last_kan_actor if last_kan_actor is not None else last_discarder
                ev = {
                    "type": "hora",
                    "actor": seat,
                    "target": target,
                    "han": int(h.get("count", 0)),
                    "fu": int(h.get("fu", 0)),
                    "title_id": int(h.get("title_id", 0)),
                    "dadian": int(h.get("dadian", 0)),
                    "yiman": bool(h.get("yiman", False)),
                    "fans": [
                        {"id": int(f.get("id", 0)), "val": int(f.get("val", 0)), "name": f.get("name", "")}
                        for f in h.get("fans", [])
                    ],
                }
                if target is not None and target != seat:
                    winning_action = next((x for x in reversed(events) if x.get("actor") == target and x.get("type") in ("dahai", "kakan", "ankan")), None)
                    if winning_action:
                        tile = (winning_action.get("consumed") or [None])[0] if winning_action.get("type") == "ankan" else winning_action.get("pai")
                        if tile:
                            ev["pai"] = tile
                if i == 0 and len(deltas) == 4:
                    ev["deltas"] = deltas
                ura = [ms_to_mjai(x) for x in h.get("li_doras", [])]
                if ura:
                    ev["ura_markers"] = ura
                events.append(ev)
            end_kyoku()

        elif name == "RecordNoTile":
            accept_reach()
            deltas = [0, 0, 0, 0]
            for s in d.get("scores", []):
                for i, x in enumerate(s.get("delta_scores", [])):
                    if i < 4:
                        deltas[i] += int(x)
            events.append({
                "type": "ryukyoku",
                "deltas": deltas,
                "majsoul": {
                    "record_type": name,
                    "reason": "nagashi_mangan" if d.get("liujumanguan") else "exhaustive",
                    "liujumanguan": bool(d.get("liujumanguan")),
                    "players": d.get("players", []),
                    "scores": d.get("scores", []),
                },
            })
            end_kyoku()

        elif name == "RecordLiuJu":
            if d.get("liqi"):
                accept_reach()
            else:
                pending_reach = None
            raw_type = int(d.get("type", 0))
            reasons = {1: "kyushu_kyuhai", 2: "suufon_renda", 3: "suukan_sanra", 4: "suucha_riichi"}
            events.append({
                "type": "ryukyoku",
                "deltas": [0, 0, 0, 0],
                "majsoul": {
                    "record_type": name,
                    "reason": reasons.get(raw_type, "unknown"),
                    "original_type": raw_type,
                    "seat": d.get("seat"),
                    "tiles": d.get("tiles", []),
                },
            })
            end_kyoku()

        else:
            # Unknown / irrelevant record types (e.g. fill-in events) are ignored.
            continue

    if in_kyoku:
        end_kyoku()
    events.append({"type": "end_game"})
    return events
