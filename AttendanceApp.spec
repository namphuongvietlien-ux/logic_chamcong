# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec: onedir build for CustomTkinter + EasyOCR (Windows)."""

import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_data_files

datas = []
binaries = []
hiddenimports = [
    "PIL._tkinter_finder",
    "tkinter",
    "customtkinter",
    "easyocr",
    "cv2",
    "skimage",
    "scipy",
    "nltk",
    "multiprocessing",
    "correction_ui",
    "employee_ui",
    "holiday_ui",
    "leave_ui",
    "dashboard_ui",
    "history_ui",
    "date_picker",
    "tkcalendar",
    "babel",
    "babel.numbers",
    "babel.dates",
    "babel.localtime",
    "pandas",
    "openpyxl",
    "openpyxl.cell._writer",
    "sqlite3",
    "tree_theme",
    "processing",
    "processing.ocr_processor",
    "processing.pipeline",
    "processing.validation",
    "processing.sessions",
    "processing.corrections",
    "processing.folders",
    "processing.master_data",
    "processing.excel_processor",
    "processing.merger",
    "processing.cong_rules",
    "processing.reports",
    "processing.report_k9",
    "processing.report_cham_cong",
    "processing.report_individual",
    "processing.period",
    "processing.output_files",
    "processing.utils",
    "processing.resources",
    "processing.database",
    "processing.holidays",
    "processing.leave",
    "processing.excel_locale",
    "processing.variance",
    "processing.history_import",
]

for pkg in ("customtkinter", "easyocr", "torch", "torchvision", "cv2"):
    try:
        pkg_datas, pkg_binaries, pkg_hidden = collect_all(pkg)
        datas += pkg_datas
        binaries += pkg_binaries
        hiddenimports += pkg_hidden
    except Exception:
        pass

datas += collect_data_files("customtkinter")
datas += collect_data_files("babel")
try:
    import customtkinter as _customtkinter

    _ctk_dir = str(Path(_customtkinter.__file__).resolve().parent)
    datas.append((_ctk_dir, "customtkinter"))
except Exception:
    pass

easyocr_home = Path.home() / ".EasyOCR"
if easyocr_home.exists():
    datas.append((str(easyocr_home), ".EasyOCR"))

_spec_dir = Path(SPECPATH) if "SPECPATH" in dir() else Path(".")
# Required layouts: packed into _MEIPASS so end users never copy them.
_required_templates = (
    "Template_Cham_Cong.xlsx",
    "K9 08.2026.xlsx",
)
for _name in _required_templates:
    _file = _spec_dir / _name
    if not _file.exists():
        raise SystemExit(f"Missing required bundled template: {_name}")
    datas.append((str(_file.resolve()), "."))
for _name in (
    "Mau_Cham_Cong.xlsx",
    "CHẤM CÔNG THÁNG 09.xlsx",
):
    _file = _spec_dir / _name
    if _file.exists():
        datas.append((str(_file.resolve()), "."))

a = Analysis(
    ["app.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pytest", "unittest"],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="AttendanceApp",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    # Hide the black CMD window behind the GUI (keep False for end users).
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
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
    name="AttendanceApp",
)
