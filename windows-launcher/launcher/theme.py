"""Palette e foglio di stile, ripresi dalla dashboard esistente.

Stessi colori del progetto Flask di riferimento, cosi' i due strumenti si
somigliano: fondo #0a0e14, pannelli #161d27, bordi #1f2733, accento ambra
#e8a838.
"""

SFONDO = "#0a0e14"
PANNELLO = "#161d27"
BORDO = "#1f2733"
BORDO_CHIARO = "#2a3441"
TESTO = "#e6edf3"
TESTO_TENUE = "#8b97a8"
ACCENTO = "#e8a838"
VERDE = "#3fb950"
ROSSO = "#f85149"
BLU = "#58a6ff"

FOGLIO_DI_STILE = f"""
QWidget {{
    background: {SFONDO};
    color: {TESTO};
    font-family: 'Segoe UI', sans-serif;
    font-size: 13px;
}}

QGroupBox {{
    background: {PANNELLO};
    border: 1px solid {BORDO};
    border-radius: 8px;
    margin-top: 14px;
    padding: 12px 10px 10px 10px;
    font-weight: 600;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 12px;
    padding: 0 6px;
    color: {ACCENTO};
}}

QLineEdit, QComboBox, QSpinBox, QPlainTextEdit, QTextEdit {{
    background: {SFONDO};
    border: 1px solid {BORDO};
    border-radius: 6px;
    padding: 6px 8px;
    selection-background-color: {ACCENTO};
    selection-color: {SFONDO};
}}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QPlainTextEdit:focus {{
    border: 1px solid {ACCENTO};
}}
QLineEdit:disabled, QComboBox:disabled {{
    color: {TESTO_TENUE};
    background: #0d1219;
}}

QComboBox::drop-down {{ border: none; width: 18px; }}
QComboBox QAbstractItemView {{
    background: {PANNELLO};
    border: 1px solid {BORDO};
    selection-background-color: {BORDO_CHIARO};
}}

QPushButton {{
    background: {PANNELLO};
    border: 1px solid {BORDO_CHIARO};
    border-radius: 6px;
    padding: 7px 14px;
}}
QPushButton:hover {{ background: #1b2430; border-color: {ACCENTO}; }}
QPushButton:pressed {{ background: #11161d; }}
QPushButton:disabled {{ color: {TESTO_TENUE}; border-color: {BORDO}; }}

QPushButton#esegui {{
    background: {ACCENTO};
    color: {SFONDO};
    border: none;
    font-weight: 700;
    padding: 9px 18px;
}}
QPushButton#esegui:hover {{ background: #f0b855; }}
QPushButton#esegui:disabled {{ background: #4a4331; color: {TESTO_TENUE}; }}

QPushButton#interrompi {{
    background: transparent;
    border: 1px solid {ROSSO};
    color: {ROSSO};
    font-weight: 600;
}}
QPushButton#interrompi:hover {{ background: rgba(248, 81, 73, 0.12); }}

QTableWidget {{
    background: {SFONDO};
    border: 1px solid {BORDO};
    border-radius: 6px;
    gridline-color: {BORDO};
    selection-background-color: {BORDO_CHIARO};
}}
QHeaderView::section {{
    background: {PANNELLO};
    color: {TESTO_TENUE};
    border: none;
    border-bottom: 1px solid {BORDO};
    padding: 6px;
    font-weight: 600;
}}

QTextEdit#console {{
    background: #070a0f;
    border: 1px solid {BORDO};
    font-family: 'Cascadia Mono', 'Consolas', monospace;
    font-size: 12px;
}}

QPlainTextEdit#anteprima {{
    background: #070a0f;
    color: {ACCENTO};
    font-family: 'Cascadia Mono', 'Consolas', monospace;
    font-size: 12px;
}}

QLabel#stato {{ font-weight: 700; }}
QLabel.tenue {{ color: {TESTO_TENUE}; }}

QCheckBox::indicator {{
    width: 15px; height: 15px;
    border: 1px solid {BORDO_CHIARO};
    border-radius: 3px;
    background: {SFONDO};
}}
QCheckBox::indicator:checked {{
    background: {ACCENTO};
    border-color: {ACCENTO};
}}

QSplitter::handle {{ background: {BORDO}; }}
QScrollBar:vertical {{ background: transparent; width: 10px; }}
QScrollBar::handle:vertical {{ background: {BORDO}; border-radius: 5px; }}
QScrollBar::handle:vertical:hover {{ background: {BORDO_CHIARO}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
"""

# Codici SGR usati da Ansible, tradotti in colori della console.
COLORI_ANSI = {
    "30": "#484f58", "31": ROSSO,   "32": VERDE,   "33": ACCENTO,
    "34": BLU,       "35": "#bc8cff", "36": "#39c5cf", "37": TESTO,
    "90": TESTO_TENUE, "91": "#ff7b72", "92": "#56d364", "93": "#e3b341",
    "94": "#79c0ff", "95": "#d2a8ff", "96": "#56d4dd", "97": "#ffffff",
}
