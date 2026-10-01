# Caspian Parking

Windows desktop parking‑management application (Python 3.12 + PySide6 + SQLite / SQL Server): gate entry and
exit with thermal receipts, subscribers and shop wallets, reports, advertising and coupons, cameras / ANPR,
multi‑gate sync with a central SQL Server, barrier / RFID / card terminal / LED sign.

- Requirements: [`docs/SPEC.md`](docs/SPEC.md)
- Plan and status: [`docs/PLAN.md`](docs/PLAN.md), [`docs/PROGRESS.md`](docs/PROGRESS.md)
- Decisions: [`docs/DECISIONS.md`](docs/DECISIONS.md)
- Site installation (Persian): [`docs/SITE_SETUP.md`](docs/SITE_SETUP.md)
- User manual (Persian): [`docs/USER_MANUAL_FA.md`](docs/USER_MANUAL_FA.md)
- ANPR engine plug‑ins: [`docs/ANPR_PLUGINS.md`](docs/ANPR_PLUGINS.md)
- Licences: [`docs/THIRD_PARTY_LICENSES.md`](docs/THIRD_PARTY_LICENSES.md)

## Development

```powershell
powershell -ExecutionPolicy Bypass -File scripts\setup.ps1   # .venv + pinned dependencies
powershell -ExecutionPolicy Bypass -File scripts\check.ps1   # ruff, mypy, tests (SQLite + LocalDB), smoke launch
powershell -ExecutionPolicy Bypass -File scripts\run.ps1     # start the app
powershell -ExecutionPolicy Bypass -File scripts\perf.ps1    # 1,000,000-visit benchmarks (SPEC §2.4)
```

## Building the installer

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build.ps1   # gate → PyInstaller → smoke → Inno Setup → install test
```

Output: `dist\installer\<AppFolder>-Setup-<version>.exe` and `version.json` (copy both to the update share).
Needs Inno Setup 6 (found automatically, or pass `-Iscc <path to ISCC.exe>`).
