# -*- mode: python ; coding: utf-8 -*-
"""One-file windowed Keyware.exe. Build it on Windows with build.py."""

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

datas = collect_data_files("customtkinter")
datas += [("ui/monochrome.json", "ui"), ("ui/assets", "ui/assets")]

hidden = [
    "pynput.keyboard._win32",
    "pynput.mouse._win32",
    "pynput._util.win32",
    "pynput._util.win32_vks",
    "pystray._win32",
    "win32gui",
    "win32api",
    "win32con",
    "win32process",
    "PIL.Image",
    "PIL.ImageDraw",
    "PIL.ImageTk",
    "assist.ai_binder",
    "assist.secret_store",
    "assist.feedback",
    "assist.web_lookup",
]
hidden += collect_submodules("customtkinter")

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pytest"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    name="Keyware",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    icon="ui/assets/keyware.ico",
    disable_windowed_traceback=False,
)
