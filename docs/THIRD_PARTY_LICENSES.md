# Third‑party licenses

Every dependency must allow closed‑source commercial distribution. Shipped = bundled in the installer.

| Component | Version | License | Shipped | Notes |
|---|---|---|---|---|
| Python (CPython) | 3.12.13 | PSF | yes | |
| PySide6 / Shiboken6 (Qt 6) | 6.11.2 | LGPL‑3.0 | yes | dynamically linked, onedir build keeps Qt DLLs replaceable (LGPL relinking satisfied); Qt source offer in installer docs |
| SQLAlchemy | 2.1.1 | MIT | yes | |
| Alembic | 1.20.0 | MIT | yes | |
| Mako / MarkupSafe | 1.4.3 / 3.0.3 | MIT / BSD‑3 | yes | Alembic dependencies |
| pyodbc | 5.3.0 | MIT | yes | |
| jdatetime / jalali‑core | 6.1.0 / 1.0.0 | PSF / MIT | yes | |
| tzdata | 2026.4 | Apache‑2.0 | yes | IANA time zone data |
| tzlocal | 5.4.4 | MIT | yes | APScheduler dependency |
| openpyxl / et‑xmlfile | 3.1.5 / 2.0.0 | MIT | yes | |
| python‑docx | 1.2.0 | MIT | yes | |
| lxml | 6.1.3 | BSD‑3 | yes | |
| Pillow | 12.3.0 | MIT‑CMU (HPND) | yes | |
| APScheduler | 3.11.3 | MIT | yes | |
| pywin32 | 312 | PSF | yes | |
| pyserial | 3.5 | BSD‑3 | yes | |
| opencv‑python‑headless | 5.0.0.93 | Apache‑2.0 (bundled FFmpeg: LGPL‑2.1, shipped as separate DLLs) | yes | RTSP/ONVIF cameras; headless build (no Qt/GTK GUI inside) |
| NumPy | 2.5.3 | BSD‑3 | yes | OpenCV frames |
| typing‑extensions | 4.16.0 | PSF | yes | |
| Vazirmatn font (5 weights) | 33.003 | SIL OFL 1.1 | yes | `resources/fonts/OFL.txt`; bundled unmodified |
| Lucide icons (SVG subset) | 1.48.0 | ISC | yes | `resources/icons/LICENSE.txt`; recolored at runtime |
| pytest, pluggy, iniconfig | 9.1.1 … | MIT | no | dev only |
| pytest‑qt | 4.5.0 | MIT | no | dev only |
| pytest‑cov / coverage | 7.1.0 / 7.16.2 | MIT / Apache‑2.0 | no | dev only |
| pytest‑xdist / execnet | 3.8.0 / 2.1.2 | MIT | no | dev only (parallel tests) |
| zxing‑cpp | 3.1.1 | Apache‑2.0 | no | dev only (tests decode printed barcodes) |
| hypothesis / sortedcontainers | 6.168.3 / 2.4.0 | MPL‑2.0 / Apache‑2.0 | no | dev only |
| ruff | 0.16.9 | MIT | no | dev only |
| mypy (+ deps) | 2.3.1 | MIT | no | dev only |
| PyInstaller (+ hooks‑contrib) | 6.22.3 / 2026.8 | GPL‑2.0 **with bootloader exception** / GPL‑2.0 + Apache‑2.0 | bootloader only | the exception explicitly allows distributing the built app under any licence; the PyInstaller code itself is not shipped |
| altgraph / pefile / pywin32‑ctypes | 0.17.5 / 2024.8.26 / 0.2.3 | MIT / MIT / BSD‑3 | no | build only |
| Inno Setup | 6.7.3 | Inno Setup License (free, commercial use allowed) | setup stub only | builds the installer |
| Inno Setup Farsi translation (`packaging/Farsi.isl`) | 6.5.0+ | same terms as Inno Setup (user‑contributed translation, credits kept in the file) | in the installer | Persian installer wizard |

## Licence review (Phase 11, 2026‑10‑01)
- Every component shipped in the installer allows closed‑source commercial distribution.
- LGPL parts (Qt via PySide6, FFmpeg inside OpenCV) stay separate, replaceable DLLs in the onedir build (`_internal\`); the installer documentation states where the Qt and FFmpeg sources can be obtained.
- No ANPR model is shipped (see DECISIONS D‑076); a licensed engine is added on site as a plug‑in.
- GPL code (PyInstaller) is used only as a build tool; its bootloader exception covers the bundled bootloader.
