# PyInstaller spec — onedir build of the app (SPEC §7). Run through scripts\build.ps1.
# onedir keeps the Qt DLLs as separate, replaceable files (LGPL relinking, see THIRD_PARTY_LICENSES.md).
# ruff: noqa
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata

ROOT = Path(SPECPATH).parent
SRC = ROOT / "src"
PKG = SRC / "caspian_parking"

datas = [
    (str(PKG / "resources"), "caspian_parking/resources"),
    (str(PKG / "i18n" / "fa.json"), "caspian_parking/i18n"),
    # Alembic reads the migration scripts from files, so they ship as plain files too
    (str(PKG / "data" / "migrations"), "caspian_parking/data/migrations"),
]
datas += collect_data_files("tzdata")
datas += collect_data_files("jdatetime")
datas += copy_metadata("APScheduler")  # APScheduler finds its triggers through package metadata

hiddenimports = collect_submodules("caspian_parking")
hiddenimports += collect_submodules("apscheduler")
hiddenimports += [
    "sqlalchemy.dialects.sqlite",
    "sqlalchemy.dialects.mssql.pyodbc",
    "win32timezone",
    "win32serviceutil",
    "servicemanager",
]

excludes = [
    "tests",
    "pytest",
    "hypothesis",
    "zxingcpp",
    "mypy",
    "ruff",
    "tkinter",
    "PySide6.QtWebEngineCore",
    "PySide6.QtWebEngineWidgets",
    "PySide6.QtQuick",
    "PySide6.QtQml",
    "PySide6.Qt3DCore",
    "PySide6.QtMultimedia",
]

a = Analysis(
    [str(PKG / "__main__.py")],
    pathex=[str(SRC)],
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=excludes,
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="parking",
    icon=str(ROOT / "build" / "app.ico"),
    console=False,
    version=str(ROOT / "build" / "version_info.txt"),
)
coll = COLLECT(exe, a.binaries, a.datas, name="parking")
