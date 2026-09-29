# Decisions log

Assumptions and choices made where SPEC.md is silent or where the environment forced a choice.
Newest entries at the bottom of each section. Format: **D‑NNN — title** → decision · why.

## Planning (2026‑09‑28)

- **D‑001 — IDs:** own UUIDv7 generator (`core/ids.py`), stored as `CHAR(36)` text · time‑ordered, portable across SQLite/SQL Server, readable in DB tools.
- **D‑002 — Time:** `UTCDateTime` TypeDecorator (naive UTC in DB); display via `zoneinfo("Asia/Tehran")` + `tzdata` + `jdatetime`. Clock is injectable (`core/clock.py`) · deterministic tests.
- **D‑003 — Money:** `BigInteger` columns, `int` in Python; a test scans money modules for `float` · SPEC rule.
- **D‑004 — Append‑only:** event repositories expose `append()` only; a SQLAlchemy `before_flush` guard rejects UPDATE/DELETE on event models; DB triggers (SQLite `RAISE(ABORT)`, SQL Server `INSTEAD OF UPDATE, DELETE`) are a second wall. `active_sessions` is a **projection** (rebuildable from events): rows are removed on exit; the permanent record is `visits` + events.
- **D‑005 — Reference data:** `row_version`, `updated_at`, `updated_by`; every change writes `audit_log` (old → new JSON) in the same transaction.
- **D‑006 — Passwords & secrets:** stdlib `hashlib.scrypt` for passwords; secrets (HMAC key, SQL password) DPAPI‑encrypted via pywin32 in `config\` · no extra dependency.
- **D‑007 — Ticket number:** `G-SSSSS-C`, per‑gate sequence, min 5 digits (grows, never resets, never shared between gates), Luhn over the digits of `G`+`SSSSS`.
- **D‑008 — Barcode payload:** 20 digits for Code 128 set C = gate(1) + seq(6) + entry minutes since Unix epoch(8) + HMAC‑SHA256 truncated to 5 decimal digits. 10 symbols ≈ 165 modules × 3 px + quiet zones ≈ 555 px ≤ 576. Own Code 128 encoder (pure, tested). HMAC key is per installation, shared by all gates (distributed when a gate joins the server).
- **D‑009 — Night fine:** one fine per configured closing time the vehicle is still inside for; configurable grace minutes, **default 0** (literal SPEC). Applies on free days; motorcycles per flag (default on).
- **D‑010 — Motorcycle on a free day:** fee 0 (flat fee only when there are chargeable minutes); night fine still applies.
- **D‑011 — Sync transport:** gates connect directly to the central SQL Server over ODBC (no custom HTTP API). Push outbox with insert‑if‑absent by UUID; pull by a server‑assigned sequence cursor. Reference data: highest `row_version` wins; the losing version is kept in `audit_log` and put in the review queue.
- **D‑012 — Reports by phase:** reports whose data only exists in later phases (ads, camera accuracy, outages, after‑hours) are delivered in those phases on the Phase‑5 report framework.
- **D‑013 — PC‑POS:** interface + simulator + manual entry; PSP‑specific drivers are added when their SDK/protocol documents are available.
- **D‑014 — ANPR model:** use an open Iranian‑plate ONNX model only if its license allows closed‑source commercial use; otherwise ship the interface + manual + simulator + a guide for adding a commercial SDK.

## Environment (2026‑09‑28, phase 0)

- **D‑015 — Python 3.12 x64 from uv:** the machine only had a 32‑bit 3.12; uv installs CPython 3.12.13 x64 per user (no admin). `scripts\setup.ps1` uses uv when present, otherwise `py -3.12`.
- **D‑016 — GitHub repo:** the owner already created `alirezamolayari-prog/Caspian_parking`; it is used instead of creating `caspian-parking` with `gh`. `gh` is not installed and not needed.
- **D‑017 — SQL Server for development tests:** the local SQL Server 2008 R2 is too old for SQLAlchemy 2; tests use SQL Server 2012 LocalDB `(localdb)\v11.0` with ODBC Driver 13. The ODBC driver name is a setting; production uses ODBC Driver 18 + SQL Server Express 2022. T‑SQL is kept 2012‑compatible.
- **D‑018 — Quality gate also runs mypy (lenient):** cheap and catches Qt API misuse early.

## Phase 1 (2026‑09‑29)

- **D‑019 — No default password, ever:** on first start the app asks for the first administrator account (the Phase 11 wizard will reuse this). Users created by an admin must change their password at first login.
- **D‑020 — Roles:** permissions are stored per user (the checkbox matrix is the truth); role presets only fill the matrix. Admins can save any matrix as a new named preset. An admin cannot remove their own user‑management right or deactivate themselves.
- **D‑021 — Audit is automatic:** session hooks write `audit_log` for every insert/update of reference data (password hashes are masked), so no code path can forget it. Reference rows are never deleted; they are deactivated (`is_active`).
- **D‑022 — Config folder in the data root:** machine settings live in `<data root>\config\settings.json`; the data root itself is found via the `PARKING_DATA_ROOT` environment variable → `%PROGRAMDATA%\<product>\dataroot.txt` (written by installer/wizard) → default from `resources/app_defaults.json`. Product names live only in resource JSON, never in code (a test enforces this).
- **D‑023 — Local DB file:** `<data root>\db\local.db` (training: `training.db`). A `db\` folder was added to the SPEC §2.6 layout.
- **D‑024 — Shadows:** drop shadows only on raised cards; regular cards use borders (cheaper to paint on old gate PCs).
- **D‑025 — Test note:** Qt's QTest cannot synthesize Arabic‑script key events offscreen (the process crashes), so tests type Latin keys for real and feed Persian text through the same `textEdited`/`setText` path.
- **D‑026 — Formatting:** `ruff format` is part of the quality gate (line length 120).

## Phase 2 (2026‑09‑29)

- **D‑027 — After‑hours arrivals:** a vehicle that arrives outside opening hours is a security record (no charge) — but if it is still inside when the parking opens, the minutes inside opening hours are priced normally and the visit is flagged `inside_at_opening` for the supervisor. Night fines only count closings the vehicle was inside for (so an after‑hours arrival is not fined for the closing that already passed).
- **D‑028 — No retroactive tariffs:** a new tariff version must start now or in the future; versions that have not started yet can be withdrawn (deactivated, audited). Past versions are never edited, so every old ticket keeps its price basis.
- **D‑029 — Price basis setting:** "tariff at entry time" (default) / "at exit time" is one installation setting (`tariff.price_basis`), not part of a tariff version.
- **D‑030 — Less than one chargeable minute is free:** SPEC says a partial minute is not charged and the entry fee applies "if chargeable minutes > 0", so a stay under one minute costs nothing.
- **D‑031 — Pass‑through fine:** a pass‑through (عبوری) that happens to cross closing time still gets the night fine (literal SPEC; the configurable grace minutes exist for this). Pass‑through limit uses the total stay, not only chargeable minutes.
- **D‑032 — Tariff defaults in seed data:** SPEC §4.5 amounts live in `resources/seed/site.json` (`tariff`, `calendar`); a test forbids those amounts in code.
- **D‑033 — Time inputs:** Qt reverses date/time sections for right‑to‑left locales (09:30 would show as 30:09), so times use a small LTR `TimeField` with Persian digits instead of `QTimeEdit`. The app's default QLocale is Persian (Persian digits in spin boxes).

## Phase 3 (2026‑09‑29)

- **D‑034 — Luhn:** standard Luhn over the digits of gate + sequence. The SPEC example `1-24715-8` is illustrative only; with standard Luhn that ticket is `1-24715-4`.
- **D‑035 — Barcode sequence:** the payload carries the sequence modulo 10⁶ (6 digits keep the payload at 20 digits); a ticket is found by gate + entry minute + sequence mod 10⁶, which is unique. The printed ticket number always shows the full sequence.
- **D‑036 — Occupancy per level:** there are no per‑level sensors, so vehicles inside are counted against the open parking levels in order (P1 first). Total inside vs total open capacity is exact.
- **D‑037 — Immutable counters:** derived from append‑only entry events since the start of the Jalali year (Phase 6 switches to the fiscal‑year table). Nobody can edit them; cancelled entries still count (the vehicle did pass).
- **D‑038 — Payment corrections:** a wrong payment is voided with a cancellation event (reason required, permission *cancel transactions*) and the correct payment is recorded as a new event.
- **D‑039 — Printer failure never loses an entry:** the entry is committed first; a printer error raises the alert bar + toast and the receipt can be reprinted (المثنی) from the "already inside" dialog or the lost‑ticket dialog.
- **D‑040 — Receipt artwork:** the pine‑forest and wave barcode art are generated into `<data root>eceiptarcode-art` on first use; the logo (`receipt\logo.png`) must be supplied by the owner — until then receipts print without it and the alert bar says so.
- **D‑041 — Style hooks:** the QSS size hook is the dynamic property `scale` (Qt silently ignores dynamic properties named like built‑in ones such as `size`; a test guards this).
- **D‑042 — Scanner scope:** the keyboard‑wedge filter only acts while its owning screen is visible; serial scanners run on a background thread with automatic reconnect.
- **D‑043 — Overnight auto‑flag:** runs whenever the gate screen is shown (e.g. first login in the morning); idempotent per night.
- **D‑044 — Pass‑through types:** courier → motorcycle, van unloading → van; taxi/other use the selected vehicle type.
- **D‑045 — Duplicates:** one open session per plate (the operator is offered "exit this vehicle" or "print duplicate"); receipts without plate are debounced for the configured seconds (default 3) per gate.
