from pathlib import Path

from deaddemo.core.steam import locator

FIXTURES = Path(__file__).parent / "fixtures"


def _fake_steam(tmp_path: Path) -> Path:
    root = tmp_path / "Steam"
    (root / "config").mkdir(parents=True)
    (root / "config" / "loginusers.vdf").write_text((FIXTURES / "loginusers.vdf").read_text())
    lib = tmp_path / "Lib"
    (lib / "steamapps" / "common" / "Deadlock" / "game" / "citadel" / "replays").mkdir(parents=True)
    (lib / "steamapps" / "appmanifest_1422450.acf").write_text('"AppState" { "appid" "1422450" }')
    (lib / "steamapps" / "common" / "Deadlock" / "game" / "citadel" / "steam.inf").write_text(
        "ClientVersion=6698\nServerVersion=6698\nProductName=citadel\n"
    )
    vdf = (FIXTURES / "libraryfolders.vdf").read_text().replace(
        r"D:\\SteamLibrary", str(lib).replace("\\", "\\\\")
    )
    (root / "config" / "libraryfolders.vdf").write_text(vdf)
    return root


def test_logged_in_account_prefers_autologin(tmp_path):
    root = _fake_steam(tmp_path)
    acct = locator.find_logged_in_account(root)
    assert acct is not None
    assert acct.steam_id64 == 76561198314737511
    assert acct.account_id == 354471783
    assert acct.persona_name == "Bark 4 Me Howl 4 U"


def test_detect_finds_deadlock_and_build(tmp_path):
    root = _fake_steam(tmp_path)
    libs = locator.read_library_folders(root)
    dl = locator.find_deadlock_dir(libs)
    assert dl is not None and dl.name == "Deadlock"
    assert locator.read_client_build(dl) == 6698
    assert [s for s, _ in locator.replay_dirs(dl)] == ["game"]
