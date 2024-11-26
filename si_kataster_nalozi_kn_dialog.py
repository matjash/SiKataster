from qgis.PyQt.QtWidgets import QWidget,QPushButton, QLabel,  QHBoxLayout, QVBoxLayout
from qgis.PyQt.QtCore import QCoreApplication
from qgis.utils import iface
from qgis.core import  QgsApplication
import os

from .functions_container import LoadQlrTask
        
MESSAGE_CATEGORY = 'SiKataster'

class NaloziKNDialog(QWidget):
    def __init__(self):
        super().__init__()
        self.iface = iface
        layout = QVBoxLayout()
        self.setWindowTitle(self.tr("Naloži KN"))

        
        load_kn_layout = QHBoxLayout()
        self.load_kn_button = QPushButton(self.tr('Naloži parcele in K. O. za območje Slovenije'))
        load_kn_layout.addWidget(self.load_kn_button)
        layout.addLayout(load_kn_layout)

        self.loading_label = QLabel(self.tr('Nalaganje...'))
        self.loading_label.setVisible(False)
        layout.addWidget(self.loading_label)

        self.setLayout(layout)
        self.load_kn_button.clicked.connect(self.load_kn)


    def load_kn(self):
        self.loading_label.setVisible(True)
        self.load_kn_task = LoadQlrTask(description=self.tr('Naloži qlr'), qlr_file='KN parcele.qlr', loading_label=self.loading_label)
        QgsApplication.taskManager().addTask(self.load_kn_task)
  

    def tr(self, message):
        return QCoreApplication.translate('SiKataster', message)