import pytest


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    """Point all app directories at a temp folder so tests never touch real user data."""
    monkeypatch.setenv("DEADDEMO_HOME", str(tmp_path / "home"))
    yield
