from pathlib import Path

from deaddemo.core.steam.vdf import parse_vdf

FIXTURES = Path(__file__).parent / "fixtures"


def test_parse_libraryfolders():
    data = parse_vdf((FIXTURES / "libraryfolders.vdf").read_text())
    libs = data["libraryfolders"]
    assert libs["0"]["path"] == r"C:\Program Files (x86)\Steam"
    assert libs["1"]["path"] == r"D:\SteamLibrary"
    assert libs["1"]["apps"]["1422450"] == "40000000000"


def test_escapes_and_comments():
    data = parse_vdf('// comment\n"root" { "k" "a\\"b" // trailing\n "n" { } }')
    assert data == {"root": {"k": 'a"b', "n": {}}}


def test_unquoted_tokens():
    assert parse_vdf('root { key value }') == {"root": {"key": "value"}}
