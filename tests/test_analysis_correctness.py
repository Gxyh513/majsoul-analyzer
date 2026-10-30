"""Focused correctness regressions for model review output."""
from server.engine.reviewer import compute_tile_danger
from server.engine.tiles import tid


def danger(rivers, riichi, melds=None):
    return compute_tile_danger(
        tid("1m"), False, 0, rivers, riichi,
        melds or [[], [], [], []], [], [0] * 34,
    )


def test_genbutsu_label_identifies_partial_riichi_safety():
    rivers = [[], [{"pai": "1m", "step": 1}], [], []]
    value, tags = danger(rivers, [None, 5, 5, None])
    assert value > 0
    assert "对部分立直者现物" in tags
    assert "现物" not in tags


def test_genbutsu_label_when_safe_against_all_riichi_players():
    rivers = [[], [{"pai": "1m", "step": 1}], [{"pai": "1m", "step": 2}], []]
    value, tags = danger(rivers, [None, 5, 5, None])
    assert value == 0
    assert "现物" in tags


def test_genbutsu_label_distinguishes_other_open_hand_threat():
    rivers = [[], [{"pai": "1m", "step": 1}], [{"pai": "9s", "step": i} for i in range(6)], []]
    melds = [[], [], [{}, {}], []]
    value, tags = danger(rivers, [None, 5, None, None], melds)
    assert value > 0
    assert "对立直者现物" in tags


def convert_end(name, data):
    from server.majsoul.convert import to_mjai
    return next(e for e in to_mjai({"head": {}, "actions": [{"name": name, "data": data}]}) if e["type"] == "ryukyoku")


def test_exhaustive_and_nagashi_mangan_preserve_details():
    scores = [{"seat": 0, "delta_scores": [8000, -4000, -2000, -2000]},
              {"seat": 1, "delta_scores": [-2000, 8000, -4000, -2000]}]
    event = convert_end("RecordNoTile", {"liujumanguan": True, "scores": scores})
    assert event["deltas"] == [6000, 4000, -6000, -4000]
    assert event["majsoul"]["reason"] == "nagashi_mangan"
    assert event["majsoul"]["scores"] == scores
    assert convert_end("RecordNoTile", {})["majsoul"]["reason"] == "exhaustive"


def test_abortive_draw_reasons_and_unknown_are_preserved():
    expected = {1: "kyushu_kyuhai", 2: "suufon_renda", 3: "suukan_sanra", 4: "suucha_riichi", 5: "unknown", 0: "unknown"}
    for raw, reason in expected.items():
        event = convert_end("RecordLiuJu", {"type": raw, "seat": 2, "tiles": ["1m"]})
        assert event["type"] == "ryukyoku"
        assert event["deltas"] == [0, 0, 0, 0]
        assert event["majsoul"]["original_type"] == raw
        assert event["majsoul"]["reason"] == reason
        assert event["majsoul"]["seat"] == 2


def test_libriichi_accepts_added_draw_metadata():
    import json
    from pathlib import Path
    from server.engine import reviewer
    events = json.loads(Path("data/demo_mjai.json").read_text(encoding="utf8"))
    state = reviewer.lstate.PlayerState(0)
    for event in events:
        if event["type"] == "end_kyoku":
            break
        state.update(json.dumps(event))
    event = {"type": "ryukyoku", "deltas": [0, 0, 0, 0], "majsoul": {"reason": "unknown", "original_type": 5}}
    state.update(json.dumps(event))


def test_multiple_kan_choices_are_not_fabricated():
    from server.engine.reviewer import label_to_action_dict, format_action_cn
    action = label_to_action_dict(42, 0, 1, "9s", [False] * 3, ["1m", "9m"], [])
    assert action == {"type": "kan", "actor": 0, "ambiguous": True}
    assert format_action_cn(action) == "杠（具体牌未判定）"
    action = label_to_action_dict(42, 0, 1, "9s", [True, False, False], ["5m"], [])
    assert sorted(action["consumed"]) == ["5m", "5m", "5m", "5mr"]


def test_ambiguous_kan_is_not_counted_as_agreement(monkeypatch):
    import numpy as np
    from test_review_regressions import FakePlayerState, FakeManager, start_round
    from server.engine import reviewer

    class KanState(FakePlayerState):
        def ankan_candidates(self): return ["1m", "2m"] if self.phase == "tsumo" else []
        def encode_obs(self, version, oracle):
            obs = np.zeros((1012, 34), dtype=np.float32)
            mask = np.zeros(46, dtype=bool)
            mask[0] = mask[42] = True
            return obs, mask

    monkeypatch.setattr(reviewer.lstate, "PlayerState", KanState)
    monkeypatch.setattr(reviewer, "analyze_candidate_metadata", lambda *args: {})
    events = [{"type": "start_game", "names": ["A", "B", "C", "D"]}, start_round(),
              {"type": "tsumo", "actor": 0, "pai": "1m"},
              {"type": "ankan", "actor": 0, "consumed": ["1m"] * 4},
              {"type": "end_kyoku"}, {"type": "end_game"}]
    result = reviewer.review_game(events, 0, FakeManager())
    dec = result["decisions"][0]
    assert dec["expected"]["type"] == "kan"
    assert dec["is_equal"] is None and dec["verdict"] == "unknown"
    assert dec["rel_score"] is None
    assert result["summary"]["total_reviewed"] == 0
    assert result["summary"]["unscored_kan"] == 1
    assert result["summary"]["match_rate"] is None
    assert result["summary"]["rating"] is None
    assert result["kyokus"][0]["unscored_count"] == 1
    assert result["kyokus"][0]["stats"]["match_rate"] is None
    assert result["kyokus"][0]["stats"]["rating"] is None


def test_review_cache_key_depends_on_events_and_metadata():
    from server.app import review_cache_key
    def key(events, meta):
        return review_cache_key('same-uuid', events, meta, 0, 'akagi', 0.1, 'v3_' + 'a' * 64)
    first = [{'type': 'start_game', 'names': ['A']}]
    second = [{'type': 'start_game', 'names': ['B']}]
    assert key(first, {'names': ['A']}) != key(second, {'names': ['A']})
    assert key(first, {'names': ['A']}) != key(first, {'names': ['B']})
    assert key(first, {'a': 1, 'b': 2}) == key(first, {'b': 2, 'a': 1})
    assert len(key(first, {'names': ['A']})) < 240


def test_memory_cache_resolves_configured_account_seat(monkeypatch):
    import asyncio
    from server import app
    monkeypatch.setattr(app.ms_client, 'account_id', 20)
    monkeypatch.setattr(app, 'load_config', lambda: {})
    game_uuid = '241026-11111111-2222-3333-4444-555555555555'
    monkeypatch.setattr(app, '_MEMORY_LOGS', {game_uuid: ([{'type': 'start_game'}], {'account_ids': [10, 20, 30, 40]})})
    _, _, _, seat = asyncio.run(app.resolve_game_data(paipu=game_uuid))
    assert seat == 1
    _, _, _, seat = asyncio.run(app.resolve_game_data(paipu=game_uuid, target_actor=2))
    assert seat == 2


def test_same_uuid_different_upload_events_do_not_reuse_review(monkeypatch):
    import asyncio
    from server import app
    calls = []
    cache = {}
    monkeypatch.setattr(app, 'resolve_engine_cache_info', lambda _: ('akagi', 'v3_' + 'a' * 64))
    monkeypatch.setattr(app, 'valid_review_source', lambda *_: True)
    monkeypatch.setattr(app, 'get_cached_review', lambda k: cache.get(k))
    monkeypatch.setattr(app, 'save_cached_review', lambda k, v: cache.__setitem__(k, v.copy()))
    def fake_review(events, seat, mgr, **kwargs):
        calls.append(events)
        return {'events': events, 'engine': {}, 'summary': {'match_rate': 0, 'rating': 0}}
    monkeypatch.setattr(app, 'review_game', fake_review)
    async def run():
        first = await app._run_review_with_cache('same-uuid', [{'type':'first'}], {'names':['A']}, 0, 'akagi', 0.1)
        second = await app._run_review_with_cache('same-uuid', [{'type':'second'}], {'names':['A']}, 0, 'akagi', 0.1)
        return first, second
    first, second = asyncio.run(run())
    assert first['events'][0]['type'] == 'first'
    assert second['events'][0]['type'] == 'second'
    assert len(calls) == 2


def test_upload_identity_is_namespaced_and_content_based(monkeypatch):
    import asyncio
    from server import app
    monkeypatch.setattr(app, '_MEMORY_LOGS', {})
    source = {'head': {'uuid': 'same_uuid', 'accounts': [], 'result': {}, 'config': {}}, 'actions': []}
    other = {'head': {'uuid': 'same_uuid', 'accounts': [], 'result': {'players': [{'seat': 0, 'total_point': 5}]}, 'config': {}}, 'actions': []}
    first, _, first_meta, _ = asyncio.run(app.resolve_game_data(content=source))
    second, _, _, _ = asyncio.run(app.resolve_game_data(content=other))
    assert first.startswith('upload-') and first != second
    assert first_meta['uuid'] == 'same_uuid'
    assert 'same_uuid' not in app._MEMORY_LOGS
    _, _, _, seat = asyncio.run(app.resolve_game_data(paipu=first, target_actor=1))
    assert seat == 1


def test_robbing_kakan_records_the_added_tile():
    from server.majsoul.convert import to_mjai
    hands = [['5m'] + ['1m'] * 12, ['5m'] * 3 + ['2m'] * 10, ['3m'] * 13, ['4m'] * 13]
    round_data = {f'tiles{i}': hand for i, hand in enumerate(hands)}
    round_data.update({'dora': '1m', 'scores': [25000] * 4})
    actions = [
        {'name': 'RecordNewRound', 'data': round_data},
        {'name': 'RecordDiscardTile', 'data': {'seat': 0, 'tile': '5m'}},
        {'name': 'RecordChiPengGang', 'data': {'seat': 1, 'type': 1, 'tiles': ['5m'] * 3, 'froms': [1, 1, 0]}},
        {'name': 'RecordDiscardTile', 'data': {'seat': 1, 'tile': '2m'}},
        {'name': 'RecordAnGangAddGang', 'data': {'seat': 1, 'type': 2, 'tiles': '5m'}},
        {'name': 'RecordHule', 'data': {'hules': [{'seat': 2}], 'delta_scores': [0, -1000, 1000, 0]}},
    ]
    events = to_mjai({'head': {}, 'actions': actions})
    win = next(event for event in events if event['type'] == 'hora')
    assert win['target'] == 1 and win['pai'] == '5m'


def test_logout_removes_local_secrets_even_with_environment_credentials(monkeypatch, tmp_path):
    import asyncio
    import json
    from server import app
    from unittest.mock import AsyncMock
    config = tmp_path / 'config.local.json'
    config.write_text(json.dumps({'account': 'local', 'password': 'local-pass', 'access_token': 'local-token'}))
    monkeypatch.setattr(app, 'LOCAL_CONFIG_PATH', config)
    monkeypatch.setenv('MAJSOUL_PASSWORD', 'env-secret')
    monkeypatch.setattr(app.ms_client, 'close', AsyncMock())
    monkeypatch.setattr(app, 'api_status', AsyncMock(return_value={'ok': True}))
    asyncio.run(app.api_logout())
    saved = json.loads(config.read_text())
    assert 'password' not in saved and 'access_token' not in saved
    assert 'env-secret' not in config.read_text()
