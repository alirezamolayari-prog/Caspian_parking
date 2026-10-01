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
- **D‑040 — Receipt artwork:** the pine‑forest and wave barcode art are generated into `<data root>\receipt\barcode-art` on first use; the logo (`receipt\logo.png`) must be supplied by the owner — until then receipts print without it and the alert bar says so.
- **D‑041 — Style hooks:** the QSS size hook is the dynamic property `scale` (Qt silently ignores dynamic properties named like built‑in ones such as `size`; a test guards this).
- **D‑042 — Scanner scope:** the keyboard‑wedge filter only acts while its owning screen is visible; serial scanners run on a background thread with automatic reconnect.
- **D‑043 — Overnight auto‑flag:** runs whenever the gate screen is shown (e.g. first login in the morning); idempotent per night.
- **D‑044 — Pass‑through types:** courier → motorcycle, van unloading → van; taxi/other use the selected vehicle type.
- **D‑045 — Duplicates:** one open session per plate (the operator is offered "exit this vehicle" or "print duplicate"); receipts without plate are debounced for the configured seconds (default 3) per gate.

## Phase 4 (2026‑09‑30)

- **D‑046 — People model:** subscribers and free‑access persons share one `people` table (`kind` = subscriber | free, `free_category` = staff | owner | guest). Plates live in `person_plates`; a plate can belong to only one active person.
- **D‑047 — Subscription end date:** stored on the person (audited) and always written together with an append‑only `subscription_payments` event (previous end, new end, used negative days), so the history is complete.
- **D‑048 — Covered visits:** active subscribers, negative‑allowed subscribers and valid free access enter as "covered": no parking fee, but night fines still apply unless the person has the permanent night exemption. Expired subscribers (not negative‑allowed) and a second plate over the concurrency limit are charged as transients (flagged).
- **D‑049 — Negative subscriptions:** a permitted user allows it with a reason and a maximum number of days (default 10, setting `subscription.negative_max_days`). At payment the *distinct Tehran dates* with covered entries after the old end are deducted; the permission is consumed by the payment.
- **D‑050 — Paper tickets for covered visits:** not printed by default (the entry is still recorded for traffic reports and negative‑day counting); setting `gate.print_for_covered` turns printing on.
- **D‑051 — Wallets:** wallet balance = sum of signed append‑only wallet transactions. Automatic renewal from the wallet (button "renew due", also used by the scheduler in Phase 5) renews only when the balance covers the price; the shop's light is amber when the balance is below its threshold or below the next renewals.
- **D‑052 — Blocklist:** blocking is per plate or per person (a person block covers all their plates). A blocked arrival is refused, logged in `block_attempts`, and shows a full‑screen red alarm with a beep. For the *security* category operators only see the generic message unless they have the "view block details" permission.
- **D‑053 — Excel import:** rows with any problem are skipped and listed in an error report in `exports`; valid rows are imported in one transaction. Imported start dates become a zero‑amount `import` payment (so revenue reports are not inflated) and the row amount becomes the person's price.
- **D‑054 — Exports:** Excel/Word exports are right‑to‑left, landscape, fit to page width, header row repeated, font Tahoma (present on every Windows PC). Plates are exported as text in a left‑to‑right embedding so digits never flip.
- **D‑055 — Test speed:** the quality gate runs the suite on 4 processes (pytest‑xdist); tests marked `serial` (startup time, render time, quote time) run alone afterwards so machine load cannot distort them. The startup test takes the best of two launches.

## Phase 5 (2026‑09‑30)

- **D‑056 — Revenue definition:** the financial report counts money received: parking payments (incl. night fines), debt recoveries, subscription payments in cash / card / mall‑card and wallet deposits. Subscriptions paid *from* a wallet are not counted again, and imported subscriptions (amount 0) never appear.
- **D‑057 — Occupancy:** computed by sweeping entries/exits (visits + vehicles inside) at hourly samples; the heatmap shows the average number inside per weekday × hour. "% of P1" = peak ÷ capacity of the first parking level.
- **D‑058 — Daily report:** financial, open sessions, night parking and debts for the day, Excel + Word (no Qt in the scheduler thread). Missed days (PC off) are generated at the next start, at most the last 7 days.
- **D‑059 — Performance dataset:** `.bigdata/` (git‑ignored) is built with bulk SQL for speed; benchmarks use the previous full Jalali month for "report of one month".

## Phase 6 (2026‑09‑30)

- **D‑060 — Backup format:** a single zip per backup (database + folders + manifest) so a restore is one file; secrets in `config` are DPAPI‑encrypted for this machine, so a backup restored on another PC needs the HMAC key re‑entered through the wizard (Phase 11).
- **D‑061 — Backup destinations** are per PC (machine config), other backup settings (time, keep, on close) are shared settings.
- **D‑062 — Read‑only closed years:** enforced in the session hook for append‑only records created on this node with a timestamp before the open year's start (only possible with a wrong PC clock); records synced from other nodes are not blocked here (Phase 9 handles them).
- **D‑063 — Fiscal year default:** Farvardin 1 – Esfand end of the Jalali year; the next year after a close starts the day after the closed year's end.
- **D‑064 — Photos table:** photo rows are reference data without audit (thousands per day); deleting an old file sets `file_deleted_at_utc` instead of deleting the row.
- **D‑065 — Outages:** a gap of more than 90 s between heartbeats counts as an outage; the heartbeat file also records whether the app shut down cleanly.

## Phase 7 (2026‑09‑30)

- **D‑066 — Ad rotation:** among the ads running today for the placement (entry / exit), the one with the fewest prints today is printed (ties: oldest contract). Every printed ad writes an `ad_prints` event (proof for the shop); training mode does not count prints.
- **D‑067 — Packages** (Bronze / Silver / Gold) live in the `ads.packages` setting (price + features `text`, `coupons`, `logo`, `screen`, `led`). The shop logo is printed only when the package has `logo`. Coupon sales are open to every shop (the package list is shown to the owner but not enforced), because shops may buy coupons without an ad contract.
- **D‑068 — Ad payments:** an ad contract can be recorded as paid (cash / card / mall card) when it is created; this writes a `Payment` with purpose `ad`, and coupon sales write purpose `coupon` (or a wallet debit). Both appear as their own rows in the financial report.
- **D‑069 — Coupon codes:** 12 digits = `9` + 10 random digits (`secrets`) + Luhn check digit; the prefix keeps them apart from 20‑digit ticket payloads on the same scanner. A redemption is an append‑only event with a unique `coupon_id`, so a code can be used only once even with two open quotes. Coupons are refused when expired; night fines still apply (tariff engine coupon flag).
- **D‑070 — Wallet coupon purchase needs enough balance** (unlike subscription renewals, which the owner may want to allow into the negative), so a shop cannot buy coupons on credit by mistake.
- **D‑071 — Advertiser discount** (`ads.advertiser_discount_percent`, default 10 %) applies to the list subscription price of people linked to a shop that has a running ad; a manual price override is never discounted. Integer Rial: `price − price × % // 100`.
- **D‑072 — Raffle:** candidates are the month's transient entry receipts that were not cancelled, ordered by entry time; the winner is chosen with `secrets.SystemRandom`, and the number of candidates plus a SHA‑256 digest of the candidate list are stored so the draw can be audited. One draw per Jalali month.
- **D‑073 — Receipt template selection** is a shared setting (`receipt.template`, path relative to `templates\`); if the file disappears, the mother receipt is printed and the gate shows a warning in the alert bar. Coupons may use their own template per batch.
- **D‑074 — Ad calendar:** `ads.slots_per_day` (default 3) receipt‑ad slots per day; weekends = free weekdays + holidays from the tariff calendar; occasions (Nowruz, Yalda, Esfand shopping…) come from the `ads.occasions` setting.
- **D‑075 — Report 20** is split into four reports that share number 20 (ads & prints, coupons per shop, wallet statements, one‑shop performance report), because each needs different columns.

## Phase 8 (2026‑10‑01)

- **D‑076 — No bundled ANPR model:** the only Iranian‑plate model found on Hugging Face (2026‑10) has no licence and is a YOLO `.pt` file (Ultralytics weights are typically AGPL), so it cannot ship in a closed‑source product. The app ships the engine interface, a plug‑in loader (`plugin:<file.py>:<Class>` from `<data root>\anpr`, where a licensed SDK or ONNX model can be wrapped), smart‑camera HTTP push, manual entry and a simulator. Guide: `docs/ANPR_PLUGINS.md`. ONNX Runtime is therefore not a dependency of the app itself.
- **D‑077 — Voting:** frames of one pass are weighted by engine confidence; the plate kind (car / motorcycle) with more weight wins, then each character position is voted. Final confidence = weakest position's share × average frame confidence, stored as an integer percent. Default trust threshold 80 % per camera.
- **D‑078 — The camera never registers a vehicle by itself:** an entry read fills the plate field and vehicle type; the operator confirms with one key (Space / Enter / button), as SPEC §4.3 says "receipt printed with one action". An exit read opens the matching inside session when the exit panel is free.
- **D‑079 — Read ↔ session link:** a camera read is linked (`read_matches`) to the next entry registered or exit completed on that lane within 120 s; if the operator's plate or vehicle type differs, a `plate_corrections` event keeps both values. Unreadable passes are listed for 7 days under *Unidentified passes*; filling one writes a correction without a session.
- **D‑080 — Photos are stored when the pass is read** (file name uses the read plate and the subscriber's name if known) and attached to the session when linked; the photo row's `session_id` is reference data, so setting it is allowed.
- **D‑081 — RTSP reconnect:** back‑off 1, 2, 4 … 30 s while the stream is down, reset after a good connection; the stream URL is logged without user name / password. Camera online/offline drives the alert bar (`camera_entry`, `camera_exit`).
- **D‑082 — Camera configuration is per PC** (machine config `cameras`, one per lane), applied when the app starts.
