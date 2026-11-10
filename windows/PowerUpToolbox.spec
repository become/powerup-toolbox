# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for the Windows portable (onedir) build.
# Run via windows/build_windows.ps1 (it sets POWERUP_REPO_ROOT).
import os
from PyInstaller.utils.hooks import collect_submodules, collect_all

ROOT = os.environ.get("POWERUP_REPO_ROOT") or os.path.abspath(os.path.join(SPECPATH, ".."))
WIN = os.path.join(ROOT, "windows")

datas = [
    (os.path.join(ROOT, "index.html"), "."),
    (os.path.join(ROOT, "assets"), "assets"),
    (os.path.join(WIN, "VERSION"), "."),
]

hiddenimports = ["win_support", "server", "xlrd", "openpyxl", "PIL", "PIL.Image"]
for pkg in ("docx", "pptx", "xlrd"):
    try:
        hiddenimports += collect_submodules(pkg)
    except Exception:
        pass

binaries = []
for pkg in ("pypdfium2", "pypdfium2_raw", "msoffcrypto", "olefile", "cryptography"):   # PDF preview (pdfium) + password-protected Office files
    try:
        d, b, h = collect_all(pkg)
        datas += d; binaries += b; hiddenimports += h
    except Exception:
        pass

a = Analysis(
    [os.path.join(WIN, "launcher.py")],
    pathex=[ROOT, WIN],           # ROOT gives server.py and the bundled xlrd/ package
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "torch", "torchvision", "cv2", "numpy", "scipy", "matplotlib", "pandas",
              "spandrel", "IPython", "pytest"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="PowerUpToolbox",
    debug=False,
    strip=False,
    upx=False,
    console=False,                 # --windowed
    icon=os.path.join(ROOT, "build_win", "app_multi.ico"),
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="PowerUpToolbox")
