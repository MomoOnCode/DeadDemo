"""deaddemo.core must stay importable without Qt: it runs in parse subprocesses and the CLI."""

import importlib
import pkgutil
import sys

import pytest

import deaddemo.core


class _BlockQt:
    def find_spec(self, name, path=None, target=None):
        if name == "PySide6" or name.startswith("PySide6."):
            raise ImportError("PySide6 is not allowed inside deaddemo.core")
        return None


def test_core_modules_do_not_import_qt(monkeypatch):
    for mod in [m for m in sys.modules if m.startswith("deaddemo.core")]:
        monkeypatch.delitem(sys.modules, mod, raising=False)
    monkeypatch.setattr(sys, "meta_path", [_BlockQt(), *sys.meta_path])
    failures = []
    for info in pkgutil.walk_packages(deaddemo.core.__path__, prefix="deaddemo.core."):
        try:
            importlib.import_module(info.name)
        except ImportError as exc:
            if "PySide6" in str(exc):
                failures.append(f"{info.name}: {exc}")
    if failures:
        pytest.fail("\n".join(failures))
