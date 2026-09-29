# CLAUDE.md — working rules for this repository

You are building **Caspian Parking**, a Windows desktop parking-management app (Python 3.12 + PySide6 + SQLite/SQL Server).
The full requirements are in **`docs/SPEC.md`** — read it completely before planning. This file tells you *how* to work.

## Operating mode: autonomous, phase by phase
- The owner is not a professional developer. **Do not ask for approval between phases.** Plan once, then execute every phase in order until Phase 11 is done.
- Only stop and ask when truly blocked: a credential or login only the owner can do (e.g., `gh auth login` in the browser, a Windows UAC prompt), missing hardware information, or an irreversible decision not covered by the spec. Otherwise pick the simplest robust option, write it in `docs/DECISIONS.md`, and continue.
- When you need a permission (running a command, installing software), request it and carry on once granted.
- If a session ends or context is reset: read `docs/PROGRESS.md`, `docs/PLAN.md`, `docs/DECISIONS.md`, `git log -20`, then continue from the next unchecked task.

## Loop for every phase
1. Mark the phase "in progress" in `docs/PLAN.md`.
2. Implement in small steps. Commit locally after each meaningful step.
3. Write/extend tests alongside the code.
4. Run the quality gate: `scripts\check.ps1` (ruff + pytest offscreen + smoke launch). Fix until green. Never skip or weaken tests to make them pass.
5. Update `docs/PROGRESS.md` (built, how to run/test, known gaps) and tick tasks in `docs/PLAN.md`.
6. `git commit` (Conventional Commits, e.g. `feat(tariff): per-minute pricing with round-up`), `git push`, `git tag phase-N && git push --tags`.
7. Immediately start the next phase.
- Only **push when the gate is green.** If something cannot be finished, push nothing broken: leave it on a branch `wip/<topic>`, note it in PROGRESS.md, and continue with what is possible.

## Git & GitHub
- Repo: **private** GitHub repo `caspian-parking` under the owner's account (create with `gh repo create caspian-parking --private --source . --push`).
- Never force-push to `main`. Never rewrite published history.
- Never commit secrets, databases, photos, backups, `.env`, HMAC keys, or build output. Maintain `.gitignore` accordingly.

## Code rules
- Package layout: `src/caspian_parking/` with sub-packages `core` (domain, pure logic: tariff, tickets, barcode, subscriptions), `data` (models, repositories, migrations, sync), `devices` (interfaces + simulators), `services` (scheduler, backup, reports, watch mode), `ui` (theme, widgets, screens), `i18n`, `resources`. Tests in `tests/` mirror the package.
- Domain logic stays pure and UI-free, so it is unit-testable.
- **Money = integer Rial, always.** No floats in money paths. Time stored in UTC, displayed in Jalali Tehran time.
- **Event tables are append-only.** No DELETE/UPDATE on money or traffic events; corrections are new events with a reason.
- All IDs are UUIDv7. All reference-data changes write `audit_log`.
- No brand strings in code ("Caspian", gate names, prices…) — they come from settings/seed data.
- All UI strings through `i18n` (Persian). Wrap LTR runs with the `bidi` helpers. Plates only via `PlateWidget` / receipt plate renderer.
- No colors/sizes hard-coded in widgets — use theme tokens. Both dark and light themes must look finished on every screen.
- Long-running work never blocks the UI thread.
- Every hardware device behind an interface with a simulator; the app must run end-to-end with simulators only.
- Keep dependencies licensed for closed-source commercial use; log them in `docs/THIRD_PARTY_LICENSES.md`.

## Verification habits
- Tariff, ticket number/Luhn, barcode payload/HMAC, subscription dates (incl. negative days), sync merge and fiscal-year close need exhaustive unit tests, including every example in SPEC §4.5.
- UI: pytest-qt tests for main flows (entry → print preview → exit → payment) using simulators; render receipts to PNG in `tests/artifacts/` and assert size/layout basics.
- Performance: from Phase 5 onward keep `scripts/seed_bigdata.py` (1,000,000 visits) and a benchmark test for the targets in SPEC §2.4.

## Windows environment
- Development and target OS: Windows 10/11 x64. Use PowerShell scripts in `scripts\` (`setup.ps1`, `check.ps1`, `run.ps1`, `build.ps1`).
- Use a virtual environment `.venv`. Pin versions in `requirements.txt` / `requirements-dev.txt`.
