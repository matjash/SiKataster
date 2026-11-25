# -*- coding: utf-8 -*-
"""Loading of the bundled QLR. Parcel search and web requests are in kataster.py."""
import os

from qgis.PyQt.QtCore import QCoreApplication
from qgis.core import Qgis, QgsLayerDefinition, QgsMessageLog, QgsProject, QgsTask

MESSAGE_CATEGORY = 'SiKataster'


def tr(message):
    return QCoreApplication.translate('SiKataster', message)


class LoadQlrTask(QgsTask):
    def __init__(self, description=None, qlr_file=None, loading_label=None):
        super().__init__(description, QgsTask.Flag.CanCancel)
        self.description = description
        self.qlr_file = qlr_file
        self.exception = None
        self.loading_label = loading_label
        self.plugin_dir = os.path.dirname(__file__)
        self.qlr_path = os.path.join(
            self.plugin_dir,
            'qlr',qlr_file
            )
        
    def run(self):
        try:
            if os.path.exists(self.qlr_path):
                QgsLayerDefinition().loadLayerDefinition(self.qlr_path, QgsProject.instance(), QgsProject.instance().layerTreeRoot())
                return True
            else:
                self.exception = tr('Težave pri nalaganju QLR datoteke')
                return False
        except Exception as e:
            self.exception = e
            return False
    def finished(self, result):
        if result:
            QgsMessageLog.logMessage(tr(f"Uspešno nalaganje QLR datoteke"), MESSAGE_CATEGORY, Qgis.MessageLevel.Info)
            self.loading_label.setVisible(False)
        else:
            self.loading_label.setStyleSheet("color: red;")
            self.loading_label.setText(tr(f"Error: {self.exception if self.exception else tr('Neznana napaka')}"))
            self.loading_label.setVisible(True)
            QgsMessageLog.logMessage(f"Error: {self.exception if self.exception else tr('Neznana napaka')}", MESSAGE_CATEGORY, Qgis.MessageLevel.Warning)
