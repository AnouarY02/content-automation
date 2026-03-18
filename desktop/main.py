"""
AY Marketing OS — Desktop Control Center

Startpunt: python desktop/main.py
Of vanuit de desktop/ map: python main.py
"""

import sys
import os

# Zorg dat de desktop/ map zelf in het pad staat zodat imports werken
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import Qt

from app import MainWindow, load_stylesheet


def main():
    # High-DPI schaling
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )

    app = QApplication(sys.argv)
    app.setApplicationName("AY Marketing OS")
    app.setApplicationDisplayName("AY Marketing OS")
    app.setOrganizationName("AY-Automatisering")

    load_stylesheet(app)

    window = MainWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
