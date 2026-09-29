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

## Phase 1 — Foundations ✅
Built:
- **Core (pure):** UUIDv7 ids, injectable clock, digit normalization (fa/ar/latin), integer‑Rial money helpers, Jalali/Tehran time, Iranian plate parsing (car / motorcycle / free‑form, canonical key), permission catalogue with operator/supervisor/admin presets.
- **i18n:** `tr()` + `fa.json` (all UI text), bidi isolates (`ltr()`, `rtl()`), Persian display formatters (money, dates, durations, long Jalali date).
- **Config:** data‑root folders (§2.6), machine `settings.json` (role, node id, gate, server), DPAPI secret store, rotating logs.
- **Data:** SQLAlchemy models (nodes, gates, levels, settings, users, role presets, audit_log, shift_events), Alembic migration `0001_foundation` for SQLite and SQL Server, append‑only guard (ORM) + triggers (DB), automatic audit with old → new values, optimistic `row_version`, repositories, seed data from `resources/seed/site.json`.
- **Services:** app context bootstrap (`open_context`), scrypt auth, shifts, typed settings.
- **Design system:** tokens (dark/light from the brand palette), generated QSS, bundled Vazirmatn, Lucide icons recolored per theme, DWM dark title bar, follow‑Windows mode; widgets: Button, Card, TextField (digit normalization), Toast, dimmed+blurred ModalDialog, StatusLight, EmptyState, AlertBar, lazy DataTable, **PlateWidget** (painted Iranian plate, also used for receipts).
- **Shell:** first‑admin creation, login, forced password change, main window (right sidebar, collapsible), top bar (gate, link status, Jalali clock, user menu, theme toggle, search), Ctrl+K command palette, F1 shortcuts, screens: Home, Users & permissions (matrix + custom presets), Audit log, Settings (theme per user; mall name, gates, levels & capacities).

How to test: `scripts\check.ps1` (≈230 tests; DB tests run on SQLite **and** SQL Server LocalDB). Screenshots of both themes are written to `tests/artifacts/` by the UI tests / smoke.

Known gaps (planned later): link status is static until Phase 9 (sync); tariff, gate operations and every other module arrive in their phases.

## Phase 2 — Tariff engine + settings UI ✅
Built:
- `core/tariff`: `TariffValues` / `TariffVersion` / `TariffSchedule`, `ParkingCalendar` (hours per weekday, free weekdays, holidays), `compute_price()` → `PriceBreakdown` (entry fee, extra minutes, rounding, coupon, nights & fines, flags, receipt lines). Integer Rial only; 100 % branch coverage, every SPEC §4.5 example + Hypothesis properties (price never decreases with a longer stay, amounts are integers and rounded).
- Data: `tariff_versions` (versioned, audited) and `holidays` (migration `0002_tariffs`); calendar and price basis in settings; seeded from `resources/seed/site.json`.
- Service `tariff_service`: load schedule/calendar/basis, `TariffContext.quote()`, add/withdraw versions (no retroactive), save calendar, holidays.
- UI: **Tariffs & working hours** screen (permission *change tariffs*): tariff form + effective date/time + note → new version; history with status (current/future/past/withdrawn) and withdraw; working hours & free days per weekday; holidays with Jalali date picker; price calculator showing the breakdown and flags.
- Widgets: `MoneyField` (Persian digits, separators, int Rial), `JalaliDateEdit` + month popup (Saturday first), `TimeField`.

How to run/test: `scripts\check.ps1`. In the app: sidebar → «تعرفه و ساعات کاری».

Known gaps: none for this phase. Coupons and night‑fine exemptions are inputs to the engine; their management UIs arrive in Phases 4 and 7.

## Phase 3 — Gate operations (standalone, manual plate) ✅
Built:
- Core: ticket numbers `G-SSSSS-C` with Luhn; 20‑digit barcode payload (gate, sequence, entry minute, 5‑digit HMAC‑SHA256) — every single‑digit edit is rejected; own Code 128 set C encoder/decoder.
- Data (migration `0003_gate`): `active_sessions` (inside list), `visits` (history projection), append‑only `entry_events`, `exit_events`, `payments`, `adjustments`, `cancellations`, `debts`, `reprints`, `night_marks` (DB triggers), `gate_sequences`.
- `GateService`: entry (one session per plate, no‑plate debounce, categories), resolve by barcode / ticket number / plate, quote, exit with cash / card / mall‑card, manual amount and night‑fine change (permission + reason → adjustment event), fleeing → debt → collection (partial allowed), cancel entry / payment (+ corrected payment), duplicates, night marks + automatic overnight flag, occupancy per level, immutable counters.
- Receipts: 1‑bit renderer following SPEC §5.1 (logo, diamond divider or image, ad section only when an ad exists, plate frame, dotted‑leader rows, Code 128 with tree/wave art, ticket number), duplicate/motorcycle/no‑plate/training variants, exit receipt, security list of vehicles inside. **Tests scan the printed barcode with a real reader (zxing‑cpp).**
- Devices: printers (simulator → PNG, Windows driver via QPrinter, raw ESC/POS raster), scanners (keyboard‑wedge burst detection, serial COM, simulator).
- UI: **Gate screen** (entry lane with big plate input + vehicle type, exit lane with breakdown and payment buttons, inside / today / unidentified tabs with painted plates, occupancy bars, counters, debt banner, F‑keys F2–F12 + Enter/Space, scanner goes straight to exit), dialogs (reason, preview, already inside, lost ticket), Settings → **Receipt** (sections, art, texts, preview, reset) and **Hardware** (gate code, printer + test print, scanner + live scan test).

How to run/test: `scripts\check.ps1` (439 tests). In the app: sidebar → «درب ورود و خروج». Printed receipts (simulator) are saved in `<data root>\logs\printed`; test receipts in `tests/artifacts/receipt_*.png`.

Known gaps: subscriber / free‑access / blocklist banners arrive in Phase 4; coupons and receipt ads in Phase 7; cameras in Phase 8; offline cross‑gate exits in Phase 9. The owner must copy the approved logo to `<data root>eceipt\logo.png`.
