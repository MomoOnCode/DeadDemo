import socket
import struct
import threading

from deaddemo.core.db.database import Database
from deaddemo.core.db.repos import MatchRepo
from deaddemo.core.video.console import HEADER, VConsoleClient, build_cmnd, parse_chan, parse_prnt
from deaddemo.core.video.launcher import ConfigGuard, GameInfoUnlock, LaunchOptions
from deaddemo.core.video.sequences import GenerateOptions, Sequence, SequenceRepo, VideoRepo, _merge, generate


def test_cmnd_packet_matches_reference_layout():
    pkt = build_cmnd("echo hi")
    assert pkt[:4] == b"CMND"
    assert pkt[4:8] == bytes([0x00, 0xD4, 0x00, 0x00])
    assert struct.unpack(">H", pkt[8:10])[0] == len(pkt) == 12 + len("echo hi") + 1
    assert pkt.endswith(b"echo hi\x00")


def test_prnt_and_chan_parsing():
    body = struct.pack(">I", 7) + b"\x00" * 24 + b"hello world\x00garbage"
    assert parse_prnt(body) == (7, "hello world")
    rec = struct.pack(">iiiiiI", 42, 0, 0, 0, 0, 0) + b"Console".ljust(34, b"\x00")
    assert parse_chan(struct.pack(">H", 1) + rec) == {42: "Console"}


def test_vconsole_client_against_fake_server():
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    received: list[bytes] = []

    def serve():
        conn, _ = srv.accept()
        # handshake burst: a CHAN chunk, then echo whatever CMND arrives back as PRNT
        rec = struct.pack(">iiiiiI", 1, 0, 0, 0, 0, 0) + b"Console".ljust(34, b"\x00")
        chan_body = struct.pack(">H", 1) + rec
        conn.sendall(HEADER.pack(b"CHAN", 0xD40000, 12 + len(chan_body), 0) + chan_body)
        hdr = conn.recv(12)
        _t, _v, length, _h = HEADER.unpack(hdr)
        body = conn.recv(length - 12)
        received.append(body)
        cmd = body.rstrip(b"\x00").decode()
        text = cmd.split(" ", 1)[1] if cmd.startswith("echoln ") else cmd
        prnt = struct.pack(">I", 1) + b"\x00" * 24 + text.encode() + b"\n\x00"
        conn.sendall(HEADER.pack(b"PRNT", 0xD40000, 12 + len(prnt), 0) + prnt)
        conn.close()

    threading.Thread(target=serve, daemon=True).start()
    c = VConsoleClient(port=port)
    c.connect(timeout=5)
    assert c.roundtrip(timeout=5)
    assert received and received[0].startswith(b"echoln deaddemo-")
    assert c.channels == {1: "Console"}
    c.close()


def test_gameinfo_unlock_restores_bytes(tmp_path):
    gi = tmp_path / "gameinfo.gi"
    # the retail file mixes CRLF and LF; the patch must only flip the one digit
    original = b"Engine2\r\n{\n\tDefensiveConCommands 1\r\n\tDisableLoadingPlaque 1\n}\r\n"
    gi.write_bytes(original)
    with GameInfoUnlock(gi) as u:
        assert u.patched
        assert gi.read_bytes() == original.replace(b"DefensiveConCommands 1", b"DefensiveConCommands 0")
        assert u.backup.exists()
    assert gi.read_bytes() == original and not u.backup.exists()
    assert u.restore() is True  # idempotent


def test_config_guard_restores_engine_files(tmp_path):
    cfg = tmp_path / "cfg"
    cfg.mkdir()
    video = cfg / "video.txt"
    video.write_bytes(b'"setting.defaultres" "2560"\r\n')
    guard = ConfigGuard(tmp_path).snapshot()
    assert guard.changed() == []
    video.write_bytes(b'"setting.defaultres" "1280"\r\n')  # the engine saved our -w/-h
    (cfg / "machine_convars.vcfg").write_bytes(b'"fps_max" "0"')  # did not exist before
    assert sorted(guard.changed()) == ["cfg/machine_convars.vcfg", "cfg/video.txt"]
    restored = guard.restore()
    assert sorted(restored) == ["cfg/machine_convars.vcfg", "cfg/video.txt"]
    assert video.read_bytes() == b'"setting.defaultres" "2560"\r\n'
    assert not (cfg / "machine_convars.vcfg").exists()
    assert guard.changed() == [] and guard.restore() == []


def test_launch_args():
    a = LaunchOptions(width=1920, height=1080, vcon_port=29005, netcon_port=29006).args()
    assert "-insecure" in a and "-vconsole" in a and a[a.index("-vconport") + 1] == "29005"
    assert "-dev" not in a
    assert a[a.index("-netconport") + 1] == "29006" and "-windowed" in a and "-noborder" in a
    b = LaunchOptions(width=1920, height=1080, borderless=False).args()
    assert "-noborder" not in b and b[b.index("-w") + 1] == "1920"


def test_merge_ranges():
    merged = _merge([(10, 20, "a"), (18, 25, "b"), (40, 50, "c")], gap=2.0)
    assert merged == [(10, 25, "a + b"), (40, 50, "c")]


def test_generate_and_persist_sequences():
    db = Database.open_memory()
    repo = MatchRepo(db)
    repo.store_parse_result(
        match_row={"match_id": 1, "tick_rate": 64, "game_start_tick": 1000, "regulation_seconds": 600.0},
        players=[{"hero_id": 7, "steam_id": 76561198314737511, "player_name": "me", "team_num": 2},
                 {"hero_id": 9, "steam_id": 2, "player_name": "foe", "team_num": 3}],
        kills=[
            {"tick": 1000 + 64 * 100, "match_seconds": 100.0, "attacker_hero_id": 7, "victim_hero_id": 9,
             "assister_hero_ids": []},
            {"tick": 1000 + 64 * 104, "match_seconds": 104.0, "attacker_hero_id": 7, "victim_hero_id": 9,
             "assister_hero_ids": []},
            {"tick": 1000 + 64 * 300, "match_seconds": 300.0, "attacker_hero_id": 9, "victim_hero_id": 7,
             "assister_hero_ids": []},
        ],
        item_purchases=[], objective_events=[
            {"tick": 1000 + 64 * 400, "match_seconds": 400.0, "objective_type": "walker", "team_num": 3, "lane": 1}],
    )
    match = repo.get(1)
    seqs = generate(db, match, 7, GenerateOptions(kinds=("kills", "multikills", "deaths", "objectives"),
                                                  lead_in_s=5, lead_out_s=2), lambda h: f"H{h}")
    labels = [s.label for s in seqs]
    assert any("2K" in lbl for lbl in labels), labels
    assert any("Death" in lbl for lbl in labels)
    assert any("Walker" in lbl for lbl in labels)
    assert seqs[0].start_s == 95.0 and seqs[0].start_tick(match) == 1000 + 64 * 95
    assert seqs[0].focus_account_id == 354471783
    srepo = SequenceRepo(db)
    srepo.replace_all(1, seqs)
    back = srepo.for_match(1)
    assert len(back) == len(seqs) and back[0].id is not None and back[0].label == seqs[0].label
    added = srepo.add(Sequence(1, 10, 20, "manual"))
    assert added.id and len(srepo.for_match(1)) == len(seqs) + 1
    srepo.delete(added.id)
    vids = VideoRepo(db)
    vid = vids.add(1, "C:/x.mp4", [back[0].id], 1920, 1080, 60, "engine", 9.5)
    assert vids.all()[0].id == vid and vids.all()[0].sequence_ids == [back[0].id]
