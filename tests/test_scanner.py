from deaddemo.core.db.database import Database
from deaddemo.core.db.repos import DemoRepo
from deaddemo.core.replays import scanner


def test_scan_detects_partial_and_bad_magic(tmp_path):
    d = tmp_path / "replays"
    d.mkdir()
    (d / "123456.dem.partial").write_bytes(b"\0" * 10)
    (d / "654321.dem").write_bytes(b"not a demo")
    found = scanner.scan([("game", d)])
    by_name = {f.path.name: f for f in found}
    assert by_name["123456.dem.partial"].is_partial and by_name["123456.dem.partial"].match_id == 123456
    assert by_name["654321.dem"].status == "error"


def test_sync_marks_missing(tmp_path):
    d = tmp_path / "replays"
    d.mkdir()
    (d / "1.dem").write_bytes(b"x")
    db = Database.open_memory()
    repo = DemoRepo(db)
    report = scanner.sync_to_db(repo, scanner.scan([("game", d)]))
    assert report.added == 1
    (d / "1.dem").unlink()
    report = scanner.sync_to_db(repo, scanner.scan([("game", d)]))
    assert report.missing == 1
