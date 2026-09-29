"""Design-system rules: contrast, token-only colors, no brand strings in code."""

from __future__ import annotations

import ast
import itertools
import re
from pathlib import Path

import pytest

from caspian_parking.ui.theme.qss import build_qss
from caspian_parking.ui.theme.tokens import DARK, LIGHT, PALETTES

SRC = Path(__file__).resolve().parents[2] / "src" / "caspian_parking"
HEX = re.compile(r"#[0-9a-fA-F]{3,8}\b")


def _luminance(hex_color: str) -> float:
    value = hex_color.lstrip("#")
    channels = [int(value[i : i + 2], 16) / 255 for i in (0, 2, 4)]
    linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def contrast(a: str, b: str) -> float:
    la, lb = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


TEXT_PAIRS = [
    ("text", "bg"),
    ("text", "surface"),
    ("text", "surface_alt"),
    ("text", "surface_sunken"),
    ("text", "sidebar_bg"),
    ("text", "topbar_bg"),
    ("text", "selection"),
    ("text_muted", "bg"),
    ("text_muted", "surface"),
    ("text_muted", "surface_alt"),
    ("text_muted", "sidebar_bg"),
    ("text_on_accent", "accent"),
    ("text_on_accent", "accent_hover"),
    ("text_on_accent", "accent_pressed"),
    ("text_on_accent", "danger"),
    ("accent_text", "bg"),
    ("accent_text", "surface"),
    ("accent_text", "accent_soft"),
    ("secondary_text", "surface"),
    ("danger_text", "danger_soft"),
    ("danger_text", "surface"),
    ("warning_text", "warning_soft"),
    ("success_text", "success_soft"),
    ("info_text", "info_soft"),
    ("training_text", "training_bg"),
    ("alarm_text", "alarm_bg"),
]


@pytest.mark.parametrize("palette", list(PALETTES.values()), ids=list(PALETTES))
@pytest.mark.parametrize(("fg", "bg"), TEXT_PAIRS)
def test_text_contrast_at_least_4_5(palette, fg, bg):
    ratio = contrast(getattr(palette, fg), getattr(palette, bg))
    assert ratio >= 4.5, f"{palette.name}: {fg} on {bg} = {ratio:.2f}"


@pytest.mark.parametrize("palette", [DARK, LIGHT], ids=["dark", "light"])
def test_status_lights_differ_in_lightness(palette):
    lights = [palette.light_green, palette.light_amber, palette.light_red, palette.light_black]
    values = sorted(_luminance(c) for c in lights)
    gaps = [b - a for a, b in itertools.pairwise(values)]
    assert min(gaps) >= 0.05, gaps


def test_black_light_has_visible_ring_in_dark_mode():
    assert contrast(DARK.light_ring, DARK.surface) >= 3


def test_focus_ring_is_visible():
    for palette in PALETTES.values():
        assert contrast(palette.focus, palette.surface) >= 3


@pytest.mark.parametrize("palette", list(PALETTES.values()), ids=list(PALETTES))
def test_qss_is_fully_substituted(palette):
    qss = build_qss(palette)
    assert "$" not in qss
    assert palette.accent in qss
    assert qss.count("{") == qss.count("}")


def _string_literals(path: Path) -> list[tuple[int, str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return [
        (node.lineno, node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    ]


def test_no_hard_coded_colors_outside_tokens():
    offenders = []
    for path in (SRC / "ui").rglob("*.py"):
        if path.name == "tokens.py":
            continue
        for line, text in _string_literals(path):
            if HEX.search(text) or "rgb(" in text or "rgba(" in text:
                offenders.append(f"{path.relative_to(SRC)}:{line}")
    assert not offenders, offenders


def test_no_qcolor_literals_in_widgets():
    pattern = re.compile(r"QColor\(\s*\d")
    offenders = [
        str(path.relative_to(SRC))
        for path in (SRC / "ui").rglob("*.py")
        if path.name not in ("tokens.py", "basics.py") and pattern.search(path.read_text(encoding="utf-8"))
    ]
    assert not offenders, offenders


BRAND_WORDS = ("Caspian", "کاسپین", "یافت‌آباد", "یافت آباد", "اسدنژاد")


def test_no_brand_strings_in_code():
    offenders = []
    for path in SRC.rglob("*.py"):
        for line, text in _string_literals(path):
            if any(word in text for word in BRAND_WORDS):
                offenders.append(f"{path.relative_to(SRC)}:{line}: {text[:40]!r}")
    assert not offenders, offenders
