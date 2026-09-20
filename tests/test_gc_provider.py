import os
import stat
import sys
from pathlib import Path

import pytest

from deaddemo.core import secrets
from deaddemo.core.gc import provider


@pytest.fixture(autouse=True)
def _game_not_running(monkeypatch):
    monkeypatch.setattr(provider, "game_running", lambda: False)


def test_game_running_detection(monkeypatch):
    monkeypatch.undo()
    fake = '"Deadlock.exe","123","Console","1","1,000 K"\n"DeadDemo.exe","5","Console","1","1 K"\n'
    monkeypatch.setattr(provider.subprocess, "run",
                        lambda *a, **k: type("P", (), {"stdout": fake})())
    if sys.platform == "win32":
        assert provider.game_running() is True
        monkeypatch.setattr(provider.subprocess, "run",
                            lambda *a, **k: type("P", (), {"stdout": '"deaddemo-gc.exe","1","Console","1","1 K"\n'})())
        assert provider.game_running() is False


def _fake_helper(tmp_path: Path, monkeypatch, script: str) -> Path:
    """Install a fake deaddemo-gc that runs a Python script, and point the provider at it."""
    if sys.platform == "win32":
        helper = tmp_path / "deaddemo-gc.cmd"
        py = tmp_path / "helper.py"
        py.write_text(script, encoding="utf-8")
        helper.write_text(f'@echo off\r\n"{sys.executable}" "{py}" %*\r\n', encoding="utf-8")
    else:
        helper = tmp_path / "deaddemo-gc"
        helper.write_text(f"#!{sys.executable}\n{script}", encoding="utf-8")
        helper.chmod(helper.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv(secrets.ENV_GC_BINARY, str(helper))
    return helper


LOGIN_SCRIPT = """
import json, os, sys
assert os.environ["DEADDEMO_STEAM_USER"] == "alice"
assert os.environ["DEADDEMO_STEAM_PASSWORD"] == "pw"
assert "pw" not in " ".join(sys.argv)
print("some log line")
print(json.dumps({"refresh_token": "rt-abc", "steam_id64": 76561198314737511, "account_id": 354471783}))
"""

SALTS_SCRIPT = """
import json, os, sys
assert os.environ["DEADDEMO_STEAM_REFRESH_TOKEN"] == "rt-abc"
for m in sys.argv[2:]:
    if m == "2":
        print(json.dumps({"match_id": 2, "result": "k_eResult_InvalidMatch"}))
    else:
        print(json.dumps({"match_id": int(m), "result": "success", "replay_salt": 111, "metadata_salt": 222,
                          "cluster_id": 407, "replay_valid_through": 1800000000}))
"""


def test_login_stores_token_and_never_passes_secrets_on_argv(tmp_path, monkeypatch):
    _fake_helper(tmp_path, monkeypatch, LOGIN_SCRIPT)
    data = provider.login("alice", "pw")
    assert data["account_id"] == 354471783 and "refresh_token" not in data
    assert provider.is_logged_in() and provider.logged_in_user() == "alice"
    assert secrets.load_token(provider.TOKEN_NAME) == "rt-abc"


def test_fetch_salts_parses_results_and_counts_quota(tmp_path, monkeypatch):
    _fake_helper(tmp_path, monkeypatch, SALTS_SCRIPT)
    secrets.store_token(provider.TOKEN_NAME, "rt-abc")
    secrets.store_token(provider.USER_NAME, "alice")
    results = provider.fetch_salts([1, 2])
    by_id = {r.match_id: r for r in results}
    assert by_id[1].ok and by_id[1].demo_url() == "http://replay407.valve.net/1422450/1_111.dem.bz2"
    assert not by_id[2].ok and by_id[2].result == "k_eResult_InvalidMatch"
    assert provider.quota_used_today() == 2


def test_not_configured_without_token(tmp_path, monkeypatch):
    _fake_helper(tmp_path, monkeypatch, SALTS_SCRIPT)
    monkeypatch.delenv(secrets.ENV_STEAM_REFRESH_TOKEN, raising=False)
    with pytest.raises(provider.GcNotConfigured):
        provider.fetch_salts([1])


def test_missing_helper(monkeypatch):
    monkeypatch.setattr(provider, "find_helper", lambda: None)
    secrets.store_token(provider.TOKEN_NAME, "rt")
    secrets.store_token(provider.USER_NAME, "u")
    with pytest.raises(provider.GcNotConfigured):
        provider.fetch_salts([1])
    assert os.environ.get(secrets.ENV_STEAM_PASSWORD) is None
