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

Known gaps: subscriber / free‑access / blocklist banners arrive in Phase 4; coupons and receipt ads in Phase 7; cameras in Phase 8; offline cross‑gate exits in Phase 9. The owner must copy the approved logo to `<data root>\receipt\logo.png`.

## Phase 4 — Subscribers, shops, wallets, free access, blocklist ✅
Built:
- Core `subscriptions`: status lights (green / amber ≤ 5 days / red ≤ 48 h / black), days left incl. negative, early renewal, negative subscription with distinct‑day deduction, concurrency rule, follow‑up order (100 % coverage). Tariff engine gained `covered` visits (no fee, night fines still apply).
- Data (migration `0004_people`): `shops`, `wallet_transactions`, `people`, `person_plates`, `subscription_payments`, `guest_permits`, `blocks`, `block_attempts`, plus `person_id` on sessions, entries and visits.
- Services: `PeopleService` (subscribers, plates, payments with preview, negative permission, night exemption, shops, wallet deposits / statement / low balance / auto renewal, free access + guest permits), `BlocklistService`, identification of a plate at the gate, follow‑up list with RTL Excel/Word export, Excel import with a validation report (template in `resources/templates/subscribers_import.xlsx`).
- Gate: identity banner while typing (light, name, shop, days left, negative, expired, free access, concurrency, blocked), full‑screen red alarm + beep for blocked plates (attempt logged), no paper ticket for covered visits (setting), subscriber exits free except night fines.
- UI: **Subscribers** (list with lights, profile, plates, pay dialog with new end date and deducted days, follow‑up tab with exports, import tab), **Shops & wallets**, **Free access**, **Blocklist** (register, active list with hidden security details, attempts, unblock with reason).

How to run/test: `scripts/check.ps1` (≈ 520 tests; the suite now runs in parallel with pytest‑xdist and the timing tests run alone afterwards).

Known gaps: coupon purchase from wallets arrives with the advertising module (Phase 7); the automatic wallet renewal is a button until the scheduler (Phase 5).

## Phase 5 — Reports, dashboard, automatic daily report ✅
Built:
- Report framework (`services/reports`): parameters (Jalali range, gate, operator, category, text) → result (sections, KPIs, totals, chart) → Excel / Word / PDF (Qt `QPdfWriter`) / print. Reports run on a worker thread.
- Reports delivered now (SPEC §4.12 numbers): 1 financial (cash / card / mall‑card; parking, debt recovery, subscriptions, wallet deposits; per gate and operator), 2 subscriptions, 3 subscriber traffic, 4 plate history + frequent visitors, 5 free access, 6 pass‑through, 7 fleeing & debts, 8 cancellations, 9 lost tickets & duplicates, 10 night parking & fines (with waivers), 11 motorcycles, 12 occupancy & peaks (hour × weekday heatmap, % of P1), 13 monthly executive summary (vs previous month), 14 manual amount changes, 15 blocked‑plate attempts, 16 receipts without plate, 21 open sessions. Reports 17–20 arrive with their phases.
- Dashboard: live KPIs (inside, entries today, revenue today, follow‑up count, open debts), occupancy per level, 30‑day heatmap, today's entries.
- Scheduler (APScheduler, Tehran time): daily report at a configurable time into `<data root>/reports/<year>/<month>` (or a custom folder), catch‑up of up to 7 missed days at start; daily wallet auto‑renewal.
- Performance: `scripts/seed_bigdata.py` (1,000,000 visits, 2,000 subscribers, 200 inside) and `scripts/perf.ps1`.

Benchmark results on 1,000,000 visits (this PC, `tests/artifacts/perf.json`):

| Target (SPEC §2.4) | Limit | Measured |
|---|---|---|
| Plate search | < 200 ms | 3 ms |
| Exit price calculation | < 100 ms | 2 ms |
| Entry receipt (register + render) | < 1 s | 42 ms |
| Main window ready | < 3 s | 0.8 s |
| Slowest month report (executive summary) | < 5 s | 1.7 s |

How to run/test: `scripts/check.ps1`; benchmarks `scripts/perf.ps1`. In the app: sidebar → «داشبورد» and «گزارش‌ها».

Known gaps: reports read the local database; the "server database when connected" source and the local‑data banner switch arrive with sync in Phase 9 (`ReportResult.local_only` is already wired to the exports).

## Phase 6 — Backup/restore, fiscal year, maintenance, photos, outages, training ✅
Built:
- Backup: one zip = SQLite online‑backup copy + `receipt`, `templates`, `ads`, `config` folders; copied to the data root's `backups` folder and every extra folder of this PC (second disk / USB; an unavailable one is skipped); retention (keep N); scheduled daily, catch‑up when overdue, and on close; alert when no successful backup for 24 h. Test restore (integrity check in a temp folder) and real restore (safety backup first, app restarts). SQL Server: `BACKUP DATABASE … WITH CHECKSUM` and `RESTORE VERIFYONLY` (tested on LocalDB).
- Fiscal year (migration `0005_ops`): default = current Jalali year; closing (permission + typed year name + reason) archives the final counters, marks the year closed and opens the next one. Closed years are read‑only: local writes dated inside them are refused. Counters restart at the new year; subscriptions, wallets, debts and vehicles inside carry over.
- Photos: Jalali folders `photos/yyyy/mm/dd`, SPEC file names (sanitised, ZWNJ for spaces), retention in days that never removes photos of fled, blocked, night‑parking or "keep forever" sessions; MB/day, free space and days of capacity; disk‑low alert.
- Heartbeat every 30 s → outage log (power cut vs clean shutdown) and report 19; monthly maintenance (SQLite ANALYZE/optimize/incremental vacuum, SQL Server index rebuild + statistics).
- Alert monitor (every minute) feeding the alert bar: printer, backup overdue, disk low (more checks join in later phases).
- UI: **Backup & system** screen with tabs Backup, Fiscal year, Photos & disk (+ maintenance), Outages, Training mode.

How to run/test: `scripts/check.ps1`. In the app: sidebar → «پشتیبان و سیستم».

Known gaps: restore of the central SQL Server database is done with SQL Server tools (the app creates and verifies `.bak` files); cameras (Phase 8) will be the first real writer of photos.

## Phase 7 — Advertising module ✅
Built:
- Ad contracts (migration `0006_ads`): shop, package (Bronze / Silver / Gold from the `ads.packages` setting), dates, weekdays, entry and/or exit receipts, multi‑line text, offer pill, location, large text, shop logo (packages with `logo` only), price, optional payment. Status lights like subscriptions; expired contracts drop out automatically; ending a contract needs a reason (audited).
- Rotation on receipts: the running ad with the fewest prints today is printed on the entry (and duplicate) receipt and, if enabled, on the exit receipt. Each print writes an `ad_prints` event (proof for the shop). With no running ad the ad section and its divider disappear.
- Receipt templates: `templates\` folder watched live (sub‑folders = categories, name = file name), search + category filter, 1‑bit preview, "use for entry receipt", "back to mother receipt", test print, edit in Paint, open folder. A deleted template falls back to the mother receipt with a warning in the alert bar.
- Coupons: shops buy N single‑use codes (12 digits, `9…` + Luhn) paid directly or from the wallet (balance checked); expiry in days from settings; printed on the thermal printer (shop name or the shop's template + barcode). At exit, scanning the coupon after the ticket (or typing it) makes the parking fee 0; night fines stay. A code can be used once only (unique redemption event), expired / unknown codes are refused.
- Ad calendar (sold / free slots per day, weekends & holidays, occasions such as Nowruz and Yalda from settings), monthly raffle (secure random among the month's transient receipts, candidate count + digest stored, once per month, sponsor shop), advertiser subscription discount (default 10 %).
- Reports (number 20): ads & prints, coupons per shop (bought / used / expired / revenue), shop wallet statements, printable one‑shop performance report; the financial report now has rows for coupon sales and ad contracts.
- Ad display screen: slideshow of `ads\slideshow` (seconds per slide in settings), full screen on a second monitor when present; device interface + simulator.
- UI: sidebar → «تبلیغات و کوپن» with tabs Ads, Coupons, Ad calendar, Raffle, Receipt templates, Ad display; coupon field on the gate's exit panel.

How to run/test: `scripts/check.ps1` (≈ 595 tests). Receipt images for review: `tests/artifacts/receipt_coupon*.png`.

Known gaps: LED sign text rotation arrives with the LED driver (Phase 10).

## Phase 8 — Cameras & ANPR ✅
Built:
- Plate sources behind one interface (`devices/plate_source.py`): manual (no camera), simulator (several frames with configurable misreads), RTSP/ONVIF via OpenCV on a worker thread (live preview ~5 fps, automatic reconnect with back‑off, credentials never logged), smart ANPR camera HTTP push listener (JSON, one or several frames, base64 photo).
- ANPR engines (`devices/anpr.py`): `none`, `simulator`, and plug‑ins loaded from `<data root>\anpr` (`plugin:file.py:Class`). No model is bundled — see D‑076 and `docs/ANPR_PLUGINS.md`.
- Multi‑frame voting per character with confidence threshold and vehicle‑type vote (`core/anpr.py`, property‑tested).
- Camera reads (migration `0007_camera`, append‑only): every pass with confidence, vehicle type and photo (Jalali folder, SPEC file name); link to the entry / exit session; corrections keep the camera read and the operator's value; unidentified passes (plate unreadable) listed for 7 days with photo, operator fills the plate later.
- Gate screen: lane tiles with live preview, last read plate, vehicle type and confidence chips, online light; an entry read fills the plate field and vehicle type (the operator confirms with one key); an exit read opens the matching vehicle; *Unidentified passes* tab; camera offline → alert bar.
- Settings → Devices: camera per lane (type, name, RTSP URL, push port, engine, minimum confidence).
- Report 17 camera accuracy (reads, matched, corrected, unreadable, accuracy %, list of corrections). Report 16 (receipts without plate) already existed.

How to run/test: `scripts/check.ps1`. Try it without hardware: Settings → Devices → Cameras → type *Simulator* for both lanes, restart the app.

Known gaps: plate reading from plain RTSP cameras needs a licensed engine plug‑in (none bundled); after‑hours report 18 comes with watch mode (Phase 9).

## Phase 9 — Server role & multi‑gate sync ✅
Built:
- Roles per PC (standalone / gate / server). The server uses the central SQL Server database directly; gates keep working on local SQLite and sync with it (migration `0008_sync`).
- Sync (`services/sync.py`): outbox on gates and a sequence log on the server, both written in the same transaction as the data; push inserts events if absent (idempotent re‑push) and merges reference rows (higher version wins, the losing version goes to `audit_log` + review queue); pull applies other writers' rows in order, waits for sequence gaps that may still be committing, and keeps the local list of vehicles inside up to date.
- Duplicate heuristics (same subscriber or shop, amount, day, different PCs) → **Needs review** screen; the supervisor cancels one payment (end date and wallet corrected) and closes the item with a note. Nothing is deleted automatically.
- Other gate's ticket while offline: the signed barcode (or typed ticket + entry time) gives a provisional session; it is linked to the real entry once both gates sync.
- Joining a gate to the server (Settings → Server & sync): connection test, join (empty local database set aside, server data and ticket key taken over), role, SQL login, update share.
- Link indicator in the top bar with last sync time; alerts for link down (with unsent count) and clock drift > 60 s. Reports run on the server database when connected, otherwise on local data with the «گزارش محلی» banner.
- After‑hours watch mode: camera passes outside opening hours are logged with photos, no receipts, blocked plates raise the alarm; morning report acknowledged by the first user of the day (who/when stored); report 18.
- Server host without a window (`--server`) and Windows service wrapper (`--service install|start|stop|remove`): scheduler, backups, daily reports, heartbeat, watch‑mode cameras.
- Auto‑update at gate start‑up from a shared folder (`version.json` + silent installer).

How to run/test: `scripts/check.ps1` (sync tests run two gates against an SQLite central database and against LocalDB SQL Server). Try it on one PC: start a copy with `--data-root D:\srv` and role *server*, another with `--data-root D:\g1`, join it from Settings → Server & sync.

Known gaps: the Windows service is installed by the setup program (Phase 11; installing needs administrator rights); final verification on SQL Server Express 2022 + ODBC Driver 18 happens on the site PCs (tests here use LocalDB 2012 + ODBC 13).

## Phase 10 — Hardware integrations ✅
Built (every device behind an interface, with a simulator; each can be switched off in Settings → Devices):
- **Barrier** (`devices/barrier.py`): USB/serial relay, network relay (TCP), web relay (HTTP), simulator. Opens automatically after the entry receipt / for covered subscribers and after payment or a free exit; manual opening with a reason; every opening logged (migration `0009_hardware`).
- **RFID / UHF cards** (`devices/rfid.py`): USB readers via the scanner filter, serial, UHF over TCP (auto‑reconnect), simulator. Cards are managed in the subscriber profile (add by reading or typing, report lost). A card at the gate does entry or exit for that person.
- **Card terminal (PC‑POS)** (`devices/payment.py`): interface + simulator + manual mode; approved payments store the trace number; decline/timeout keep the vehicle in the exit panel. PSP drivers wait for the bank's SDK.
- **LED sign** (`devices/led.py`): serial / TCP text drivers + simulator; rotation of free spaces per level and LED‑package ads.
- **Ad display**: shop text slides for Gold (screen) ads between the image rounds.
- Settings → Devices: kinds, ports/addresses, relay commands, terminal timeout, and test buttons for barrier, LED and terminal.

How to run/test: `scripts/check.ps1`. Without hardware: set barrier, card reader, terminal and LED to *Simulator*.

Known gaps: Iranian PSP terminal protocols (Behpardakht, Sepehr, Pardakht Novin) and vendor‑specific LED protocols need their documentation — the interfaces are ready (`PaymentTerminal`, `LedSign`).
