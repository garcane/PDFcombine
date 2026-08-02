# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller build specification for PDF Toolkit.

Build with:

    pyinstaller pdf_toolkit.spec --noconfirm --clean

The result is ``dist/PDF Toolkit.exe`` - a single file that runs on a machine
without Python installed.
"""

from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files

SPEC_DIR = Path(SPECPATH)  # noqa: F821 - injected by PyInstaller

# CustomTkinter ships its themes as data files and tkinterdnd2 ships the
# compiled tkdnd Tcl library; both must be bundled explicitly.
datas = [
    (str(SPEC_DIR / "assets"), "assets"),
]
datas += collect_data_files("customtkinter")
datas += collect_data_files("tkinterdnd2")

a = Analysis(  # noqa: F821
    ["app.py"],
    pathex=[str(SPEC_DIR)],
    binaries=[],
    datas=datas,
    hiddenimports=["PIL._tkinter_finder", "pypdfium2"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["matplotlib", "numpy", "pytest", "tkinter.test"],
    noarchive=False,
)

pyz = PYZ(a.pure)  # noqa: F821

exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="PDF Toolkit",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,          # GUI application - no console window
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(SPEC_DIR / "assets" / "app.ico"),
)
