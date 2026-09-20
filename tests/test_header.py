from deaddemo.core.parse.header import client_version_from_header


def test_client_version_from_header(tmp_path):
    p = tmp_path / "x.dem"
    p.write_bytes(b"PBDEMS2\0" + b"\0" * 40 + b"/opt/srcds/deadlock/citadel_v6686/citadel" + b"\0" * 100)
    assert client_version_from_header(p) == 6686


def test_client_version_missing(tmp_path):
    p = tmp_path / "x.dem"
    p.write_bytes(b"PBDEMS2\0" + b"\0" * 200)
    assert client_version_from_header(p) is None
