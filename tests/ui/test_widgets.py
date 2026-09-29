from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import QModelIndex, Qt
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QWidget

from caspian_parking.core.plate import parse_plate
from caspian_parking.ui.theme.icons import icon, icon_exists, pixmap
from caspian_parking.ui.theme.manager import FONT_FAMILY, load_fonts
from caspian_parking.ui.theme.tokens import PLATE, Size
from caspian_parking.ui.widgets.basics import Button, Card, TextField, chip, qcolor
from caspian_parking.ui.widgets.feedback import Alert, AlertBar, EmptyState, ModalDialog, StatusLight, Toast
from caspian_parking.ui.widgets.plate import PlateStyle, PlateWidget, render_plate_image
from caspian_parking.ui.widgets.table import Column, DataTable, LazyTableModel

ARTIFACTS = Path(__file__).resolve().parents[1] / "artifacts"


def test_fonts_are_bundled_and_registered(themed):
    assert load_fonts()
    from PySide6.QtGui import QFontDatabase

    assert FONT_FAMILY in QFontDatabase.families()


def test_icons_render_in_theme_color(themed):
    assert icon_exists("car")
    assert not icon_exists("no-such-icon")
    pm = pixmap("car", "accent", 24)
    assert not pm.isNull()
    assert not icon("printer").isNull()


@pytest.mark.parametrize(
    ("text", "name"),
    [("12ب345-22", "car"), ("12الف345-11", "car_alef"), ("123-45678", "moto"), ("TEMP9", "free"), (None, "empty")],
)
def test_plate_rendering(themed, text, name):
    plate = parse_plate(text) if text else None
    image = render_plate_image(plate, height=110)
    ARTIFACTS.mkdir(exist_ok=True)
    image.save(str(ARTIFACTS / f"plate_{name}.png"))
    assert image.height() == 110
    expected_width = 176 if plate is not None and plate.kind.value == "motorcycle" else 517
    assert image.width() == expected_width
    # the white plate body is painted in the middle
    center = image.pixelColor(image.width() // 2, 8)
    assert center.alpha() == 255


def test_plate_monochrome_style_uses_only_black_and_white(themed):
    image = render_plate_image(parse_plate("12ب345-22"), style=PlateStyle.monochrome())
    mono = image.convertToFormat(QImage.Format.Format_Mono)
    assert mono.width() == image.width()
    strip_pixel = image.pixelColor(20, 20)
    assert strip_pixel.red() < 40 and strip_pixel.alpha() == 255


def test_plate_widget_sizes(qtbot, themed):
    widget = PlateWidget(parse_plate("12ب345-22"), height=80)
    qtbot.addWidget(widget)
    assert widget.width() == round(80 * 4.7)
    widget.set_plate(parse_plate("123-45678"))
    assert widget.width() == round(80 * 1.6)
    widget.set_plate(None)
    assert widget.plate() is None
    widget.show()
    assert not widget.grab().isNull()
    assert PLATE.body == "#FFFFFF"


def test_text_field_normalizes(qtbot, themed):
    field = TextField()
    qtbot.addWidget(field)
    qtbot.keyClicks(field, "12")  # real key events (Latin keyboard)
    assert field.text() == "۱۲"
    # QTest cannot synthesize Arabic-script key events offscreen; feed them through the same signal.
    field.setText(field.text() + "٣4ي")
    field.textEdited.emit(field.text())
    assert field.text() == "۱۲۳۴ی"
    assert field.value() == "1234ی"
    latin = TextField(persian_digits=False)
    qtbot.addWidget(latin)
    qtbot.keyClicks(latin, "ab12")
    assert latin.text() == "ab12"
    field.set_invalid(True)
    assert field.property("invalid") == "true"


def test_buttons_and_cards(qtbot, themed):
    clicked = []
    button = Button("ورود", "log-in", variant="primary", size="lg", on_click=lambda: clicked.append(1))
    qtbot.addWidget(button)
    assert button.minimumHeight() >= Size.TOUCH_MIN
    qtbot.mouseClick(button, Qt.MouseButton.LeftButton)
    assert clicked == [1]
    themed.toggle()
    assert not button.icon().isNull()
    card = Card("عنوان", raised=True)
    qtbot.addWidget(card)
    assert card.title_label is not None
    assert chip("x").height() == Size.CHIP


def test_qcolor_parses_tokens():
    assert qcolor("#1F7A7A") == QColor("#1F7A7A")
    rgba = qcolor("rgba(4, 9, 14, 0.5)")
    assert (rgba.red(), rgba.green(), rgba.blue(), rgba.alpha()) == (4, 9, 14, 128)


def test_status_light_and_alert_bar(qtbot, themed):
    light = StatusLight("black")
    qtbot.addWidget(light)
    light.show()
    assert light.toolTip()
    light.set_status("amber")
    assert light.status() == "amber"
    bar = AlertBar()
    qtbot.addWidget(bar)
    bar.raise_alert(Alert("printer", "چاپگر در دسترس نیست", "danger"))
    bar.raise_alert(Alert("disk", "فضای دیسک کم است"))
    assert len(bar.alerts()) == 2
    assert not bar.isHidden()
    bar.clear_alert("printer")
    bar.clear_alert("disk")
    assert bar.isHidden()


def test_toast_and_empty_state(qtbot, themed):
    host = QWidget()
    qtbot.addWidget(host)
    host.resize(600, 400)
    host.show()
    toast = Toast(host, "ذخیره شد", "success", duration=50)
    assert toast.isVisible()
    empty = EmptyState("history", "خالی", "توضیح")
    qtbot.addWidget(empty)
    assert empty.title_label.text() == "خالی"


def test_modal_dialog_dims_parent(qtbot, themed):
    from PySide6.QtWidgets import QMainWindow

    window = QMainWindow()
    window.setCentralWidget(QWidget())
    qtbot.addWidget(window)
    window.resize(800, 600)
    window.show()
    dialog = ModalDialog(window, "عنوان")
    dialog.add_button("بستن", role="reject")
    dialog.show()
    assert window.centralWidget().graphicsEffect() is not None
    dialog.reject()
    assert window.centralWidget().graphicsEffect() is None


def test_lazy_table_fetches_in_batches(qtbot, themed):
    data = list(range(1050))
    calls = []

    def fetch(offset, limit):
        calls.append((offset, limit))
        return data[offset : offset + limit]

    model = LazyTableModel([Column("n", lambda r: r)], fetch, batch=200)
    model.reset()
    assert model.rowCount() == 200
    assert calls == [(0, 200)]
    while model.canFetchMore(QModelIndex()):
        model.fetchMore(QModelIndex())
    assert model.rowCount() == 1050
    assert calls[-1] == (1000, 200)
    view = DataTable(model)
    qtbot.addWidget(view)
    assert model.data(model.index(5, 0)) == "5"
    assert model.headerData(0, Qt.Orientation.Horizontal) == "n"
    view.selectRow(3)
    assert view.selected_object() == 3


def _icon_literals():
    """Icon names passed as literals to Button/icon/pixmap/EmptyState/ScreenSpec anywhere in the code."""
    import ast

    src = Path(__file__).resolve().parents[2] / "src" / "caspian_parking"
    positions = {"icon": 0, "pixmap": 0, "EmptyState": 0, "Button": 1, "ScreenSpec": 2}
    found = set()
    for path in src.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Call):
                continue
            name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            if name not in positions:
                continue
            candidates = [kw.value for kw in node.keywords if kw.arg in ("icon_name", "name")]
            if len(node.args) > positions[name]:
                candidates.append(node.args[positions[name]])
            for value in candidates:
                if isinstance(value, ast.Constant) and isinstance(value.value, str):
                    found.add((path.name, value.value))
    return sorted(found)


def test_every_icon_used_in_code_is_bundled():
    literals = _icon_literals()
    assert len(literals) > 10
    missing = [f"{file}: {name}" for file, name in literals if not icon_exists(name)]
    assert not missing, missing


def test_missing_icon_falls_back(themed):
    assert not pixmap("definitely-not-an-icon").isNull()
