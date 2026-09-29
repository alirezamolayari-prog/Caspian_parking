# Progress

Resume rule: read this file, `docs/PLAN.md`, `docs/DECISIONS.md` and `git log -20`, then continue from the first unchecked task in PLAN.md.

## How to run
```powershell
powershell -ExecutionPolicy Bypass -File scripts\setup.ps1   # once: Python 3.12 x64 venv + dependencies
powershell -ExecutionPolicy Bypass -File scripts\check.ps1   # quality gate: ruff + mypy + pytest (offscreen) + smoke launch
powershell -ExecutionPolicy Bypass -File scripts\run.ps1     # start the app
```

## Phase 0 — Environment, repo, scripts, skeleton ✅
- Python 3.12.13 x64 venv (`.venv`, via uv), pinned `requirements.txt` / `requirements-dev.txt`.
- `pyproject.toml` (src layout, ruff, pytest markers, lenient mypy), package skeleton `src/caspian_parking/{core,data,devices,services,ui,i18n,resources}`.
- `python -m caspian_parking [--smoke]` opens a placeholder RTL window; `--smoke` exits 0 after it is shown.
- Scripts: `setup.ps1`, `check.ps1`, `run.ps1`, `build.ps1` (packaging arrives in Phase 11).
- Tests: `tests/test_smoke.py` (window builds offscreen, smoke subprocess exits 0).
- Known gaps: none for this phase.
