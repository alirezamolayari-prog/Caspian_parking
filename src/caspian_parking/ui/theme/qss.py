"""Qt style sheet generated from design tokens (never edited by hand per widget)."""

from __future__ import annotations

from dataclasses import asdict
from string import Template

from caspian_parking.ui.theme.tokens import FONT_FAMILY, FontSize, Palette, Radius, Size, Space

_TEMPLATE = Template(
    """
* { font-family: "$font"; }
QWidget { color: $text; font-size: ${body}px; }
QMainWindow, QDialog, QWidget#AppRoot { background: $bg; }
QWidget#ScreenRoot { background: $bg; }
QToolTip { background: $surface_alt; color: $text; border: 1px solid $border_strong;
    padding: ${xs}px ${s}px; border-radius: ${r_input}px; }

/* ---- typography roles ---- */
QLabel { background: transparent; }
QLabel[role="display"] { font-size: ${display}px; font-weight: 800; }
QLabel[role="h1"] { font-size: ${h1}px; font-weight: 800; }
QLabel[role="h2"] { font-size: ${h2}px; font-weight: 700; }
QLabel[role="h3"] { font-size: ${h3}px; font-weight: 700; }
QLabel[role="title"] { font-size: ${title}px; font-weight: 600; }
QLabel[role="muted"] { color: $text_muted; }
QLabel[role="caption"] { color: $text_muted; font-size: ${caption}px; }
QLabel[role="accent"] { color: $accent_text; font-weight: 600; }
QLabel[role="danger"] { color: $danger_text; }
QLabel[role="kpi"] { font-size: ${h1}px; font-weight: 800; color: $text; }

/* ---- cards ---- */
QFrame[card="true"] { background: $surface; border: 1px solid $border; border-radius: ${r_card}px; }
QFrame[card="raised"] { background: $surface_alt; border: 1px solid $border; border-radius: ${r_card_lg}px; }
QFrame[divider="true"] { background: $border; border: none; max-height: 1px; min-height: 1px; }

/* ---- buttons ---- */
QPushButton, QToolButton {
    background: $surface_alt; color: $text; border: 1px solid $border;
    border-radius: ${r_button}px; padding: 0 ${l}px; min-height: ${button}px; font-weight: 600;
}
QPushButton:hover, QToolButton:hover { border-color: $border_strong; background: $surface; }
QPushButton:pressed, QToolButton:pressed { background: $surface_sunken; }
QPushButton:focus, QToolButton:focus { border: 2px solid $focus; }
QPushButton:disabled, QToolButton:disabled { color: $text_disabled; background: $surface; border-color: $border; }
QPushButton[variant="primary"] { background: $accent; color: $text_on_accent; border: 1px solid $accent; }
QPushButton[variant="primary"]:hover { background: $accent_hover; border-color: $accent_hover; }
QPushButton[variant="primary"]:pressed { background: $accent_pressed; }
QPushButton[variant="primary"]:focus { border: 2px solid $focus; }
QPushButton[variant="primary"]:disabled { background: $surface_alt; color: $text_disabled; border-color: $border; }
QPushButton[variant="danger"] { background: $danger; color: $text_on_accent; border: 1px solid $danger; }
QPushButton[variant="danger"]:hover { background: $danger_hover; }
QPushButton[variant="secondary"] { background: $surface; color: $secondary_text; border: 1px solid $secondary; }
QPushButton[variant="ghost"], QToolButton[variant="ghost"] { background: transparent; border: 1px solid transparent; }
QPushButton[variant="ghost"]:hover, QToolButton[variant="ghost"]:hover { background: $surface_alt; }
QPushButton[size="lg"] { min-height: ${button_lg}px; font-size: ${body_lg}px; font-weight: 700;
    border-radius: ${r_card}px; padding: 0 ${xl}px; }
QPushButton[size="sm"] { min-height: 30px; padding: 0 ${m}px; font-size: ${small}px; }

/* ---- navigation ---- */
QWidget#Sidebar { background: $sidebar_bg; border-left: 1px solid $border; }
QPushButton[nav="true"] {
    background: transparent; border: none; border-radius: ${r_button}px; color: $text_muted;
    text-align: right; padding: 0 ${m}px; min-height: ${touch}px; font-weight: 600;
}
QPushButton[nav="true"]:hover { background: $surface_alt; color: $text; }
QPushButton[nav="true"]:checked { background: $accent_soft; color: $accent_text; }
QPushButton[nav="true"]:focus { border: 2px solid $focus; }
QWidget#TopBar { background: $topbar_bg; border-bottom: 1px solid $border; }
QLabel#Clock { font-size: ${title}px; font-weight: 700; }

/* ---- chips / badges ---- */
QLabel[chip="neutral"] { background: $surface_alt; color: $text_muted; border: 1px solid $border;
    border-radius: ${r_pill}px; padding: ${xxs}px ${m}px; font-size: ${small}px; }
QLabel[chip="success"] { background: $success_soft; color: $success_text; border-radius: ${r_pill}px;
    padding: ${xxs}px ${m}px; font-size: ${small}px; font-weight: 600; }
QLabel[chip="danger"] { background: $danger_soft; color: $danger_text; border-radius: ${r_pill}px;
    padding: ${xxs}px ${m}px; font-size: ${small}px; font-weight: 600; }
QLabel[chip="warning"] { background: $warning_soft; color: $warning_text; border-radius: ${r_pill}px;
    padding: ${xxs}px ${m}px; font-size: ${small}px; font-weight: 600; }
QLabel[chip="info"] { background: $info_soft; color: $info_text; border-radius: ${r_pill}px;
    padding: ${xxs}px ${m}px; font-size: ${small}px; font-weight: 600; }
QLabel[chip="accent"] { background: $accent_soft; color: $accent_text; border-radius: ${r_pill}px;
    padding: ${xxs}px ${m}px; font-size: ${small}px; font-weight: 600; }

/* ---- banners / alert bar ---- */
QFrame[banner="warning"] { background: $warning_soft; border: 1px solid $border; border-radius: ${r_button}px; }
QFrame[banner="danger"] { background: $danger_soft; border: 1px solid $border; border-radius: ${r_button}px; }
QFrame[banner="info"] { background: $info_soft; border: 1px solid $border; border-radius: ${r_button}px; }
QFrame[banner="success"] { background: $success_soft; border: 1px solid $border; border-radius: ${r_button}px; }
QFrame[banner="warning"] QLabel { color: $warning_text; }
QFrame[banner="danger"] QLabel { color: $danger_text; }
QFrame[banner="info"] QLabel { color: $info_text; }
QFrame[banner="success"] QLabel { color: $success_text; }
QLabel#TrainingBanner { background: $training_bg; color: $training_text; font-weight: 800;
    font-size: ${body_lg}px; padding: ${s}px; }
QFrame#Toast { background: $surface_alt; border: 1px solid $border_strong; border-radius: ${r_card}px; }
QFrame#Toast QLabel { color: $text; font-weight: 600; }
QFrame#Toast[kind="success"] { border-color: $success_text; }
QFrame#Toast[kind="error"] { border-color: $danger_text; }
QFrame#Toast[kind="warning"] { border-color: $warning_text; }
QWidget#Alarm { background: $alarm_bg; }
QWidget#Alarm QLabel { color: $alarm_text; }

/* ---- inputs ---- */
QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QComboBox, QDateEdit, QTimeEdit {
    background: $surface_sunken; color: $text; border: 1px solid $border_strong;
    border-radius: ${r_input}px; padding: ${xs}px ${m}px; min-height: ${input_h}px;
    selection-background-color: $selection; selection-color: $text;
}
QPlainTextEdit, QTextEdit { padding: ${s}px; }
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus, QSpinBox:focus, QComboBox:focus,
QDateEdit:focus, QTimeEdit:focus { border: 2px solid $focus; }
QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled { color: $text_disabled; background: $surface; }
QLineEdit[size="lg"] { font-size: ${h3}px; min-height: ${button_lg}px; font-weight: 700; }
QLineEdit[invalid="true"] { border: 2px solid $danger; }
QComboBox::drop-down { border: none; width: 28px; }
QComboBox QAbstractItemView { background: $surface; color: $text; border: 1px solid $border_strong;
    selection-background-color: $selection; outline: none; }
QAbstractSpinBox::up-button, QAbstractSpinBox::down-button { width: 0; border: none; }
QCheckBox, QRadioButton { spacing: ${s}px; background: transparent; }
QCheckBox::indicator, QRadioButton::indicator { width: 18px; height: 18px; border: 1px solid $border_strong;
    background: $surface_sunken; border-radius: 4px; }
QRadioButton::indicator { border-radius: 9px; }
QCheckBox::indicator:checked, QRadioButton::indicator:checked { background: $accent; border-color: $accent; }
QCheckBox:focus, QRadioButton:focus { color: $accent_text; }

/* ---- tables & lists ---- */
QTableView, QTreeView, QListView, QListWidget {
    background: $surface; alternate-background-color: $surface_alt; color: $text;
    border: 1px solid $border; border-radius: ${r_card}px; gridline-color: $border;
    selection-background-color: $selection; selection-color: $text; outline: none;
}
QTableView::item, QListView::item, QListWidget::item { padding: ${xs}px ${s}px; }
QListWidget::item { min-height: ${touch}px; border-radius: ${r_input}px; }
QListWidget::item:selected { background: $selection; color: $text; }
QListWidget::item:hover { background: $surface_alt; }
QHeaderView::section { background: $surface_alt; color: $text_muted; border: none;
    border-bottom: 1px solid $border; padding: ${s}px; font-weight: 700; font-size: ${small}px; }
QTableCornerButton::section { background: $surface_alt; border: none; }

/* ---- tabs ---- */
QTabWidget::pane { border: 1px solid $border; border-radius: ${r_card}px; background: $surface; top: -1px; }
QTabBar::tab { background: transparent; color: $text_muted; padding: ${s}px ${l}px; margin: 0 ${xxs}px;
    border-bottom: 2px solid transparent; font-weight: 600; }
QTabBar::tab:selected { color: $accent_text; border-bottom: 2px solid $accent; }
QTabBar::tab:hover { color: $text; }

/* ---- menus ---- */
QMenu { background: $surface; color: $text; border: 1px solid $border_strong; border-radius: ${r_input}px;
    padding: ${xs}px; }
QMenu::item { padding: ${s}px ${l}px; border-radius: 6px; }
QMenu::item:selected { background: $selection; }
QMenu::separator { height: 1px; background: $border; margin: ${xs}px ${s}px; }

/* ---- scrollbars ---- */
QScrollBar:vertical { background: transparent; width: 10px; margin: 2px; }
QScrollBar::handle:vertical { background: $border_strong; border-radius: 4px; min-height: 32px; }
QScrollBar:horizontal { background: transparent; height: 10px; margin: 2px; }
QScrollBar::handle:horizontal { background: $border_strong; border-radius: 4px; min-width: 32px; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }
QScrollArea { background: transparent; border: none; }
QScrollArea > QWidget > QWidget { background: transparent; }

/* ---- command palette ---- */
QFrame#Palette { background: $surface; border: 1px solid $border_strong; border-radius: ${r_card_lg}px; }
QFrame#Palette QLineEdit { font-size: ${title}px; min-height: ${button_lg}px; }
QFrame#Palette QListWidget { border: none; background: transparent; }
"""
)


def build_qss(palette: Palette) -> str:
    values: dict[str, object] = {k: v for k, v in asdict(palette).items() if isinstance(v, str)}
    values.update(
        font=FONT_FAMILY,
        caption=FontSize.CAPTION,
        small=FontSize.SMALL,
        body=FontSize.BODY,
        body_lg=FontSize.BODY_LG,
        title=FontSize.TITLE,
        h3=FontSize.H3,
        h2=FontSize.H2,
        h1=FontSize.H1,
        display=FontSize.DISPLAY,
        xxs=Space.XXS,
        xs=Space.XS,
        s=Space.S,
        m=Space.M,
        l=Space.L,
        xl=Space.XL,
        r_input=Radius.INPUT,
        r_button=Radius.BUTTON,
        r_card=Radius.CARD,
        r_card_lg=Radius.CARD_LG,
        r_pill=Radius.PILL,
        button=Size.BUTTON,
        button_lg=Size.BUTTON_LG,
        input_h=Size.INPUT,
        touch=Size.TOUCH_MIN,
    )
    return _TEMPLATE.substitute(values)
