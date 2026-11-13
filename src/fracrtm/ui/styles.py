from __future__ import annotations


class UiPalette:
    DARK_TEAL = "#17383D"
    ACCENT_TEAL = "#2D7C78"
    PAGE_BACKGROUND = "#F4F6F7"
    CARD_BACKGROUND = "#FFFFFF"
    BORDER = "#D7DEE2"
    INPUT_BORDER = "#CAD3D7"
    TEXT = "#1F2D33"
    MUTED = "#5F6B70"
    READY = "#27805E"
    STALE = "#B7791F"
    NEUTRAL = "#77858A"
    FAILED = "#B64242"


class UiTypography:
    FAMILY = "Microsoft YaHei UI"
    BRAND = 25
    PAGE_TITLE = 24
    NAV_GROUP = 21
    NAV_ITEM = 18
    CARD_TITLE = 19
    BODY = 18
    HELPER = 16


class UiMetrics:
    MAIN_WINDOW_WIDTH = 1600
    MAIN_WINDOW_HEIGHT = 950
    MINIMUM_WINDOW_WIDTH = 1280
    MINIMUM_WINDOW_HEIGHT = 800
    SIDEBAR_WIDTH = 260
    PAGE_MARGIN = 24
    CARD_GAP = 16
    CARD_PADDING = 16
    OVERVIEW_LABEL_WIDTH = 190
    OVERVIEW_COLUMN_SPACING = 28
    OVERVIEW_ROW_SPACING = 10
    OVERVIEW_ACTION_SPACING = 16
    SPATIAL_LABEL_WIDTH = 190
    SPATIAL_COLUMN_SPACING = 28
    SPATIAL_ROW_SPACING = 10
    INPUT_HEIGHT = 40
    BUTTON_HEIGHT = 40
    CARD_RADIUS = 6
    NAV_ITEM_HEIGHT = 44


APP_STYLE = f"""
* {{
    font-family: \"{UiTypography.FAMILY}\";
    font-size: {UiTypography.BODY}px;
    color:{UiPalette.TEXT};
}}
QMainWindow {{
    background:{UiPalette.PAGE_BACKGROUND};
}}
QWidget#MainContent,QWidget#PageContent {{
    background:{UiPalette.PAGE_BACKGROUND};
}}
QFrame#Sidebar {{
    background:{UiPalette.DARK_TEAL};
    border:none;
}}
QLabel#Brand {{
    background:transparent;
    color:#F4F7F7;
    font-size:{UiTypography.BRAND}px;
    font-weight:600;
    padding:0;
}}
QLabel#SideHead {{
    background:transparent;
    color:rgba(255,255,255,0.74);
    font-size:{UiTypography.NAV_GROUP}px;
    font-weight:600;
    padding:12px 10px 3px 10px;
}}
QPushButton#NavButton {{
    min-height:{UiMetrics.NAV_ITEM_HEIGHT}px;
    border:none;
    border-radius:6px;
    padding:0 12px;
    text-align:left;
    color:#D8E3E5;
    background:transparent;
    font-size:{UiTypography.NAV_ITEM}px;
}}
QPushButton#NavButton:hover {{
    background:#21484D;
}}
QPushButton#NavButton:checked {{
    background:{UiPalette.ACCENT_TEAL};
    color:white;
    font-weight:600;
}}
QFrame#Card {{
    background:{UiPalette.CARD_BACKGROUND};
    border:1px solid {UiPalette.BORDER};
    border-radius:{UiMetrics.CARD_RADIUS}px;
}}
QLabel#PageTitle {{
    font-size:{UiTypography.PAGE_TITLE}px;
    font-weight:700;
    color:{UiPalette.TEXT};
}}
QLabel#SectionTitle {{
    font-size:{UiTypography.CARD_TITLE}px;
    font-weight:600;
    color:{UiPalette.TEXT};
    padding-bottom:2px;
}}
QLabel#MutedLabel,QLabel#PageDescription {{
    color:{UiPalette.MUTED};
    font-size:{UiTypography.HELPER}px;
}}
QLabel#Monospace,QPlainTextEdit {{
    font-family:Consolas;
    font-size:{UiTypography.HELPER}px;
}}
QPushButton {{
    min-height:{UiMetrics.BUTTON_HEIGHT}px;
    border:1px solid {UiPalette.INPUT_BORDER};
    border-radius:5px;
    background:#FFFFFF;
    padding:0 16px;
}}
QPushButton:hover {{
    border-color:{UiPalette.ACCENT_TEAL};
    color:#256A67;
}}
QPushButton#PrimaryButton {{
    color:white;
    background:{UiPalette.ACCENT_TEAL};
    border-color:{UiPalette.ACCENT_TEAL};
    font-weight:600;
}}
QPushButton#PrimaryButton:hover {{
    background:#256A67;
}}
QPushButton:disabled {{
    color:#9AA8AC;
    background:#EDF1F2;
    border-color:#E2E8EA;
}}
QLineEdit,QSpinBox,QDoubleSpinBox,QComboBox {{
    min-height:{UiMetrics.INPUT_HEIGHT}px;
    background:white;
    border:1px solid {UiPalette.INPUT_BORDER};
    border-radius:5px;
    padding:0 8px;
}}
QLineEdit:focus,QSpinBox:focus,QDoubleSpinBox:focus,QComboBox:focus {{
    border-color:{UiPalette.ACCENT_TEAL};
}}
QLabel,QGroupBox,QCheckBox,QRadioButton,QTableView,QTreeView,QListWidget,QTabBar {{
    font-size:{UiTypography.BODY}px;
}}
QGroupBox::title {{
    font-size:{UiTypography.CARD_TITLE}px;
    font-weight:600;
}}
QCheckBox,QRadioButton {{
    spacing:8px;
}}
QTabWidget::pane {{
    border:1px solid {UiPalette.BORDER};
    background:#FFFFFF;
}}
QTabBar::tab {{
    padding:9px 18px;
    color:{UiPalette.MUTED};
}}
QTabBar::tab:selected {{
    color:{UiPalette.ACCENT_TEAL};
    border-bottom:2px solid {UiPalette.ACCENT_TEAL};
    font-weight:600;
}}
"""


DIALOG_STYLE = f"""
QDialog {{
    background: {UiPalette.PAGE_BACKGROUND};
    color: {UiPalette.TEXT};
    font-family: "Microsoft YaHei UI", "Segoe UI", sans-serif;
    font-size: {UiTypography.BODY}px;
}}
QWidget#PagePanel, QFrame#Card {{
    background: {UiPalette.CARD_BACKGROUND};
    border: 1px solid {UiPalette.BORDER};
    border-radius: {UiMetrics.CARD_RADIUS}px;
}}
QLabel#PageTitle {{
    color: {UiPalette.TEXT};
    font-size: {UiTypography.PAGE_TITLE}px;
    font-weight: 600;
}}
QLabel#PageDescription, QLabel#MutedLabel {{
    color: {UiPalette.MUTED};
}}
QLabel#SectionTitle {{
    color: {UiPalette.TEXT};
    font-size: {UiTypography.CARD_TITLE}px;
    font-weight: 600;
}}
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QListWidget, QTableWidget {{
    background: white;
    color: {UiPalette.TEXT};
    border: 1px solid {UiPalette.BORDER};
    border-radius: 5px;
    min-height: {UiMetrics.INPUT_HEIGHT}px;
    padding: 4px 8px;
    font-size: {UiTypography.BODY}px;
    selection-background-color: #dfeee8;
    selection-color: {UiPalette.TEXT};
}}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {{
    border: 1px solid {UiPalette.ACCENT_TEAL};
}}
QPushButton {{
    min-height: {UiMetrics.BUTTON_HEIGHT}px;
    padding: 2px 18px;
    border: 1px solid {UiPalette.BORDER};
    border-radius: 5px;
    background: white;
    color: {UiPalette.TEXT};
    font-size: {UiTypography.BODY}px;
}}
QPushButton:hover {{
    border-color: {UiPalette.ACCENT_TEAL};
    color: {"#256A67"};
}}
QPushButton#PrimaryButton {{
    background: {UiPalette.ACCENT_TEAL};
    border-color: {UiPalette.ACCENT_TEAL};
    color: white;
    font-weight: 600;
}}
QPushButton#PrimaryButton:hover {{
    background: {"#256A67"};
}}
QPushButton#DangerButton {{
    color: {UiPalette.FAILED};
}}
QListWidget#Navigation {{
    background: #eef2f1;
    border: none;
    border-radius: 8px;
    padding: 6px;
    outline: none;
}}
QListWidget#Navigation::item {{
    min-height: 48px;
    padding-left: 13px;
    border-radius: 6px;
    color: #4e5e67;
}}
QListWidget#Navigation::item:selected {{
    background: white;
    color: {"#256A67"};
    font-weight: 600;
}}
QListWidget#Navigation::item:disabled {{
    color: #a7b0b5;
}}
QGroupBox {{
    border: 1px solid {UiPalette.BORDER};
    border-radius: 7px;
    margin-top: 12px;
    padding: 14px 10px 8px 10px;
    background: white;
    font-weight: 600;
    font-size: {UiTypography.CARD_TITLE}px;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 10px;
    padding: 0 5px;
}}
QHeaderView::section {{
    background: #eef2f1;
    color: {UiPalette.TEXT};
    padding: 7px;
    border: none;
    border-bottom: 1px solid {UiPalette.BORDER};
    font-weight: 600;
}}
QTableWidget {{
    gridline-color: #e8edef;
}}
QCheckBox, QRadioButton {{
    spacing: 7px;
}}
QToolTip {{
    background: white;
    color: {UiPalette.TEXT};
    border: 1px solid {UiPalette.BORDER};
}}
"""
