"""Focused correctness regressions for model review output."""
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
    events = [{"type": "start_game", "names": ["A", "B", "C", "D"]}, start_round(),
              {"type": "tsumo", "actor": 0, "pai": "1m"},
              {"type": "ankan", "actor": 0, "consumed": ["1m"] * 4},
              {"type": "end_kyoku"}, {"type": "end_game"}]
    result = reviewer.review_game(events, 0, FakeManager())
    dec = result["kyokus"][0]["decisions"][0]
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


def test_stored_record_resolves_configured_account_seat(monkeypatch, tmp_path):
    import asyncio
    import json
    from unittest.mock import AsyncMock
    from server import app

    game_uuid = '241026-11111111-2222-3333-4444-555555555555'
    record = {'head': {'uuid': game_uuid, 'accounts': [
        {'seat': seat, 'account_id': account_id} for seat, account_id in enumerate([10, 20, 30, 40])
    ]}, 'actions': []}
    (tmp_path / f'{game_uuid}.json').write_text(json.dumps(record), encoding='utf8')
    monkeypatch.setattr(app, 'RECORDS_DIR', tmp_path)
    monkeypatch.setattr(app, 'LEGACY_RECORDS_DIRS', ())
    monkeypatch.setattr(app.ms_client, 'account_id', 20)
    monkeypatch.setattr(app, 'load_config', lambda: {})
    fetch = AsyncMock(side_effect=AssertionError('unexpected network fetch'))
    monkeypatch.setattr(app, 'fetch_record_with_failover', fetch)
    _, _, _, seat = asyncio.run(app.resolve_game_data(paipu=game_uuid))
    assert seat == 1
    _, _, _, seat = asyncio.run(app.resolve_game_data(paipu=game_uuid, target_actor=2))
    assert seat == 2
    fetch.assert_not_awaited()


def test_same_uuid_different_upload_events_are_reanalysed(monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from server import app
    from server.engine.model_policy import MODEL_FILE, MODEL_ID, MODEL_REVISION, MODEL_SHA256, REVIEW_SCHEMA

    sha = MODEL_SHA256
    calls = []
    monkeypatch.setattr(app, 'review_semaphore', asyncio.Semaphore(1))
    monkeypatch.setattr(app, '_review_tasks', set())
    monkeypatch.setattr(app, 'engine_mgr', SimpleNamespace(
        get_mortal=lambda _: (SimpleNamespace(weight_sha256=sha), MODEL_ID, None),
    ))

    def fake_review(events, seat, mgr, **kwargs):
        calls.append(events)
        return {'events': events, 'target_actor': seat, 'summary': {'match_rate': 0, 'rating': 0},
                'engine': {'type': 'mortal', 'id': MODEL_ID, 'weight_file': MODEL_FILE,
                           'weight_sha256': sha, 'version': 4, 'steps': None, 'revision': MODEL_REVISION,
                           'q_source': 'neural_network', 'review_schema': REVIEW_SCHEMA}}

    monkeypatch.setattr(app, 'review_game', fake_review)

    async def run():
        first = await app._run_review('same-uuid', [{'type': 'first'}], {'names': ['A']}, 0, MODEL_ID, 0.1)
        second = await app._run_review('same-uuid', [{'type': 'second'}], {'names': ['A']}, 0, MODEL_ID, 0.1)
        third = await app._run_review('same-uuid', [{'type': 'first'}], {'names': ['A']}, 0, MODEL_ID, 0.1)
        return first, second, third

    first, second, third = asyncio.run(run())
    assert [result['events'][0]['type'] for result in (first, second, third)] == ['first', 'second', 'first']
    assert len(calls) == 3


def test_upload_identity_is_namespaced_and_unique(monkeypatch):
    import asyncio
    from server import app

    monkeypatch.setattr(app, 'load_config', lambda: {})
    source = {'head': {'uuid': 'same_uuid', 'accounts': [], 'result': {}, 'config': {}}, 'actions': []}
    other = {'head': {'uuid': 'same_uuid', 'accounts': [], 'result': {'players': [{'seat': 0, 'total_point': 5}]}, 'config': {}}, 'actions': []}
    first, _, first_meta, _ = asyncio.run(app.resolve_game_data(content=source))
    second, _, _, _ = asyncio.run(app.resolve_game_data(content=other))
    third, _, _, seat = asyncio.run(app.resolve_game_data(content=source, target_actor=1))
    assert all(game_id.startswith('upload-') for game_id in (first, second, third))
    assert len({first, second, third}) == 3
    assert first_meta['uuid'] == 'same_uuid' and seat == 1


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
