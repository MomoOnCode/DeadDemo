"""Remote console transports for a running Deadlock client.

* ``VConsoleClient``  – Valve's VConsole2 TCP protocol (launch the game with ``-vconsole -vconport N``).
  Chunks: 12-byte big-endian header ``>4sIHH`` (type, version 0x00D40000, total length, handle);
  ``CMND`` body is a NUL-terminated command; ``PRNT`` body is uint32 channel id, 24 opaque bytes,
  then NUL-terminated text.
* ``NetConClient``    – line-oriented telnet console (``-netconport N``).
* ``ConsoleLogTail``  – read-only tail of ``game/citadel/console.log`` (``-condebug``).

All expose ``send()``, ``wait_for()`` and ``query()`` so the director does not care which one is in use.
"""

from __future__ import annotations

import re
import socket
import struct
import threading
import time
from collections import deque
from collections.abc import Callable
from pathlib import Path

HEADER = struct.Struct(">4sIHH")
CMND_VERSION = 0x00D40000
DEFAULT_VCON_PORT = 29000


class ConsoleError(Exception):
    pass


def build_cmnd(cmd: str, version: int = CMND_VERSION) -> bytes:
    body = cmd.encode("utf-8")
    total = HEADER.size + len(body) + 1
    if total > 0xFFFF:
        raise ValueError("command too long")
    return HEADER.pack(b"CMND", version, total, 0) + body + b"\x00"


def parse_prnt(body: bytes) -> tuple[int, str]:
    if len(body) < 28:
        return 0, ""
    channel = struct.unpack(">I", body[:4])[0]
    text = body[28:].split(b"\x00", 1)[0].decode("utf-8", "replace")
    return channel, text


def parse_chan(body: bytes) -> dict[int, str]:
    out: dict[int, str] = {}
    rest = body[2:]
    for i in range(len(rest) // 58):
        o = i * 58
        cid = struct.unpack(">i", rest[o:o + 4])[0]
        out[cid] = rest[o + 24:o + 58].split(b"\x00", 1)[0].decode("utf-8", "replace")
    return out


class _LineBuffer:
    """Thread-safe line log with monotonically increasing indices."""

    def __init__(self, keep: int = 5000):
        self._lines: deque[tuple[int, str]] = deque(maxlen=keep)
        self._n = 0
        self._cv = threading.Condition()
        self.listeners: list[Callable[[str], None]] = []

    def push(self, text: str) -> None:
        for piece in text.replace("\r", "").split("\n"):
            if not piece:
                continue
            with self._cv:
                self._lines.append((self._n, piece))
                self._n += 1
                self._cv.notify_all()
            for fn in list(self.listeners):
                try:
                    fn(piece)
                except Exception:  # noqa: BLE001
                    pass

    @property
    def cursor(self) -> int:
        with self._cv:
            return self._n

    def wait_for(self, pattern: re.Pattern[str], since: int, timeout: float) -> re.Match[str] | None:
        deadline = time.monotonic() + timeout
        with self._cv:
            while True:
                for idx, line in self._lines:
                    if idx >= since:
                        m = pattern.search(line)
                        if m:
                            return m
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self._cv.wait(min(remaining, 0.5))

    def lines_since(self, since: int) -> list[str]:
        with self._cv:
            return [line for idx, line in self._lines if idx >= since]


class ConsoleBase:
    name = "base"

    def __init__(self) -> None:
        self.buffer = _LineBuffer()
        self.closed = threading.Event()

    # -- to implement ------------------------------------------------------------------
    def send(self, cmd: str) -> None:
        raise NotImplementedError

    def close(self) -> None:
        self.closed.set()

    # -- shared helpers ----------------------------------------------------------------
    @property
    def cursor(self) -> int:
        return self.buffer.cursor

    def wait_for(self, pattern: str | re.Pattern[str], timeout: float, since: int | None = None):
        rx = re.compile(pattern) if isinstance(pattern, str) else pattern
        return self.buffer.wait_for(rx, self.cursor if since is None else since, timeout)

    def query(self, cmd: str, pattern: str | re.Pattern[str], timeout: float = 5.0):
        """Send ``cmd`` and return the first later line matching ``pattern`` (or None)."""
        since = self.cursor
        self.send(cmd)
        return self.wait_for(pattern, timeout, since)

    def roundtrip(self, timeout: float = 5.0) -> bool:
        token = f"deaddemo-{int(time.time() * 1000) % 100000}"
        return self.query(f"echoln {token}", re.escape(token), timeout) is not None

    def lines_since(self, since: int) -> list[str]:
        return self.buffer.lines_since(since)


class VConsoleClient(ConsoleBase):
    name = "vconsole"

    def __init__(self, host: str = "127.0.0.1", port: int = DEFAULT_VCON_PORT):
        super().__init__()
        self.host, self.port = host, port
        self.sock: socket.socket | None = None
        self.channels: dict[int, str] = {}
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    def connect(self, timeout: float = 60.0, interval: float = 0.5) -> None:
        deadline = time.monotonic() + timeout
        last: Exception | None = None
        while time.monotonic() < deadline and not self.closed.is_set():
            try:
                self.sock = socket.create_connection((self.host, self.port), timeout=2.0)
                self.sock.settimeout(None)
                break
            except OSError as exc:
                last = exc
                time.sleep(interval)
        if self.sock is None:
            raise ConsoleError(f"no VConsole listener on {self.host}:{self.port} ({last})")
        self._thread = threading.Thread(target=self._reader, name="vconsole-reader", daemon=True)
        self._thread.start()

    def _recv_exact(self, n: int) -> bytes:
        assert self.sock is not None
        buf = bytearray(n)
        view = memoryview(buf)
        got = 0
        while got < n:
            k = self.sock.recv_into(view[got:], n - got)
            if k == 0:
                raise ConnectionError("VConsole closed")
            got += k
        return bytes(buf)

    def _reader(self) -> None:
        self.chunks_seen = 0
        self.close_reason = ""
        try:
            while not self.closed.is_set():
                mtype, _ver, length, _handle = HEADER.unpack(self._recv_exact(HEADER.size))
                self.chunks_seen += 1
                body = self._recv_exact(max(0, length - HEADER.size))
                if mtype == b"PRNT":
                    _cid, text = parse_prnt(body)
                    self.buffer.push(text)
                elif mtype == b"CHAN":
                    self.channels.update(parse_chan(body))
        except (OSError, ConnectionError, struct.error) as exc:
            self.close_reason = f"{type(exc).__name__}: {exc}"
        finally:
            self.closed.set()

    def send(self, cmd: str) -> None:
        if self.sock is None or self.closed.is_set():
            reason = getattr(self, "close_reason", "")
            seen = getattr(self, "chunks_seen", 0)
            raise ConsoleError(f"VConsole connection closed after {seen} chunk(s)"
                               + (f" ({reason})" if reason else "") + "; the game probably exited")
        with self._lock:
            self.sock.sendall(build_cmnd(cmd))

    def close(self) -> None:
        super().close()
        if self.sock is not None:
            try:
                self.sock.close()
            except OSError:
                pass


class NetConClient(ConsoleBase):
    name = "netcon"

    def __init__(self, host: str = "127.0.0.1", port: int = 29001):
        super().__init__()
        self.host, self.port = host, port
        self.sock: socket.socket | None = None
        self._lock = threading.Lock()

    def connect(self, timeout: float = 60.0, interval: float = 0.5) -> None:
        deadline = time.monotonic() + timeout
        last: Exception | None = None
        while time.monotonic() < deadline and not self.closed.is_set():
            try:
                self.sock = socket.create_connection((self.host, self.port), timeout=2.0)
                self.sock.settimeout(None)
                break
            except OSError as exc:
                last = exc
                time.sleep(interval)
        if self.sock is None:
            raise ConsoleError(f"no netcon listener on {self.host}:{self.port} ({last})")
        threading.Thread(target=self._reader, name="netcon-reader", daemon=True).start()

    def _reader(self) -> None:
        assert self.sock is not None
        pending = b""
        try:
            while not self.closed.is_set():
                chunk = self.sock.recv(4096)
                if not chunk:
                    break
                pending += chunk
                while b"\n" in pending:
                    line, pending = pending.split(b"\n", 1)
                    self.buffer.push(line.decode("utf-8", "replace"))
        except OSError:
            pass
        finally:
            self.closed.set()

    def send(self, cmd: str) -> None:
        if self.sock is None or self.closed.is_set():
            raise ConsoleError("netcon not connected")
        with self._lock:
            self.sock.sendall(cmd.encode("utf-8") + b"\n")

    def close(self) -> None:
        super().close()
        if self.sock is not None:
            try:
                self.sock.close()
            except OSError:
                pass


class ConsoleLogTail(ConsoleBase):
    """Read-only: follows console.log written by ``-condebug``. ``send`` raises."""

    name = "condebug"

    def __init__(self, path: Path):
        super().__init__()
        self.path = path
        self._pos = path.stat().st_size if path.exists() else 0
        threading.Thread(target=self._poll, name="console-log-tail", daemon=True).start()

    def _poll(self) -> None:
        while not self.closed.is_set():
            try:
                if self.path.exists():
                    size = self.path.stat().st_size
                    if size < self._pos:
                        self._pos = 0
                    if size > self._pos:
                        with self.path.open("rb") as fh:
                            fh.seek(self._pos)
                            data = fh.read(size - self._pos)
                        self._pos = size
                        self.buffer.push(data.decode("utf-8", "replace"))
            except OSError:
                pass
            time.sleep(0.25)

    def send(self, cmd: str) -> None:
        raise ConsoleError("console.log is read-only; launch with -vconsole to send commands")
