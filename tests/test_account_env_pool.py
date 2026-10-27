"""Environment-managed account pools remain private and editable."""
import json
from pathlib import Path

import pytest
from server import app


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(app, "CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr(app, "LOCAL_CONFIG_PATH", tmp_path / "config.local.json")
    monkeypatch.setattr(app, "EXAMPLE_CONFIG_PATH", tmp_path / "example.json")
    for key in ("MAJSOUL_ACCOUNT", "MAJSOUL_PASSWORD", "MAJSOUL_ACCESS_TOKEN", "MAJSOUL_ACCOUNTS_JSON"):
        monkeypatch.delenv(key, raising=False)
    app._env_pool_token_overrides.clear()
    yield
    app._env_pool_token_overrides.clear()


def test_environment_pool_uses_all_five_and_overrides_stale_config(monkeypatch):
    app.LOCAL_CONFIG_PATH.write_text(json.dumps({"account":"stale", "password":"old", "backup_accounts":[{"account":"old2","password":"pw"}]}))
    pool = [{"account":f"new{i}", "password":f"pw{i}", "access_token":f"token{i}"} for i in range(5)]
    monkeypatch.setenv("MAJSOUL_ACCOUNTS_JSON", json.dumps(pool))
    accounts = app.get_all_configured_accounts()
    assert [a["account"] for a in accounts] == [f"new{i}" for i in range(5)]
    assert accounts[0]["is_primary"] and len(accounts) == 5
    # Manual deletion or insertion in the secret immediately replaces the pool.
    monkeypatch.setenv("MAJSOUL_ACCOUNTS_JSON", json.dumps([pool[4], {"account":"new6", "password":"pw6"}]))
    assert [a["account"] for a in app.get_all_configured_accounts()] == ["new4", "new6"]


def test_invalid_env_pool_fails_closed_without_echoing_secrets(monkeypatch):
    for value in ("not-json-secret-value", "{}", "[42]", "[{\"account\":\"name-only\"}]"):
        monkeypatch.setenv("MAJSOUL_ACCOUNTS_JSON", value)
        with pytest.raises(ValueError) as err:
            app.load_config()
        assert value not in str(err.value)


def test_token_refresh_stays_in_memory_for_env_pool(monkeypatch):
    pool = [{"account":"primary", "password":"p", "access_token":"old"},
            {"account":"backup", "password":"p2", "access_token":"old2"}]
    monkeypatch.setenv("MAJSOUL_ACCOUNTS_JSON", json.dumps(pool))
    accounts = app.get_all_configured_accounts()
    app._persist_updated_token(accounts[0], "fresh")
    app._persist_updated_token(accounts[1], "fresh2")
    assert not app.LOCAL_CONFIG_PATH.exists()
    assert [a["access_token"] for a in app.get_all_configured_accounts()] == ["fresh", "fresh2"]
    # Refreshing again keeps the mapping tied to the original env value.
    app._persist_updated_token(app.get_all_configured_accounts()[0], "fresh-again")
    assert app.get_all_configured_accounts()[0]["access_token"] == "fresh-again"
    pool[0]["access_token"] = "manually-rotated"
    monkeypatch.setenv("MAJSOUL_ACCOUNTS_JSON", json.dumps(pool))
    assert app.get_all_configured_accounts()[0]["access_token"] == "manually-rotated"


def test_empty_pool_does_not_fall_back_to_old_account(monkeypatch):
    app.LOCAL_CONFIG_PATH.write_text(json.dumps({"account":"stale", "password":"old"}))
    monkeypatch.setenv("MAJSOUL_ACCOUNTS_JSON", "[]")
    assert app.get_all_configured_accounts() == []
