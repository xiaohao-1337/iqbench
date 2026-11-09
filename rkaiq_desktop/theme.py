"""Shared visual tokens for the Windows workbench."""

NAVY = "#16273c"
INK = "#172b42"
MUTED = "#4f647d"
ACCENT = "#185ad0"
BORDER = "#dce5f0"

STYLE = """
QWidget { color: #172b42; font-size: 13px; }
QMainWindow, QWidget#surface, QScrollArea { background: #f2f5fa; }
QFrame#card, QFrame#footer, QFrame#topbar { background: white; border: 1px solid #dce5f0; }
QFrame#card { border-radius: 12px; }
QFrame#readonly { background: #e6edf7; border: 1px solid #d8e2f0; border-radius: 11px; }
QFrame#rail { background: #16273c; border-radius: 12px; }
QFrame#hero { background: #16273c; border-radius: 12px; }
QFrame#step { background: transparent; border-radius: 8px; }
QFrame#step[state="active"] { background: #e8f0ff; }
QLabel#badge[state="done"] { background: #e3f3ec; color: #166144; }
QLabel#badge[state="active"] { background: #d7e7ff; color: #174d9e; }
QLabel#eyebrow { color: #3e5a79; font-weight: 700; }
QLabel#section { font-size: 17px; font-weight: 700; }
QLabel#muted { color: #4f647d; }
QLabel#inverse { color: #f2f6fd; font-size: 19px; font-weight: 700; }
QLabel#inverseNote { color: #b3c5dc; }
QLabel#value { background: #eaf2ff; color: #134da9; border-radius: 6px; padding: 4px; font-weight: 700; }
QLabel#badge { background: #e8eff9; color: #3d5d83; border-radius: 6px; padding: 4px 8px; }
QLabel#status[tone="error"] { color: #a83036; }
QLabel#status[tone="ok"] { color: #15734d; }
QLabel#status[tone="busy"] { color: #185ad0; }
QPushButton, QComboBox, QLineEdit {
    background: white; border: 1px solid #d0dced; border-radius: 7px; padding: 7px 10px; min-height: 20px;
}
QPushButton { font-weight: 600; }
QPushButton:hover, QComboBox:hover { background: #edf4ff; border-color: #9bb8df; }
QLineEdit#password { lineedit-password-character: 42; lineedit-password-mask-delay: 0; }
QPushButton:pressed { background: #dce9ff; }
QPushButton:focus, QLineEdit:focus, QComboBox:focus { border: 2px solid #2865d5; padding: 6px 9px; }
QPushButton#primary { background: #185ad0; color: white; border-color: #185ad0; }
QPushButton#primary:hover { background: #104db8; }
QPushButton#primary:focus { border-color: #0b327b; }
QPushButton#primary:pressed { background: #103b8f; }
QPushButton#primary:disabled { background: #eef2f7; color: #657992; border-color: #dce5f0; }
QPushButton#nav { text-align: left; color: #cbd9ec; background: transparent; border-color: transparent; }
QPushButton#nav:hover { background: #233954; color: white; }
QPushButton#nav:checked { background: #28466b; color: white; }
QPushButton#nav:focus { border-color: #79adfd; }
QPushButton#toggle { border-radius: 14px; min-height: 18px; padding: 4px 12px; background: #eff3f8; }
QPushButton#toggle:checked { background: #e8f3ff; border-color: #8fb7f4; color: #124ba5; }
QPushButton#toggle:focus { padding: 3px 11px; }
QPushButton:disabled, QComboBox:disabled, QLineEdit:disabled { background: #eef2f7; color: #657992; border-color: #dce5f0; }
QComboBox QAbstractItemView { background: white; color: #172b42; selection-background-color: #e3efff; selection-color: #172b42; }
QComboBox::drop-down { border: none; width: 22px; }
QComboBox#picker { border-color: #185ad0; }
QScrollArea { border: none; }
QScrollBar:vertical { width: 8px; background: #e9eff7; border-radius: 4px; }
QScrollBar::handle:vertical { background: #b7c8df; min-height: 30px; border-radius: 4px; }
QScrollBar::handle:vertical:hover { background: #8aa6c9; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
QSlider::groove:horizontal { height: 5px; border-radius: 2px; background: #dfe8f4; }
QSlider::sub-page:horizontal { height: 5px; border-radius: 2px; background: #4881dd; }
QSlider::handle:horizontal { background: white; border: 2px solid #185ad0; width: 14px; margin: -6px 0; border-radius: 8px; }
QSlider::handle:horizontal:hover, QSlider::handle:horizontal:focus { background: #dceaff; border-color: #0b429f; }
QSlider::handle:horizontal:disabled { border-color: #aebed2; background: #f6f8fa; }
QSlider::sub-page:horizontal:disabled { background: #b9c9db; }
QTableWidget { background: white; alternate-background-color: #f3f7fc; border: 1px solid #dce5f0; gridline-color: #dce5f0; }
QHeaderView::section { background: #e7effa; color: #233e5f; border: 0; padding: 8px; font-weight: 600; }
QToolTip { background: #16273c; color: white; border: none; padding: 4px; }
"""


def update_style(widget, name, value):
    if widget.property(name) != value:
        widget.setProperty(name, value)
        widget.style().unpolish(widget)
        widget.style().polish(widget)
        widget.update()
