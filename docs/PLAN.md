# Master Plan (Phases 0–11)

Status legend: ⬜ not started · 🔄 in progress · ✅ done (tagged)

## Context
The repository contains only `CLAUDE.md`, `docs/SPEC.md` and a `README.md` pulled from GitHub
(`alirezamolayari-prog/Caspian_parking`, local `main` already tracks `origin/main`). We are building the
whole Windows desktop parking app described in SPEC.md from scratch, phase by phase (SPEC §9), each phase
ending green (ruff + pytest offscreen + smoke launch), committed, pushed and tagged `phase-N`.
After approval this plan is saved as `docs/PLAN.md`, and `docs/PROGRESS.md` + `docs/DECISIONS.md` are created.

---

## Prerequisites — checked on this machine (2026‑09‑28)

| Item | Found | Action |
|---|---|---|
| Python 3.12 x64 | **Only 3.12 32‑bit** (+3.14, +uv 3.11) | `uv python install 3.12` → CPython 3.12.13 x64 (per‑user, no UAC). `.venv` created with `uv venv --python 3.12`. |
| uv | 0.11.30 | used for Python + fast installs (pip still works inside the venv) |
| Git | 2.55, credential helper = `manager` | first `git push` may open a browser login (owner action) |
| gh CLI | **not installed** | not required — repo already exists; install via winget only if needed |
| GitHub repo | exists, `main` has 1 commit (README) | use existing `Caspian_parking` instead of creating `caspian-parking` |
| ODBC Driver 18 | **missing**; Driver 13 present | dev/tests use Driver 13; driver name is a setting. ODBC 18 = installer prerequisite for site PCs. Ask owner to install (UAC) only if Phase 9 testing needs it. |
| SQL Server for dialect tests | SQL 2008 R2 Express (too old for SQLAlchemy 2) + **LocalDB 2012 (v11.0)** | run SQL Server dialect/migration/sync tests on `(localdb)\v11.0`; avoid T‑SQL newer than 2012 (target in production = SQL Server Express 2022) |
| Inno Setup 6 | not installed | Phase 11: `winget install JRSoftware.InnoSetup --scope user`; if it needs UAC, ask then |
| PowerShell | 5.1, policy Bypass | scripts written for 5.1 (no `&&`, no ternary) |
| Disk / RAM | E: 269 GB free, C: 27 GB, 7.9 GB RAM | 1M‑row dataset lives under a temp folder on E: |

---

## Cross‑cutting technical decisions
See `docs/DECISIONS.md` (D‑001 … D‑014 were fixed at planning time).

---

## Target layout
```
src/caspian_parking/
  __main__.py  app.py  version.py
  core/        ids, clock, money, digits, jalali, plate, permissions, tariff/, tickets, barcode, subscriptions, coupons, fiscal
  data/        db (engine/pragmas), types, base, models/*, repositories/*, migrations/ (alembic), sync/
  devices/     printer/, scanner/, plate_source/, barrier/, rfid/, payment/, led/, adscreen/  (each: interface + simulator + real)
  services/    auth, gate_service, identification, subscriptions, reports/, backup, fiscal_year, photos, scheduler, heartbeat, watch_mode, alerts, updater, server_host
  ui/          theme/ (tokens, qss, fonts, icons, manager), widgets/ (Card, Button, PlateWidget, DataTable, Toast, Dialog, StatusLight, AlertBar, JalaliDatePicker…), receipt/ (renderer), screens/*, shell (main window, sidebar, topbar, command palette)
  i18n/        __init__ (tr), bidi.py, fa.json
  resources/   fonts/ (Vazirmatn), icons/ (Lucide), sounds/, templates/ (Excel import template), receipt defaults
tests/  mirrors the package; tests/artifacts/ (PNG receipts, gitignored)
scripts/ setup.ps1 check.ps1 run.ps1 build.ps1 perf.ps1 seed_bigdata.py
docs/   SPEC PLAN PROGRESS DECISIONS THIRD_PARTY_LICENSES SITE_SETUP USER_MANUAL_FA
```
**Quality gate `scripts\check.ps1`:** `ruff check` → `mypy` (lenient) → `pytest` with `QT_QPA_PLATFORM=offscreen` (excludes `perf` marker; `mssql` tests run when LocalDB is reachable, otherwise skip with a visible reason) → smoke launch `python -m caspian_parking --smoke` (offscreen, temp data root, opens main window, exits 0). Non‑zero exit on any failure.
**Migration DoD test:** `tests/data/test_migrations.py` walks every Alembic revision on SQLite and LocalDB, seeding rows at each phase's head and asserting they survive the upgrade.

---

## ✅ Phase 0 — Environment, repo, scripts, skeleton
- [x] Install CPython 3.12 x64 via uv; create `.venv`
- [x] `pyproject.toml` (src layout, ruff, pytest markers `mssql`/`perf`/`slow`, mypy lenient), `requirements.txt` + `requirements-dev.txt` pinned
- [x] Package skeleton with all sub‑packages; `python -m caspian_parking` opens a placeholder window; `--smoke` flag
- [x] `scripts\setup.ps1`, `check.ps1`, `run.ps1`, `build.ps1` (stub), `.gitignore` (venv, db, photos, backups, .env, keys, build, artifacts), `.gitattributes`
- [x] Docs: PLAN, PROGRESS, DECISIONS, THIRD_PARTY_LICENSES (initial deps)
- [x] Tests: `tests/test_smoke.py` (import + offscreen window), `conftest.py`
- **Acceptance:** check.ps1 green; push works; tag `phase-0`.
- **Risks:** first push may need the owner's browser login.

## ⬜ Phase 1 — Foundations
- [ ] **Core utils:** `ids` (UUIDv7), `clock`, `money` (format with separators + Persian digits), `digits` (normalize fa/ar/latin), `jalali` (UTC↔Tehran↔Jalali, formatting), `plate` (Iranian plate parse/normalize/format: `12ب345-22`, motorcycle and free‑form), `i18n.tr` + `fa.json`, `i18n.bidi` (`ltr()`, `rtl()` isolates).
- [ ] **Config & logging:** machine `settings.json` in data root `config\`, data‑root folder creation (§2.6), rotating size‑capped logs, DPAPI secrets (with test fallback).
- [ ] **DB layer:** engine factory (SQLite WAL, `synchronous=FULL`, FK on, busy timeout; mssql via pyodbc), custom types, `EventMixin`/`ReferenceMixin`, models: nodes, gates, levels, users, roles, permissions, role/user permissions, shifts, settings (versioned key→JSON), audit_log. Alembic env for both dialects (`render_as_batch` on SQLite). Append‑only guard + triggers. Base repositories with audit.
- [ ] **Users & permissions:** permission enum (all 17 from §4.1), role presets (operator / supervisor / admin), scrypt auth, shift login/logout log, seed admin.
- [ ] **Design system:** `tokens.py` (dark + light from brand palette, status colors with lightness contrast, radii, 8‑pt spacing, type scale), QSS generator, Vazirmatn registration (5 weights, tabular figures), Lucide icons recolored per theme, ThemeManager (instant switch, per‑user memory, follow Windows), DWM dark title bar. Widgets: Card, Button variants (≥44 px), TextField (digit normalization), Toast, dimmed modal Dialog, StatusLight (black light with ring on dark), Badge, EmptyState, AlertBar, lazy `DataTable` base model (`canFetchMore/fetchMore`), **PlateWidget** (painted Iranian plate), micro‑animations helper.
- [ ] **App shell:** login dialog, main window (RTL; right collapsible sidebar; top bar: gate name, link status, Jalali clock, operator, theme toggle, search), screen registry with permission gating, Ctrl+K command palette (pluggable providers), Help → Shortcuts, Users & Roles screen (permission checkbox matrix), Audit log viewer.
- **Tests:** uuid7 ordering/uniqueness; digits/jalali/money/plate parametrized; bidi mixed‑direction strings; theme contrast ≥ 4.5:1 for every text/background pair in both themes; lint‑test "no hex colors outside tokens"; "no brand strings in code" test; migrations on SQLite + LocalDB; append‑only (update/delete raise, triggers fire); audit written on every reference change; scrypt; pytest‑qt: login, permission‑hidden buttons, theme toggle, command palette, PlateWidget render to PNG.
- **Deps:** Phase 0. **Risk:** LocalDB 2012 + SQLAlchemy 2 quirks → keep T‑SQL 2012‑compatible.

## ⬜ Phase 2 — Tariff engine + settings UI
- [ ] `core/tariff/`: `TariffValues` (frozen, all ints), `TariffVersion` (effective_from), `ParkingCalendar` (hours per weekday, free weekdays, holidays), `compute_price(...) -> PriceBreakdown` (entry fee, extra minutes & amount, rounding, chargeable minutes, nights & fines, coupon, exemption, flags, lines for receipts/reports); price basis entry‑time/exit‑time; pass‑through rule; motorcycle flat; after‑hours flag.
- [ ] Data: `tariff_versions`, `working_hours`, `holidays` (reference + audit); seed defaults from §4.5.
- [ ] UI: Settings → Tariffs (versions + history, new version with effective date, permission‑gated), Working hours per weekday, Free weekdays, Holiday calendar; Jalali date picker widget.
- **Tests (exhaustive, ~100% branch):** every §4.5 example (5/60/61/72/73/120 min, Wed 14:00→Thu 11:00), second flooring (60m59s = 60), rounding step variants, integers only, motorcycle, night fines (0/1/multiple nights, free days, grace), holidays, spans over several paid/free days, tariff change mid‑stay (both bases), pass‑through 20 vs 21 min, coupon zeroes fee but not fines, exemption, after‑hours arrivals; property tests (hypothesis): monotonic non‑decreasing in exit time, result multiple of step.
- **Deps:** Phase 1 (settings, audit, jalali).

## ⬜ Phase 3 — Gate operations (standalone, manual plate)
- [ ] `core/tickets.py` (Luhn, format/parse, sequence), `core/barcode.py` (payload + HMAC encode/verify, Code 128 C encoder → modules).
- [ ] Models: `active_sessions`, `visits`, entry/exit/payment/cancellation/adjustment/debt/debt_collection/duplicate/night_mark events, `gate_sequences`, `fy_counter_events` (immutable counters), `levels` occupancy.
- [ ] `services/gate_service.py`: entry (3 s debounce, one open session per plate, types, receipt without plate, motorcycle, pass‑through kinds), exit (scan/type/search/lost), price via tariff engine, payments (cash / card / mall‑card), fleeing → debt, debt collection on next arrival, lost ticket + duplicate, cancel with reason, manual amount change with reason, night marks + closing list + next‑morning auto‑flag, occupancy, counters.
- [ ] Receipt renderer (`ui/receipt/`): QPainter → 576 px → 1‑bit (dither), sections per §5.1 (toggles, dividers built‑in or image, plate frame, dotted leaders, barcode art overlay), duplicate label, motorcycle, exit receipt; missing image → skip + warning.
- [ ] Devices: `Printer` (QPrinter driver, ESC/POS raster via win32print, Simulator→PNG), `Scanner` (HID wedge event filter: < 35 ms bursts + Enter, digit normalization, focus‑independent; Serial; Simulator); scanner test page; print preview dialog.
- [ ] UI: main operator screen (lane tiles w/o camera, big PlateWidget + editor, big buttons + F‑keys + Space/Enter, banners placeholder), exit/payment panel with breakdown, inside list (lazy, filter), today's entries, occupancy per level, counters, cancel/lost/fleeing/night dialogs.
- **Tests:** Luhn catches all single‑digit errors and adjacent transpositions (except 09↔90); Code 128 against reference vectors + checksum + module ≥ 3 px + quiet zones; payload round‑trip; HMAC rejects every single‑digit edit of fixed samples; renderer PNGs in `tests/artifacts/` (width 576, 1‑bit, no‑ad = 1 divider, ad = 2); pytest‑qt entry → preview → exit → payment; debounce; duplicate plate warning; cancel = new event; fleeing → debt → alert on next entry; counters immutable; timing: receipt render + simulated print < 1 s, exit price < 100 ms.
- **Deps:** 1, 2.

## ⬜ Phase 4 — Subscribers, shops, wallets, free access, blocklist
- [ ] `core/subscriptions.py` (pure): end date, early renewal (+30 from current end), lights with configurable thresholds, negative subscription (distinct entered days deducted, max days), concurrency rule.
- [ ] Models: persons/profiles, plates (normalized, many per profile), shops, wallet events, subscription payment events, negative allowances, free‑access permits (staff/owner/guest with ranges), blocks + block attempts, night‑fine exemptions.
- [ ] Services: identification (plate → subscriber/free/blocked/debtor result used by the gate), renewals (wallet auto‑debit or supervisor action per setting, low‑balance light), follow‑up list, Excel/Word export, Excel import with template (`resources/templates/subscribers_import.xlsx`) + validation report.
- [ ] UI: subscribers list/profile/new form, shop account + wallet + printable statement, free access, blocklist (register/unblock with reason; red full‑screen alarm + sound; generic text for security categories), follow‑up list; banners on the main screen.
- **Tests:** exhaustive date logic (boundaries of each light, negative days, early payment, max negative), concurrency (2nd plate → transient), wallet debit/low balance, import validation cases, exports open and contain rows, UI flows for new subscriber / blocked arrival / debtor.
- **Deps:** 3.

## ⬜ Phase 5 — Reports, dashboard, automatic daily report
- [ ] Report framework: definition (params: Jalali range, gate, operator, category) → result (columns, rows, totals, chart data) → exporters Excel / Word / PDF (QTextDocument + QPdfWriter) / print; runs in QThreadPool; local‑data banner hook.
- [ ] Reports 1–11, 12 (heatmap), 13 (monthly executive summary), 14–16, 21 (rest in their phases).
- [ ] Dashboard screen (today KPIs, occupancy, heatmap widget, recent events).
- [ ] APScheduler daily report with configurable time/folder, disable flag, catch‑up at next start.
- [ ] `scripts/seed_bigdata.py` (1,000,000 visits, 2,000 subscribers) + `perf` benchmarks: plate search < 200 ms, exit calc < 100 ms, month report < 5 s, main window ready < 3 s; `scripts\perf.ps1`.
- **Tests:** each report's totals on a fixed fixture; exporters produce valid files; catch‑up logic; benchmarks (run at end of every phase ≥ 5, results in PROGRESS).
- **Deps:** 3, 4.

## ⬜ Phase 6 — Backup/restore, fiscal year, maintenance, photos, outages, training
- [ ] Backups: SQLite online backup API, SQL Server `BACKUP DATABASE` (tested on LocalDB), zip of `receipt\ templates\ ads\ config\`, multiple destinations, retention, scheduled + on close + catch‑up, > 24 h warning; restore with confirmation + test restore (integrity check / `RESTORE VERIFYONLY`).
- [ ] Fiscal year: define/close → archive read‑only (repositories reject writes into closed years), carry‑over (active subs, balances, debts, inside vehicles), counters reset & archived.
- [ ] Monthly maintenance (ANALYZE + incremental vacuum; UPDATE STATISTICS / index rebuild).
- [ ] Photo storage: Jalali folders, spec file naming + sanitizing, retention that never deletes protected sessions, MB/day + days‑left estimate, disk alerts.
- [ ] Heartbeat every 30 s → outage log + report 19. Training mode (separate DB, banner, "آموزشی" on receipts). Hardware alert bar service (§4.15).
- **Tests:** backup→restore round trip (SQLite + LocalDB), retention, fiscal close carry‑over and read‑only, photo naming & retention, outage detection from heartbeat gaps, training isolation.
- **Deps:** 3–5.

## ⬜ Phase 7 — Advertising module
- [ ] Ads (shop, dates, weekdays, package, price, text size, logo for premium), rotation + print counts, receipt ad section on/off, exit‑receipt ads.
- [ ] Template folder watcher (QFileSystemWatcher; name = file name; categories = subfolders), searchable picker + 1‑bit preview, test print, edit in Paint, fallback + warning.
- [ ] Coupons: purchase (direct or wallet), unique single‑use codes + expiry, print, redeem at exit (fee 0, fines stay), reports per shop.
- [ ] Ad contracts with lights + auto‑removal, ad calendar (weekends, Nowruz, Yalda…), packages Bronze/Silver/Gold in settings, advertiser subscription discount, monthly raffle (`secrets.SystemRandom`, logged), shop performance report, report 20, ad slideshow screen (AdScreen simulator + second monitor).
- **Tests:** rotation fairness, print counters, no‑ad layout, coupon single‑use/expiry/night fine, watcher add/remove/fallback, raffle logged & reproducible audit.
- **Deps:** 3, 4, 5.

## ⬜ Phase 8 — Cameras & ANPR
- [ ] `PlateSource` implementations: Manual, Simulator (images/plates from folder with configurable misreads), RTSP/ONVIF (OpenCV thread + auto‑reconnect), Smart camera HTTP push listener; `AnprEngine` plug‑in + ONNX Runtime loader (license check → DECISIONS).
- [ ] Multi‑frame voting, vehicle‑type classification interface, confidence threshold, snapshots saved via photo service.
- [ ] Lane tiles with live preview, unidentified passes list (fill plate later), corrections (camera read + corrected value), reports 16/17, camera offline alerts.
- **Tests:** voting logic, simulator end‑to‑end entry with photo, correction storage, accuracy report, reconnect behaviour (fake stream).
- **Deps:** 3, 6.

## ⬜ Phase 9 — Server role & multi‑gate sync
- [ ] Roles (Server/Gate/Standalone) in config; server schema on SQL Server; outbox/inbox tables; sync worker (push idempotent, pull by cursor, reference merge rule); duplicate heuristic → "Needs review" queue screen; link status + last sync; clock drift > 60 s warning; report data source selection + local banner.
- [ ] Server host process + Windows service (pywin32): scheduler, sync hub tasks, backups, daily reports, watch mode; after‑hours watch mode (log in/out with photos, no receipts), morning report ack (who/when); auto‑update check against server share; HMAC key distribution to joining gates.
- **Tests:** two SQLite gates + LocalDB server: online, offline, reconnect, re‑push idempotency, cross‑gate ticket exit offline (barcode decode), duplicate subscription payment → review queue, drift warning, morning ack.
- **Deps:** all previous. **Risk:** may need ODBC 18 / SQL 2022 for final verification (owner UAC).

## ⬜ Phase 10 — Hardware integrations (behind flags)
- [ ] Barrier (serial/USB/network relay + simulator; open rules; manual open with log), RFID/UHF (HID, serial, TCP + simulator; card ↔ profile; lost card), LED sign (text rotation: ads, free spaces), PaymentTerminal (interface + simulator + manual; PSP drivers pending SDK), AdScreen polish; Hardware settings screen with test buttons and enable flags.
- **Tests:** simulators drive each flow (paid → barrier opens, RFID subscriber entry, POS success/failure/timeout, LED rotation).
- **Deps:** 3, 4, 7, 8.

## ⬜ Phase 11 — Packaging, wizard, performance, docs
- [ ] PyInstaller onedir spec (fonts, icons, resources, Qt plugins), Inno Setup script (Persian + English, desktop shortcut, start‑with‑Windows, ODBC 18 + VC++ checks, uninstall keeps data root), `build.ps1`.
- [ ] First‑run wizard (role, gate, server + SQL test, data root, printer, scanner, cameras, admin, theme); auto‑update install path.
- [ ] Full 1M‑row performance run vs SPEC §2.4 (results in PROGRESS); final QA checklist across all screens in both themes; `docs/USER_MANUAL_FA.md`, `docs/SITE_SETUP.md`; license review.
- **Acceptance:** installer builds; installed app launches & passes smoke; tag `phase-11`.

---

## Things only the owner can do (I will stop and ask only for these)
1. Browser login for GitHub on the first `git push` (if Git Credential Manager asks).
2. UAC prompts: ODBC Driver 18 (only if Phase 9 needs it), Inno Setup if a per‑user install is not possible.
3. Real hardware/protocol details (printer model, POS PSP SDK, camera models) — until then simulators are used and it's noted in PROGRESS.

## Verification (every phase)
`scripts\check.ps1` green (ruff, mypy lenient, pytest offscreen incl. LocalDB tests, smoke launch) → update PROGRESS/PLAN → Conventional Commit → `git push` → `git tag phase-N` + `git push --tags`. From Phase 5 also `scripts\perf.ps1` with results logged. Receipt PNGs in `tests/artifacts/` are inspected visually at the end of Phases 3 and 7.
