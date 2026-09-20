# PyInstaller spec: `uv run pyinstaller packaging/deaddemo.spec` -> dist/DeadDemo/DeadDemo.exe
# One-dir build; boon's native extension and PySide6 plugins are collected explicitly.
from PyInstaller.utils.hooks import collect_all, collect_submodules

block_cipher = None

datas, binaries, hiddenimports = [], [], []
for pkg in ("boon", "polars", "zstandard"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h
hiddenimports += collect_submodules("deaddemo")
datas += [("../src/deaddemo/core/db/schema.sql", "deaddemo/core/db")]

a = Analysis(
    ["../src/deaddemo/app.py"],
    pathex=["../src"],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "IPython", "jupyter"],
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="DeadDemo",
    debug=False,
    console=False,
    icon=None,
)
coll = COLLECT(exe, a.binaries, a.zipfiles, a.datas, strip=False, upx=False, name="DeadDemo")
