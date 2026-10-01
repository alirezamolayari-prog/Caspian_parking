"""Design tokens: the only place where colors, radii, spacing and type sizes are defined.

Brand palette (approved logo): navy #13324A, teal #1F7A7A, walnut #9A6034, ivory #F5F0E6.
Widgets read tokens through the ThemeManager; QSS is generated from these values.
"""

from __future__ import annotations

from dataclasses import dataclass, fields

BRAND_NAVY = "#13324A"
BRAND_TEAL = "#1F7A7A"
BRAND_WALNUT = "#9A6034"
BRAND_IVORY = "#F5F0E6"


@dataclass(frozen=True)
class Palette:
    name: str
    is_dark: bool
    # surfaces
    bg: str
    surface: str
    surface_alt: str
    surface_sunken: str
    sidebar_bg: str
    topbar_bg: str
    border: str
    border_strong: str
    # text
    text: str
    text_muted: str
    text_disabled: str
    text_on_accent: str
    # accent (teal) and secondary (walnut)
    accent: str
    accent_hover: str
    accent_pressed: str
    accent_soft: str
    accent_text: str
    secondary: str
    secondary_text: str
    # feedback
    danger: str
    danger_hover: str
    danger_soft: str
    danger_text: str
    warning_soft: str
    warning_text: str
    success_soft: str
    success_text: str
    info_soft: str
    info_text: str
    # subscription / status lights (differ in lightness, not only hue)
    light_green: str
    light_amber: str
    light_red: str
    light_black: str
    light_ring: str
    # misc
    focus: str
    overlay: str
    shadow: str
    selection: str
    training_bg: str
    training_text: str
    alarm_bg: str
    alarm_text: str


DARK = Palette(
    name="dark",
    is_dark=True,
    bg="#0E1620",
    surface="#141F2B",
    surface_alt="#1B2836",
    surface_sunken="#0B121A",
    sidebar_bg="#101A25",
    topbar_bg="#111C27",
    border="#263647",
    border_strong="#3A4E63",
    text="#E8EEF3",
    text_muted="#9FB0BF",
    text_disabled="#5E6F7F",
    text_on_accent="#FFFFFF",
    accent=BRAND_TEAL,
    accent_hover="#1B6E6E",
    accent_pressed="#196666",
    accent_soft="#15383D",
    accent_text="#5CC4C0",
    secondary=BRAND_WALNUT,
    secondary_text="#D9A477",
    danger="#C93A3F",
    danger_hover="#D8474C",
    danger_soft="#3A1A1E",
    danger_text="#FF8A8E",
    warning_soft="#3A2E12",
    warning_text="#F5C76A",
    success_soft="#12352A",
    success_text="#6FDDA6",
    info_soft="#14304A",
    info_text="#8CC4F2",
    light_green="#34C27A",
    light_amber="#F2B230",
    light_red="#E0484D",
    light_black="#050505",
    light_ring="#E8EEF3",
    focus="#5CC4C0",
    overlay="rgba(4, 9, 14, 0.62)",
    shadow="rgba(0, 0, 0, 0.45)",
    selection="#1F5C5C",
    training_bg="#9A6034",
    training_text="#FFFFFF",
    alarm_bg="#A3151B",
    alarm_text="#FFFFFF",
)

LIGHT = Palette(
    name="light",
    is_dark=False,
    bg=BRAND_IVORY,
    surface="#FFFFFF",
    surface_alt="#FBF8F2",
    surface_sunken="#FFFFFF",
    sidebar_bg="#EFE8DA",
    topbar_bg="#FFFFFF",
    border="#E3DACA",
    border_strong="#C9BCA5",
    text=BRAND_NAVY,
    text_muted="#55697A",
    text_disabled="#9AA6B0",
    text_on_accent="#FFFFFF",
    accent=BRAND_TEAL,
    accent_hover="#1A6B6B",
    accent_pressed="#155959",
    accent_soft="#DCEFEE",
    accent_text="#16625F",
    secondary=BRAND_WALNUT,
    secondary_text="#7E4B24",
    danger="#C62F35",
    danger_hover="#B0262C",
    danger_soft="#FBE3E3",
    danger_text="#A5242A",
    warning_soft="#FCF0D4",
    warning_text="#7A5300",
    success_soft="#DDF3E6",
    success_text="#17613A",
    info_soft="#E1EEF9",
    info_text="#1D4F7A",
    light_green="#2DB36B",
    light_amber="#F0B429",
    light_red="#C62F35",
    light_black="#111111",
    light_ring=BRAND_NAVY,
    focus=BRAND_TEAL,
    overlay="rgba(19, 50, 74, 0.38)",
    shadow="rgba(19, 50, 74, 0.16)",
    selection="#CFE8E6",
    training_bg="#9A6034",
    training_text="#FFFFFF",
    alarm_bg="#B3121A",
    alarm_text="#FFFFFF",
)

PALETTES: dict[str, Palette] = {"dark": DARK, "light": LIGHT}


def palette_fields() -> list[str]:
    return [f.name for f in fields(Palette) if f.name not in ("name", "is_dark")]


@dataclass(frozen=True)
class PlateColors:
    """Physical Iranian plate colors: identical in both themes."""

    body: str = "#FFFFFF"
    ink: str = "#000000"
    frame: str = "#111111"
    strip: str = "#0B3D91"
    strip_ink: str = "#FFFFFF"
    empty_ink: str = "#8A8A8A"


PLATE = PlateColors()

# Receipt ink (1-bit thermal output).
RECEIPT_PAPER = "#FFFFFF"
RECEIPT_INK = "#000000"


class Space:
    """8-pt spacing grid."""

    XXS = 2
    XS = 4
    S = 8
    M = 12
    L = 16
    XL = 24
    XXL = 32
    XXXL = 48


class Radius:
    INPUT = 8
    BUTTON = 10
    CARD = 12
    CARD_LG = 14
    PILL = 12  # chips are ~24 px tall; Qt ignores radii larger than half the height


class FontSize:
    CAPTION = 12
    SMALL = 13
    BODY = 14
    BODY_LG = 16
    TITLE = 18
    H3 = 20
    H2 = 24
    H1 = 30
    DISPLAY = 44


class Size:
    TOUCH_MIN = 44  # minimum target for main operator buttons (SPEC §3)
    BUTTON = 38
    BUTTON_LG = 56
    INPUT = 38
    TOPBAR = 60
    SIDEBAR = 232
    SIDEBAR_COLLAPSED = 68
    ICON = 20
    ICON_LG = 28
    ROW = 40
    CHIP = 26
    SLIDESHOW_W = 960  # windowed ad slideshow when there is no second monitor
    SLIDESHOW_H = 540
    TEMPLATE_PREVIEW = 300  # receipt template / coupon preview width
    CAMERA_PREVIEW_H = 170  # live camera preview in a lane tile


class Motion:
    FAST = 120
    NORMAL = 160
    SLOW = 200
    TOAST_MS = 3200


FONT_FAMILY = "Vazirmatn"
FONT_WEIGHTS = ("Regular", "Medium", "SemiBold", "Bold", "ExtraBold")
