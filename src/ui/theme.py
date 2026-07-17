"""Zentrales Dark-Theme fuer Qalam.

Die Farben sind identisch zum Aufnahme-Overlay (status_window.py), damit
Settings-, Haupt- und Overlay-Fenster wie EIN Tool aussehen.
"""
from PyQt5.QtGui import QColor

# --- Kernpalette (gespiegelt aus status_window.py) ---
BG        = "#18181B"   # Fenster-Hintergrund (fast schwarz)
SURFACE   = "#27272A"   # Eingabefelder / Flaechen
SURFACE_2 = "#323238"   # Hover / Drop-down
BORDER    = "#3F3F46"
TEXT      = "#E6E6E6"
SUBTLE    = "#9AA0A6"
GREEN     = "#3ECF5A"
ORANGE    = "#F5A623"
RED       = "#E5484D"

# Hintergrund fuer die abgerundete paintEvent-Flaeche der Fenster
BG_PAINT = QColor(24, 24, 27, 240)


def dark_qss() -> str:
    """Vollstaendiges Qt-Stylesheet fuer das Dark-Theme.

    Der aeussere Container bleibt transparent, damit die abgerundeten
    Fensterecken (paintEvent der BaseWindow) sichtbar bleiben; nur die
    eigentlichen Bedien-Widgets bekommen eine Flaechenfarbe.
    """
    return """
    QWidget {
        color: #E6E6E6;
        background: transparent;
        font-family: 'Segoe UI';
    }

    /* Tabs */
    QTabWidget::pane {
        border: 1px solid #3F3F46;
        border-radius: 8px;
        top: -1px;
    }
    QTabBar::tab {
        background: #27272A;
        color: #9AA0A6;
        padding: 7px 16px;
        margin-right: 2px;
        border: 1px solid #3F3F46;
        border-top-left-radius: 8px;
        border-top-right-radius: 8px;
    }
    QTabBar::tab:selected {
        background: #18181B;
        color: #E6E6E6;
        border-bottom: 2px solid #3ECF5A;
    }
    QTabBar::tab:hover {
        color: #E6E6E6;
    }

    /* Eingaben */
    QLineEdit, QTextEdit, QSpinBox, QComboBox {
        background: #27272A;
        color: #E6E6E6;
        border: 1px solid #3F3F46;
        border-radius: 6px;
        padding: 4px 6px;
        selection-background-color: #3ECF5A;
        selection-color: #18181B;
    }
    QLineEdit:focus, QTextEdit:focus, QSpinBox:focus, QComboBox:focus {
        border: 1px solid #3ECF5A;
    }
    QLineEdit:disabled, QComboBox:disabled {
        color: #9AA0A6;
        background: #202023;
    }
    QComboBox::drop-down {
        border: none;
        width: 20px;
        background: #323238;
        border-top-right-radius: 6px;
        border-bottom-right-radius: 6px;
    }
    QComboBox QAbstractItemView {
        background: #27272A;
        color: #E6E6E6;
        border: 1px solid #3F3F46;
        selection-background-color: #3ECF5A;
        selection-color: #18181B;
        outline: none;
    }

    /* Checkboxen */
    QCheckBox { spacing: 8px; background: transparent; }
    QCheckBox::indicator {
        width: 16px; height: 16px;
        border: 1px solid #3F3F46;
        border-radius: 4px;
        background: #27272A;
    }
    QCheckBox::indicator:checked {
        background: #3ECF5A;
        border: 1px solid #3ECF5A;
    }

    /* Buttons */
    QPushButton {
        background: #27272A;
        color: #E6E6E6;
        border: 1px solid #3F3F46;
        border-radius: 8px;
        padding: 8px 14px;
    }
    QPushButton:hover  { background: #323238; }
    QPushButton:pressed{ background: #202023; }
    QPushButton#saveButton {
        background: #3ECF5A;
        color: #14261A;
        border: none;
        font-weight: bold;
    }
    QPushButton#saveButton:hover   { background: #4FD96B; }
    QPushButton#saveButton:pressed { background: #34B34D; }

    QToolButton { background: transparent; border: none; }
    QToolButton:hover { color: #3ECF5A; }

    /* Scrollbereich + Scrollbalken */
    QScrollArea { border: none; background: transparent; }
    QScrollBar:vertical {
        background: transparent; width: 10px; margin: 2px;
    }
    QScrollBar::handle:vertical {
        background: #3F3F46; border-radius: 5px; min-height: 24px;
    }
    QScrollBar::handle:vertical:hover { background: #52525B; }
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
    QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }

    /* Tooltips + Dialoge solide, damit nichts durchscheint */
    QToolTip {
        background: #27272A; color: #E6E6E6;
        border: 1px solid #3F3F46; padding: 4px;
    }
    QMessageBox, QDialog { background: #18181B; }
    """


def apply_dark_theme(widget):
    """Dark-Theme auf ein Fenster (und alle Kind-Widgets) anwenden."""
    widget.setStyleSheet(dark_qss())
