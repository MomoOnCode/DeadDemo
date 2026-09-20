import os

from deaddemo.core import secrets


def test_dotenv_does_not_override_environment(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text('DEADLOCK_API_KEY="from_file"\nexport DEADDEMO_STEAM_USER=alice\n# comment\nBROKEN\n')
    monkeypatch.setenv("DEADLOCK_API_KEY", "from_env")
    monkeypatch.delenv("DEADDEMO_STEAM_USER", raising=False)
    loaded = secrets.load_dotenv(env_file)
    assert env_file in loaded
    assert os.environ["DEADLOCK_API_KEY"] == "from_env"
    assert os.environ["DEADDEMO_STEAM_USER"] == "alice"


def test_token_round_trip_and_delete():
    p = secrets.store_token("unit_test", "tok-123")
    assert p.exists()
    assert secrets.load_token("unit_test") == "tok-123"
    raw = p.read_bytes()
    assert b"tok-123" not in raw or not raw.startswith(b"DPAPI1"), "DPAPI payloads never contain the plaintext"
    secrets.delete_token("unit_test")
    assert secrets.load_token("unit_test") is None


def test_token_store_lives_outside_repo(tmp_path):
    p = secrets.token_path("x")
    assert str(tmp_path) in str(p)  # DEADDEMO_HOME points at tmp_path in tests
