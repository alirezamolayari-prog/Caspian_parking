# Caspian Parking — Product & Technical Specification (v1)

> Windows desktop parking-management software for **Caspian Furniture Mall (بازار مبل کاسپین)**, Yaftabad, Tehran.
> This file is the single source of truth. When anything is unclear, follow this file; when this file is silent, choose the simplest robust option, record the decision in `docs/DECISIONS.md`, and continue.

---

## 0. Context in one page

- **Site:** a commercial furniture mall with 4 underground levels: **P1 (202 spaces), P2 (221), P3 (221)** for parking, **P4 = building storage (not parking)**. Today only P1 is used. Capacity per level must be configurable (levels can be added/renamed).
- **Two gates**, each is both entry and exit: **Yaftabad gate (درب یافت‌آباد)** and **Asadnejad gate (درب اسدنژاد)**. Each gate has one PC, one operator, a thermal printer (80 mm), a handheld barcode scanner. Later: one camera per lane (4 total: entry+exit × 2 gates), barrier, RFID, LED sign, PC-POS.
- **An old server with its own battery** exists. It becomes the central server (SQL Server Express). Power outages are common.
- **Opening hours:** 09:30–20:30 (configurable per weekday). **Thursdays and Fridays parking is free** (configurable) but night-parking fines still apply.
- **Users:** parking staff (operators), parking supervisor, and the owner/admin (the product owner). The mall owner receives reports by email (exported files); no mobile app, no web app.
- **Language:** UI in **Persian (fa-IR), right-to-left**, Jalali (Shamsi) calendar everywhere. English/Latin text (plates in some places, codes, file names) must sit correctly beside Persian.
- **Money unit in software: Rial (ریال)**, stored as integers (BIGINT). Display with thousands separators and Persian digits.
- **Brand:** v1 is branded "Caspian". **Never hard-code the brand** in code: mall name, logo, gate names, levels, tariffs, texts all come from settings/data, so a v2 can be white-labelled for other malls.

---

## 1. Tech stack (fixed decisions)

| Concern | Choice |
|---|---|
| Language | Python 3.12 (64-bit) |
| UI | PySide6 (Qt 6, **Qt Widgets**) with a custom design system (QSS generated from theme tokens). Do **not** use GPL UI kits. |
| Central DB | Microsoft SQL Server Express (2022) on the server. Backups as `.bak`. |
| Gate-local DB | SQLite (WAL mode) on each gate PC — the UI always reads/writes the local DB (fast, offline-safe) and a sync worker exchanges data with the server. |
| ORM / migrations | SQLAlchemy 2.x + Alembic (migrations must run on both SQLite and SQL Server). Driver: pyodbc + "ODBC Driver 18 for SQL Server". |
| Jalali dates | jdatetime |
| Excel / Word / PDF | openpyxl / python-docx / PDF via Qt (QPdfWriter / QTextDocument) |
| Images | Pillow + Qt (QImage/QPainter) |
| Camera | OpenCV (RTSP/ONVIF streams); ANPR engine via a plug-in interface (ONNX Runtime for local models) |
| Serial devices | pyserial |
| Scheduling | APScheduler (SQL Server Express has **no SQL Agent** — all scheduling is done by the app) |
| Windows integration | pywin32 (printing, service, DWM dark title bar) |
| Tests / lint | pytest, pytest-qt (run with `QT_QPA_PLATFORM=offscreen`), ruff, mypy (lenient) |
| Packaging | PyInstaller (onedir) + Inno Setup 6 installer |
| Font | **Vazirmatn** (SIL OFL, free) bundled — excellent Persian + Latin harmony. Lalezar (OFL) only for the brand wordmark if needed. |
| Icons | Lucide (ISC license) SVGs, recolored per theme |

Licenses of every dependency must allow closed-source commercial use (the product may be sold later). Record them in `docs/THIRD_PARTY_LICENSES.md`.

---

## 2. Architecture

### 2.1 Roles (chosen in the first-run wizard, changeable in settings)
- **Server**: hosts SQL Server Express, runs the background service (sync hub, scheduler, backups, daily reports, after-hours watch mode, time reference, update share). Can also run the full UI for the admin.
- **Gate**: operator UI + local SQLite + sync worker. Works fully offline.
- **Standalone**: a gate with no server (single-PC mode, Phase 1 default).

### 2.2 Data & sync model (critical)
- Every record has a **UUIDv7** primary key, `origin_node`, `created_at_utc`, `created_by`. No auto-increment IDs cross machines.
- **Append-only events** for money and traffic: entries, exits, payments, cancellations, adjustments, subscription payments, wallet deposits. **Nothing is ever deleted or overwritten**; corrections are new events (cancel with reason, adjustment with reason). Enforce at the repository layer (no DELETE/UPDATE on event tables) and cover with tests.
- **Reference data** (subscribers, plates, shops, blocklist, tariffs, settings, users, ads) is edited anywhere, versioned (`row_version`, `updated_at`, `updated_by`), full change history in `audit_log`.
- **Outbox/inbox sync**: each gate writes events to a local `outbox`; the sync worker pushes to the server idempotently (by UUID) and pulls reference data + other gate's events. Merging never loses data because events are independent rows.
- **Offline-possible duplicates** (e.g., the same subscription payment entered on both gates while the LAN cable is down): detect by heuristic (same subscriber, same amount, same day, different nodes) and place both in a **"Needs review" queue** for the supervisor to cancel one. Never auto-delete.
- **Link status indicator** always visible: "ارتباط با سرور: برقرار / قطع" and last sync time.
- **Clock**: the server is the time reference. Gates compare clocks at every sync; if drift > 60 s show a warning and log it. Ticket prices are computed from timestamps, so this matters.
- Reports run on the server DB when connected, otherwise on local data with a banner "گزارش محلی — ممکن است کامل نباشد".

### 2.3 Tickets must work across gates offline
- **Ticket number (human readable):** `G-SSSSS-C` → gate code (1 = Yaftabad, 2 = Asadnejad, configurable), per-gate sequence, **Luhn check digit** (catches typos). Example `1-24715-8`. Sequences never repeat across gates.
- **Barcode payload (Code 128, numeric → Code set C):** gate code + sequence + entry time (minutes since epoch) + short HMAC (4–6 digits) with a per-installation secret key. Any gate can decode the entry time **without network**, and forged/edited tickets are rejected.
- Barcode must be **Code 128** (every scanner in the Iranian market reads it). Optional artistic QR may be added later, but Code 128 is always printed.
- If the scanner fails, the operator types the ticket number; if the ticket belongs to the other gate and the link is down, the operator types the entry time printed on the ticket.

### 2.4 Performance rules (the app must stay fast after years)
- Separate **`active_sessions`** (vehicles currently inside, a few hundred rows) from **history** tables. The main screen only touches active data.
- Indexes on plate (normalized), entry/exit time, status, subscriber, ticket number.
- Photos are **files on disk**, only their path is stored in DB.
- Every list is paginated/virtualized (QAbstractTableModel with lazy fetching); never load tens of thousands of rows at once.
- Heavy work (reports, exports, backups, camera, sync) runs off the UI thread (QThreadPool / worker processes).
- Monthly automatic maintenance: SQL Server index rebuild/update statistics; SQLite `ANALYZE` + incremental vacuum.
- **Performance targets** (verify with a synthetic dataset of 1,000,000 visits and 2,000 subscribers): entry receipt printed < 1 s after keypress; plate search < 200 ms; exit price calculation < 100 ms; main window ready < 3 s; any report of one month < 5 s.

### 2.5 Power-failure resilience
- All multi-step writes in a single DB transaction; SQLite WAL + `synchronous=FULL` for money tables.
- Auto-reconnect for DB, printer, cameras, server — no operator action after power returns.
- Heartbeat file/row every 30 s; on startup, detect gaps and write an **outage log** (from–to). Report available.
- `docs/SITE_SETUP.md` (Persian) checklist: BIOS "Restore on AC power loss = Power On", Windows auto-login for the kiosk user, app auto-start, Sleep = Never (monitor-off is fine), Windows Update active hours 08:00–22:00, UPS for switch/cameras too, SQL Server service auto-start.

### 2.6 Folders
- Program files: `C:\Program Files\CaspianParking\` (read-only, replaced on update).
- **Data root (configurable, default `C:\CaspianParking\`)** — never inside Program Files, never deleted by uninstall:
```
C:\CaspianParking\
 ├─ receipt\          logo.png, divider.png, barcode-art\ (tree.png, wave.png, …)
 ├─ templates\        custom receipt header images; sub-folders = categories
 ├─ ads\              shop logos for receipt ads; slideshow\ images for the ad screen
 ├─ photos\           camera photos  yyyy\mm\dd\ (Jalali)
 ├─ backups\
 ├─ reports\          auto-generated daily reports
 ├─ exports\
 ├─ logs\
 └─ config\           settings.json (machine-level), secrets are DPAPI-encrypted
```

---

## 3. Design system & UI quality bar

The app must look **premium, modern and expensive** — not like a default Qt app.

- **Themes:** Dark and Light, switchable instantly from the top bar and settings (remember per user). Optional "follow Windows". Title bar follows theme (DWM immersive dark mode on Windows 10/11).
- **Brand palette (from the approved logo):** Navy `#13324A`, Caspian teal `#1F7A7A`, walnut `#9A6034`, ivory `#F5F0E6`.
  - Dark: background graphite-navy (≈`#0E1620`), surfaces layered (`#141F2B`, `#1B2836`), teal accent, walnut secondary accent, text `#E8EEF3` / muted `#9FB0BF`.
  - Light: warm ivory background, white cards, navy text, teal accent.
  - Status colors (used for subscription lights etc.) must differ in lightness too, not only hue, and read on both themes: green, amber/yellow, red, black (black light gets a light ring in dark mode).
- **Tokens → QSS:** all colors, radii (10–14 px cards, 8 px inputs), spacing (8-pt grid), shadows, font sizes live in `ui/theme/tokens.py`; QSS is generated from tokens. No hard-coded colors in widgets.
- **Typography:** Vazirmatn bundled and registered at startup (Regular/Medium/SemiBold/Bold/ExtraBold). Numbers use tabular figures. Display Persian digits everywhere in the UI; accept Persian, Arabic and Latin digits in every input (normalize).
- **RTL & bidi:** `QApplication.setLayoutDirection(Qt.RightToLeft)`. Wrap every embedded LTR run (numbers with separators, times, ticket numbers, codes, English words, file paths) with Unicode isolates (FSI/LRI … PDI) through a helper `bidi.ltr(text)`. Plates are rendered by a dedicated **PlateWidget** (Iranian plate look) — never as plain text in tables where order can flip. Write unit tests for mixed-direction strings.
- **Layout:** left-to-right mirrored for RTL — sidebar navigation on the right with icons + labels (collapsible), top bar (gate name, server link, clock with Jalali date, operator, theme toggle, global search), content cards. Smooth micro-animations (QPropertyAnimation: 120–200 ms fades/slides), toasts for confirmations, modal dialogs with blurred/dimmed backdrop.
- **Command palette / global search (Ctrl+K):** type a plate, name, phone, shop, ticket number → jump anywhere.
- **Keyboard first:** Space/Enter = print entry receipt for the plate currently shown (only when focus is not in a text field), F-keys for main actions (documented in a Help → Shortcuts dialog). Never trigger twice for one vehicle within 3 s (debounce, and one open ticket per plate).
- **Accessibility:** min 44 px targets for main operator buttons, contrast ≥ 4.5:1, visible focus rings.
- **Empty/error states** designed, never blank. All user-visible strings in Persian via a translation layer (`i18n/fa.json`) so English can be added in v2.

---

## 4. Functional requirements

### 4.1 Users, roles, permissions, audit
- Login per operator; shift sessions logged (login/logout time, gate).
- **Granular permissions** (checkbox matrix per user, admin can create roles as presets). At minimum:
  change tariffs · adjust/waive night fines · adjust any amount manually · cancel transactions · grant guest access · allow negative subscription · block/unblock plates · manage subscribers · manage shops & wallets · manage ads & coupons · view financial reports · export reports · manage users · backup/restore · close fiscal year · change settings · hardware settings.
- Operators without a permission do not see the related buttons.
- **Nothing is deleted.** "Cancel" (ابطال) requires a reason, keeps the original, and appears in the cancellation report.
- **audit_log** for every change to reference data (who, when, node, old → new values).
- Manual price/fine changes require a reason and appear in the "manual amount changes" report.

### 4.2 Main operator screen (per gate)
- Lane tiles (entry/exit) with live camera preview when cameras exist, last read plate (big PlateWidget), detected vehicle type, confidence, snapshot.
- If the plate belongs to a subscriber / free-access person / blocked plate / debtor → show the matching banner (subscription light color, name, shop, days left; red full-screen alarm for blocked; debt amount for debtors).
- Primary actions (big buttons + shortcuts): **Print entry receipt** (click / Space / Enter) · **Receipt without plate** (camera down or unreadable; plate field printed blank) · **Motorcycle entry** · **Pass-through (عبوری)** · **Scan / type ticket for exit** · **Calculate** · **Cash** · **Card** · **Exit without payment (فرار)** · **Night parking** · **Lost ticket**.
- Side panel: vehicles currently inside (filterable), today's entries, unidentified passes to fix.
- Occupancy per level (inside count vs capacity) and an immutable counter (see 4.14).
- Manual plate entry/correction always possible; corrections store both the camera read and the corrected value.

### 4.3 Entry flow (transient car)
1. Plate arrives (camera) or operator types it → ticket created in `active_sessions`.
2. Receipt printed with one action. Photo saved (if camera).
3. If plate already inside → warn (no duplicate open sessions for one plate).
- Vehicle types: sedan/passenger (سواری), van/pickup (وانت), truck, motorcycle, other. Detected type is editable.

### 4.4 Exit flow
- Scan barcode (or type ticket number, or search plate / today's entries if lost) → show entry & exit time, duration, breakdown (entry fee, extra minutes, night fines, coupons, adjustments), total → choose **Cash** or **Card** → recorded.
- **Payment methods:** Cash, Card (customer card on POS), and "Cash deposited with mall card" (operator swiped the mall's own card for cash received) — reported separately. PC-POS integration later (Phase 10); until then operator records card payments manually.
- **Exit receipt** optional (button), carries the rotating ad (4.11).
- Exit camera read is stored and matched to the session.

### 4.5 Tariff engine (pure, fully unit-tested, all values from settings, versioned)
Default values (all editable in Settings → Tariffs, with effective date; history kept):
- **Car "entry fee" (ورودی): 190,000 Rial** — covers the first **60 minutes**.
- After the first 60 minutes: **hourly rate 190,000 Rial**, charged **per minute**: `extra = ceil(hourly_rate × extra_minutes / 60)`. Duration in whole minutes = `floor(seconds / 60)` (a partial minute is not charged). **Integer arithmetic only — never floats for money.**
- **Rounding:** total rounded **UP** to a multiple of **10,000 Rial** (configurable step).
  - Examples: 5 min → 190,000 · 60 min → 190,000 · 61 min → 200,000 · 72 min → 230,000 · 73 min → 240,000 · 120 min → 380,000.
- **Motorcycle:** flat **200,000 Rial**, no time limit.
- **Night parking fine (جریمه شب‌پارک): 2,000,000 Rial per night** (each closing time crossed while inside). Applies on free days too. Only users with permission may reduce/waive it (reason required). Permanent exemption flag per person/plate (e.g., mall guards' cars), with approver recorded.
- **Free days:** per weekday flags (default Thursday + Friday free) + **holiday calendar** (manual dates). Stay spanning paid and free days: only minutes that fall **inside opening hours on paid days** are chargeable; if chargeable minutes > 0 apply entry fee + per-minute rule on those minutes; add night fines. Example: enter Wednesday 14:00, exit Thursday 11:00 → Wednesday 14:00–20:30 chargeable + 1 night fine; Thursday is free.
- **Pass-through (عبوری):** taxi, courier motorcycle, van unloading, other → free if duration ≤ **20 minutes** (configurable); if longer, automatically priced as a normal transient at exit, with a flag.
- **Subscription:** default **4,000,000 Rial / 30 days**; per-profile price override.
- **Coupons (sold to shops):** make the transient parking fee zero during opening hours, single-use, with expiry; never cover night fines.
- **Tariff change mid-stay:** setting "price by tariff at entry time" or "at exit time" (default: entry time).
- Motorcycle night fine: configurable flag, default ON.
- After-hours arrivals are **security records only** (no charge) unless still inside at next opening, then a flag for the supervisor.

### 4.6 Subscribers, shops, wallets
- **New subscriber form:** first/last name, mobile, brand/shop name, shop location (inside mall / outside mall), vehicle model, plate(s), notes, start date, price. Multiple plates per profile (add later from the profile).
- **Shop account (حساب مغازه):** groups people of one brand; **wallet** with deposits; for each person choose payer = shop or self. Renewals of shop-paid people are debited from the wallet automatically when due (or on supervisor action — setting). Low balance → yellow light on the shop. Printable wallet statement.
- **Subscription counter:** 30 days from payment. Early payment → new end = current end + 30 days (shown in the profile).
- **Status lights next to the profile name:** 🟢 > 5 days left · 🟡 ≤ 5 days · 🔴 ≤ 48 hours · ⚫ expired (thresholds configurable).
- **Negative subscription (اشتراک منفی):** a user with permission can allow an expired subscriber to keep entering ("⚫ مجاز" badge), with optional max days (default 10). When they finally pay, the **distinct days they actually entered** during the overdue period (from traffic records, with photos) are deducted: new end = payment date + 30 − used days. Show the list of those days before confirming.
- **Concurrency rule:** max simultaneous vehicles inside per subscription (default 1). A second plate of the same subscription → alert and treat as transient.
- **Follow-up list (فهرست پیگیری):** only black, red, yellow — sorted by urgency, sectioned. Export to **Excel and Word**, print-ready table: name, brand/shop, phone, plates, end date, days left/negative, amount due, empty "signature / notes" column.
- **Import from Excel** of existing subscribers (provide a template file in `resources/templates/`), with validation report.
- Motorcycle subscriptions supported the same way (manual plate entry allowed, plate optional + description).

### 4.7 Free access (تردد رایگان)
- Categories: **staff (پرسنل)**, **mall owner (صاحب پاساژ)**, **guest (مهمان)**. Staff/owner permanent; guest permits with date range (one-time or multi-day) and auto-expiry. Approver recorded. Recognized like subscribers, logged, and reported.

### 4.8 Blocklist (مسدودی)
- From the menu "Register block": block **per plate or per person** (never whole shop at once). Category: debtor, security, harassment, other + description.
- On arrival: **audible alarm + full-screen red message**. For security categories show a generic message to operators ("ورود ممنوع — با سرپرست تماس بگیرید"); details visible only with permission.
- Log of attempts with photo/time; unblock requires permission + reason.

### 4.9 Debts & fleeing (فراری)
- "Exit without payment" button → amount becomes plate debt. On next arrival at any gate → alert with the debt; collecting it links to the original session. Report: count, lost amount, recovered amount.

### 4.10 Other operational cases
- **Lost ticket:** search by plate or open "today's entries" → reissue **duplicate (المثنی)** printed on the receipt, or calculate & collect without printing. Flag in reports.
- **Night parking:** operator can mark vehicles; at closing a printable list of vehicles still inside for security; next morning the system auto-flags cars that stayed overnight even if not marked.
- **Open sessions report:** tickets with no exit (e.g., left via other gate undetected) → supervisor resolves.
- **After hours (watch mode):** outside configured working hours the server (or a gate PC left on) logs every plate in/out with photos, no receipts. **Morning report** must be acknowledged by the first user who logs in (who/when saved). Blocked plates at night are highlighted.
- **Unidentified passes:** camera saw a vehicle but could not read the plate (fast subscribers) → list with photo; operator fills the plate later.
- **Training mode:** separate sandbox database, big "حالت آموزشی" banner; nothing reaches real data.

### 4.11 Advertising module (تبلیغات)
- **Receipt ad text:** type any text in the Ads menu → printed in the ad section of the mother receipt (multi-line, size normal/large). **Shop logo in the ad section only for premium packages.**
- Each ad has a shop, date range, weekdays (e.g., only Thursdays/Fridays), package, price. Several active ads rotate. **Print count per ad** is stored (proof for the shop).
- **When no ad is active the whole ad section and its divider disappear** (see receipt layout 5.1).
- **Custom receipt templates:** PNG/JPG header images (width 576 px) placed in `templates\` (sub-folders = categories). The app watches the folder: new files appear automatically (**display name = file name**), removed files disappear from the app. Searchable picker with preview, "test print", "edit" (opens the file in Paint), "back to mother receipt". If the selected template file is deleted, fall back to the mother receipt and warn. Color images are converted to 1-bit with a preview that matches the print.
- **Coupons (کوپن پارکینگ رایگان):** shops buy N coupons at a price set by admin (paid directly or from the shop wallet). Each coupon has a unique single-use code + expiry (days configurable) and is printed on the thermal printer (plain or with the shop's template). At exit: scan ticket + coupon → parking fee 0 (opening hours only; night fines still apply). Reports per shop: bought / used / expired / revenue.
- **Exit receipt ads:** rotating shop ads on optional exit receipts.
- **Ad display screen:** optional second monitor at a gate shows a slideshow from `ads\slideshow\` (duration per slide configurable).
- **LED sign text rotation** (when the sign exists, Phase 10).
- **Packages:** Bronze (receipt text), Silver (+ coupons), Gold (+ logo, screen, LED sign) — contents & prices defined in settings.
- **Ad contracts** with start/end and status lights like subscriptions; auto-removal on expiry; **ad calendar** showing sold/free slots (weekends and occasions like Nowruz, Yalda).
- **Shop performance report** (printable, nice): prints of their ad, coupons used, etc.
- **Monthly raffle:** transient receipt numbers of the month enter a draw; sponsor shop name printed; app picks a winner (cryptographically random, logged).
- **Subscription discount for advertisers** (percentage configurable).

### 4.12 Reports (all: daily / monthly / custom range, filter by gate, operator, category; export Excel, Word, PDF; print)
1. Financial (transient, subscriptions, fines, coupons, ads; cash / card / mall-card; per gate & operator)
2. Subscriptions (active, expiring, expired, negative, collected vs due, inside vs outside mall)
3. Subscriber traffic (days & times per subscriber)
4. Plate history (any plate: visits, durations, paid) + frequent visitors (subscription prospects)
5. Free access (staff/owner/guest + approver)
6. Pass-through (by type, operator, duration; over-limit flagged)
7. Fleeing & debts (count, lost, recovered)
8. Cancellations (who, what, when, why)
9. Lost tickets & duplicates
10. Night parking & fines (including waivers/adjustments)
11. Motorcycles
12. Occupancy & peaks (hour × weekday heatmap, % of P1 used — signals when to open P2)
13. **Monthly executive summary** (one page, KPIs, comparison to previous month — the file emailed to the mall owner)
14. Manual amount changes
15. Blocked-plate attempts
16. Receipts without plate
17. Camera accuracy (manual corrections vs reads)
18. After-hours traffic
19. Power outages
20. Ads & coupons; shop wallet statements
21. Open sessions
- **Automatic daily report:** time + target folder configurable, can be disabled; if the PC was off at that time, generate at next start (catch-up).

### 4.13 Backup, restore, fiscal year, maintenance
- **Backup:** manual from menu; automatic at a user-chosen time; **also on application close** (option); **catch-up** at next start if missed. Server: SQL Server `.bak` (via T-SQL `BACKUP DATABASE`). Gates: SQLite online backup API. Plus a zip of the data root's `receipt\`, `templates\`, `ads\`, `config\`. Multiple destinations (e.g., second disk / USB). Retention policy. Warning if no successful backup for > 24 h.
- **Restore** with confirmation + "test restore" (restore to a temp DB and verify integrity).
- **Fiscal year:** define start/end; **close year** → archive becomes read-only; active subscriptions, shop balances, debts, vehicles inside carry over; counters reset; new year opened. Previous years remain viewable/reportable.
- **Immutable counter** of all vehicles for the current fiscal year (total + per category: transient, subscriber, free, pass-through, motorcycle). Nobody can edit it; it resets only when the fiscal year is closed; the final number is archived with the year.
- **Photos:** folder configurable; retention in days (e.g., 30–180) — oldest photos deleted automatically to keep the disk from filling; **photos of fleeing, blocked, night-parking and "keep forever" flagged sessions are never auto-deleted.** Show daily usage (MB/day) and estimated days of capacity. Disk-space alerts.
- **Photo file naming** (searchable in Windows Explorer):
  `photos\1405\07\06\1405-07-06_14-32-10_ورود_یافت‌آباد_12ب345-22_مشترک_علی‌رضایی_پژو206.jpg`
  (Jalali date-time, in/out, gate, plate, category, subscriber name or "گذری", vehicle model; sanitize invalid filename characters).
- Log files rotate (size-capped).

### 4.14 Settings
Mall name & logo (white-label), gates & codes, levels & capacities, cameras per lane, printers per gate, scanner mode, tariffs (versioned), working hours per weekday, free weekdays, holidays, light thresholds, data root & photo retention, backup schedule & destinations, auto report schedule, fiscal year, receipt texts & sections toggles, barcode art, divider style, templates folder, ads, users & permissions, theme default, sync/server address, update share path.

### 4.15 Hardware alerts
Printer offline/out of paper, camera offline, server link down, clock drift, backup missed, disk nearly full, template missing — shown as a non-blocking alert bar and logged.

---

## 5. Receipts (approved design)

Paper 80 mm → printable width **576 px (203 dpi)**. Render the whole receipt with **QPainter into a 1-bit image** (perfect Persian shaping; thermal printers cannot shape Persian text themselves), then print through the Windows printer driver (QPrinter) — optional raw ESC/POS raster mode for printers that need it. The on-screen preview uses the same renderer (what you see = what prints).

### 5.1 Mother entry receipt (top → bottom, centered)
1. **Logo** image (`receipt\logo.png`; approved "wave-sofa" Caspian logo). **No text line "پارکینگ بازار مبل کاسپین".**
2. Ornamental divider (line — small diamond — big diamond — small diamond — line) from `receipt\divider.png` or built-in styles.
3. **Ad section (only if an ad is active):** optional shop logo (premium), shop name (bold), location, highlighted offer pill (inverted black) — **all centered**. Followed by another divider. If no ad → this section and its divider are omitted entirely, leaving exactly one divider between the logo and the vehicle info.
4. **Vehicle info:** Iranian-style **plate frame** (IR strip, `۱۲ ب ۳۴۵`, separator, `ایران ۲۲`); rows with dotted leaders: نوع خودرو, تاریخ ورود (Jalali), ساعت ورود, شماره رسید. For "receipt without plate" the plate frame is empty.
5. Divider.
6. **Artistic Code 128 barcode**: art image (default: pine-tree forest skyline, `receipt\barcode-art\tree.png`) sits directly on top of the bars; bars are always generated (payload changes per ticket), quiet zones kept, module ≥ 3 px. Ticket number printed below.
- **Not printed:** gate name, operator name, tariff section, night-fine text, rules/footer.
- Each section can be toggled; texts editable in settings; "reset to default receipt" button; missing image → section skipped + warning.

### 5.2 Other receipts
- **Duplicate (المثنی):** same as mother + large "المثنی" label.
- **Motorcycle:** same layout, vehicle type motorcycle, plate optional.
- **Exit receipt (optional):** entry/exit time, duration, amount, payment method, rotating ad.
- **Coupon:** coupon code barcode + expiry + shop header/template.
- **Custom template receipts:** template header image replaces the logo/ad area; the app still prints vehicle info + barcode below automatically.

---

## 6. Hardware integration (behind interfaces + simulators)
Every device is an interface with a **simulator implementation** so the whole app can be developed and tested without hardware:
- `PlateSource`: Manual · RTSP/ONVIF camera + ANPR engine plug-in · Smart ANPR camera (HTTP push/SDK). Multi-frame reading with voting (vehicles may pass at ~20 km/h), vehicle-type classification (car/van/truck/motorcycle), confidence threshold, snapshot capture. Start with an open-source Iranian-plate ONNX model if one with a commercial-friendly license exists; otherwise ship the interface + manual mode and document how to add a commercial SDK. Record in DECISIONS.md.
- `Printer`: Windows driver (QPrinter) · ESC/POS raw.
- `Scanner`: HID keyboard wedge (detect scanner bursts by inter-key timing < 35 ms, Enter suffix, normalize Persian/Arabic digits, works regardless of focused widget) · Serial COM. A "scanner test" page.
- `Barrier`: relay board (serial/USB/network). Open on paid/subscriber/allowed; manual open button with log.
- `RFID`: HID or serial readers, UHF long-range readers (TCP). Cards linked to profiles; lost card deactivation.
- `PaymentTerminal` (PC-POS, Iranian PSPs such as Behpardakht/Sepehr/Pardakht Novin): send amount, receive result + trace number. Until available → manual.
- `LedSign`: text rotation (ads, free spaces).
- `AdScreen`: second-monitor slideshow.

---

## 7. Installer & updates
- PyInstaller onedir build → **Inno Setup** installer (Persian + English wizard), desktop shortcut, start-with-Windows option, prerequisite checks (ODBC Driver 18, VC++ runtime).
- **First-run wizard:** role (Server / Gate / Standalone), gate name & code, server address, SQL connection test, data root, printer, scanner, cameras (optional), admin user creation, theme.
- Uninstall **keeps** the data root.
- **Auto-update:** gates check a shared folder on the server for a newer version at startup and install silently (after confirming no open transaction).
- Server background service (Windows service via pywin32) for scheduler, sync hub, watch mode, backups, reports.

---

## 8. Out of scope for v1 (keep extension points, do not build)
Porter/delivery desk, storage rental in P2/P3, Friday bazaar booths, car wash, VIP spots, contracted night parking, column banner ads, selling the software to other malls, traffic analytics for shops, generic "side services" and "space rental & contracts" modules. v2 = white-label rename of the product.

---

## 9. Build phases (each phase ends green: lint + tests + smoke run, then commit, push, tag)

| Phase | Scope |
|---|---|
| 0 | Environment setup, repo, CI-like local scripts, project skeleton |
| 1 | Foundations: config, logging, DB layer (SQLAlchemy models, Alembic, SQLite + SQL Server dialect tests), UUIDv7, Jalali/digits/bidi utils, design system (tokens, dark/light, fonts, icons, base widgets, PlateWidget), app shell (login, sidebar, top bar, command palette), users/permissions, audit log |
| 2 | Tariff engine (pure + exhaustive tests incl. all examples in 4.5) + tariff/working-hours/free-days/holiday settings UI |
| 3 | Gate operations, standalone, manual plate: entry, receipt renderer (5.x), Code128 + ticket scheme + HMAC, printing + preview, scanner input, exit & payments, motorcycle, pass-through, lost ticket, duplicate, cancellation, fleeing/debts, night parking, receipt without plate, inside list, occupancy, immutable counters |
| 4 | Subscribers, shops & wallets, lights, renewals incl. negative, concurrency, follow-up list + exports, Excel import, free access, blocklist |
| 5 | Reports & exports, dashboard, automatic daily report |
| 6 | Backup/restore, fiscal year, maintenance, photo storage/retention/naming, disk alerts, outage log, training mode |
| 7 | Advertising module (4.11) complete |
| 8 | Cameras & ANPR (interfaces, RTSP, engine plug-in, voting, vehicle type, unidentified passes, corrections, accuracy report, simulator) |
| 9 | Server role + multi-gate sync (outbox/inbox, offline, review queue, link status, time sync, after-hours watch service, morning report ack, auto-update share) |
| 10 | Hardware integrations behind flags: barrier, RFID/UHF, LED sign, PC-POS, ad screen |
| 11 | Packaging (PyInstaller + Inno Setup), first-run wizard, 1M-row performance test, final QA, Persian user manual (`docs/USER_MANUAL_FA.md`) and `docs/SITE_SETUP.md` |

## 10. Definition of done (every phase)
- `ruff check` clean, `pytest` green (Qt tests offscreen), new logic covered by tests (tariff/sync/ticket/barcode logic near 100%).
- App launches (smoke test script opens main window offscreen and exits 0).
- Migrations upgrade from the previous phase's schema without data loss.
- `docs/PROGRESS.md` updated (what was built, how to run, known gaps); `docs/DECISIONS.md` for any assumption.
- Commit (conventional commits), push to GitHub, tag `phase-N`.
