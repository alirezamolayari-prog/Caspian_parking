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
