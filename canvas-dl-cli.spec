# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the canvas-dl command-line tool (one-file, console)."""

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

datas = []
datas += collect_data_files("certifi")
datas += collect_data_files("fpdf")  # core font metrics shipped inside fpdf2
datas += collect_data_files("pypdf")

hiddenimports = []
hiddenimports += collect_submodules("fpdf")
hiddenimports += collect_submodules("pypdf")

a = Analysis(
    ["packaging/entry_cli.py"],
    pathex=["src"],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "customtkinter"],  # CLI doesn't need the GUI toolkit
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="canvas-dl",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
