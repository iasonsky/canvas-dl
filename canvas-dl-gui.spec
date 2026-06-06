# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the canvas-dl desktop GUI (one-dir, windowed)."""

from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_submodules

datas, binaries, hiddenimports = [], [], []

# customtkinter ships JSON themes + .otf fonts that must travel with the app.
ctk_datas, ctk_binaries, ctk_hidden = collect_all("customtkinter")
datas += ctk_datas
binaries += ctk_binaries
hiddenimports += ctk_hidden

datas += collect_data_files("certifi")
datas += collect_data_files("fpdf")
datas += collect_data_files("pypdf")
hiddenimports += collect_submodules("fpdf")
hiddenimports += collect_submodules("pypdf")

# Some Python distributions (notably the standalone builds used by uv/pyenv) ship
# Tcl/Tk 9 whose shared libraries PyInstaller's tkinter hook does not always
# bundle, causing a "libtcl9.0.so: cannot open shared object file" crash. Pull in
# any libtcl*/libtk* next to the interpreter so the GUI binary is self-contained.
# Harmless on CI runners where the hook already bundles Tcl/Tk 8.6.
import glob as _glob
import os as _os
import sys as _sys

for _prefix in {_sys.base_prefix, _sys.prefix}:
    _libdir = _os.path.join(_prefix, "lib")
    for _pat in ("libtcl*.so*", "libtk*.so*", "libtcl*.dylib", "libtk*.dylib"):
        for _lib in _glob.glob(_os.path.join(_libdir, _pat)):
            binaries.append((_lib, "."))

a = Analysis(
    ["packaging/entry_gui.py"],
    pathex=["src"],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="canvas-dl-gui",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,  # windowed app: no console window on Windows/macOS
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="canvas-dl-gui",
)
