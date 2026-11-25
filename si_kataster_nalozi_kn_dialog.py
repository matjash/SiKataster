# -*- coding: utf-8 -*-
"""'Naloži KN' tab: live GURS WFS layers (K.O. and parcels) for the whole country."""
import html

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import QApplication, QLabel, QPushButton, QSizePolicy, QVBoxLayout, QWidget

from . import kataster


class NaloziKNDialog(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(self.tr("Naloži KN"))
        layout = QVBoxLayout(self)

        self.load_kn_button = QPushButton(self.tr("Naloži parcele in K. O. za območje Slovenije"))
        self.load_kn_button.setToolTip(self.tr("Sloja GURS WFS v skupini »{}«.").format(kataster.KN_GROUP))
        self.load_kn_button.clicked.connect(self.load_kn)
        layout.addWidget(self.load_kn_button)

        self.loading_label = QLabel()
        self.loading_label.setWordWrap(True)
        self.loading_label.setTextFormat(Qt.TextFormat.RichText)
        self.loading_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.loading_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.loading_label.setMinimumWidth(1)
        layout.addWidget(self.loading_label)
        layout.addStretch()

    def _status(self, text, error=False):
        self.loading_label.setText(f'<span style="color:{"#c62828" if error else "#333"}">{text}</span>')

    def load_kn(self):
        self.load_kn_button.setEnabled(False)
        self._status(self.tr("Nalaganje…"))
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        QApplication.processEvents()        # show the text before the layers are opened
        try:
            added, present = kataster.load_kn_layers()
        except IOError as e:
            kataster.log(e, kataster.Qgis.MessageLevel.Warning)
            self._status(html.escape(" ".join(str(e).split())), error=True)
            return
        finally:
            QApplication.restoreOverrideCursor()
            self.load_kn_button.setEnabled(True)
        if added:
            self._status(self.tr("Dodano v skupino »{}«: {}.").format(kataster.KN_GROUP, ", ".join(added)))
        else:
            self._status(self.tr("Sloja sta že v projektu ({}).").format(", ".join(present)))
