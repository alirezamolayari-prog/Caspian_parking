"""Build helpers: the application icon (from the bundled Lucide icon) and the Windows version resource.

Usage: .venv\\Scripts\\python.exe packaging\\make_assets.py build
Names and version come from app_defaults.json and caspian_parking.version (no brand strings here).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from PySide6.QtCore import QByteArray, QRectF, Qt  # noqa: E402
from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter, QPainterPath  # noqa: E402
from PySide6.QtSvg import QSvgRenderer  # noqa: E402

from caspian_parking.config.defaults import app_defaults, product_name  # noqa: E402
from caspian_parking.ui.theme.tokens import DARK  # noqa: E402
from caspian_parking.version import __version__  # noqa: E402

ICON_SIZES = (16, 24, 32, 48, 64, 128, 256)


def make_icon(target: Path) -> Path:
    svg = (ROOT / "src" / "caspian_parking" / "resources" / "icons" / "circle-parking.svg").read_text("utf-8")
    svg = svg.replace("currentColor", "#FFFFFF")
    renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    images = []
    for size in ICON_SIZES:
        image = QImage(size, size, QImage.Format.Format_ARGB32)
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(QRectF(0, 0, size, size), size * 0.22, size * 0.22)
        painter.fillPath(path, QColor(DARK.accent))
        margin = size * 0.14
        renderer.render(painter, QRectF(margin, margin, size - 2 * margin, size - 2 * margin))
        painter.end()
        images.append(image)
    target.parent.mkdir(parents=True, exist_ok=True)
    # Qt writes a single-size ICO; Pillow packs all sizes into one file
    from PIL import Image

    pngs = []
    for image in images:
        png = target.parent / f"icon_{image.width()}.png"
        image.save(str(png))
        pngs.append(Image.open(png))
    pngs[-1].save(target, format="ICO", sizes=[(s, s) for s in ICON_SIZES])
    return target


def make_version_info(target: Path) -> Path:
    numbers = [int(part) for part in __version__.split(".")] + [0] * 4
    version_tuple = tuple(numbers[:4])
    name = product_name()
    text = f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers={version_tuple}, prodvers={version_tuple}, mask=0x3f, flags=0x0, OS=0x40004,
                    fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('ProductName', '{name}'),
      StringStruct('FileDescription', '{name}'),
      StringStruct('FileVersion', '{__version__}'),
      StringStruct('ProductVersion', '{__version__}'),
      StringStruct('InternalName', 'parking'),
      StringStruct('OriginalFilename', 'parking.exe')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"""
    target.write_text(text, encoding="utf-8")
    return target


def main(argv: list[str]) -> int:
    out = Path(argv[0]) if argv else ROOT / "build"
    app = QGuiApplication.instance() or QGuiApplication([])
    make_icon(out / "app.ico")
    make_version_info(out / "version_info.txt")
    folder = str(app_defaults()["product_folder"])
    (out / "build_vars.txt").write_text(
        f"AppName={product_name()}\nAppFolder={folder}\nAppVersion={__version__}\n", encoding="utf-8"
    )
    del app
    print(f"assets written to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
