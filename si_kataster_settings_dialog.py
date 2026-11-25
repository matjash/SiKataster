# -*- coding: utf-8 -*-
"""'Nastavitve' tab: fallback parcel layer and a check of the GURS WFS connection.
Every change is saved at once."""
import html

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import (
    QFormLayout, QGroupBox, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QToolButton, QVBoxLayout, QWidget,
)
from qgis.core import Qgis, QgsApplication, QgsMapLayerProxyModel, QgsProject, QgsTask
from qgis.gui import QgsFieldComboBox, QgsMapLayerComboBox

from . import kataster

try:
    POLYGON_FILTER = Qgis.LayerFilter.PolygonLayer
except AttributeError:
    POLYGON_FILTER = QgsMapLayerProxyModel.PolygonLayer


def _wrapping_label(text=""):
    label = QLabel(text)
    label.setWordWrap(True)
    label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
    label.setMinimumWidth(1)
    return label


class SettingsDialog(QWidget):

    def __init__(self):
        super().__init__()
        self._task = None
        self.setWindowTitle(self.tr("Nastavitve"))
        layout = QVBoxLayout(self)

        # --- fallback layer ------------------------------------------------------
        box = QGroupBox(self.tr("Rezervni sloj parcel"))
        bl = QVBoxLayout(box)
        hint = _wrapping_label(self.tr(
            "Za »Poišči«, ko GURS WFS ni dosegljiv ali ko ga izberete kot vir iskanja. »Naloži kot sloj« je "
            "vedno iz GURS WFS. Vtičnik si zapomni vir sloja, zato deluje v vsakem projektu."))
        hint.setStyleSheet("color: #666;")
        bl.addWidget(hint)

        self.layer_combo = QgsMapLayerComboBox()
        self.layer_combo.setFilters(POLYGON_FILTER)
        self.layer_combo.setAllowEmptyLayer(True)
        self.layer_combo.setLayer(None)
        # 'activated' = chosen by the user; the combo changing on its own (project opened,
        # layers added or removed) must never set or change the fallback layer
        self.layer_combo.activated.connect(lambda *_: self._layer_picked(self.layer_combo.currentLayer()))
        self.clear_button = QToolButton()
        self.clear_button.setText("✕")
        self.clear_button.setToolTip(self.tr("Pozabi rezervni sloj"))
        self.clear_button.clicked.connect(self._clear)
        row = QHBoxLayout()
        row.addWidget(self.layer_combo, 1)
        row.addWidget(self.clear_button)
        self.ko_field = QgsFieldComboBox()
        self.parc_field = QgsFieldComboBox()
        self.ko_field.fieldChanged.connect(self._fields_changed)
        self.parc_field.fieldChanged.connect(self._fields_changed)
        form = QFormLayout()
        form.addRow(self.tr("Sloj iz projekta:"), row)
        form.addRow(self.tr("Polje ID K. O.:"), self.ko_field)
        form.addRow(self.tr("Polje št. parcele:"), self.parc_field)
        bl.addLayout(form)
        self.saved_name = _wrapping_label()
        self.saved_name.setTextFormat(Qt.TextFormat.RichText)
        bl.addWidget(self.saved_name)
        self.error = _wrapping_label()
        self.error.setStyleSheet("color: #c62828;")
        self.error.hide()
        bl.addWidget(self.error)
        layout.addWidget(box)

        # --- GURS WFS check ------------------------------------------------------
        box2 = QGroupBox(self.tr("GURS WFS"))
        b2 = QVBoxLayout(box2)
        address = _wrapping_label(kataster.WFS_URL)
        address.setStyleSheet("color: #666;")
        b2.addWidget(address)
        self.check_button = QPushButton(self.tr("Preveri povezavo"))
        self.check_button.clicked.connect(self._check)
        b2.addWidget(self.check_button)
        self.check_result = _wrapping_label()
        self.check_result.setTextFormat(Qt.TextFormat.RichText)
        b2.addWidget(self.check_result)
        layout.addWidget(box2)

        layout.addStretch()
        self._show_saved()

    # ---------------------------------------------------------- fallback layer

    def _project_layer(self, source):
        """The project layer with this source (the one to show in the combo), or None."""
        if not source:
            return None
        for layer in QgsProject.instance().mapLayers().values():
            if layer.source() == source:
                return layer
        return None

    def _show_saved(self):
        name, source, _provider, ko_f, parc_f = kataster.fallback_settings()
        layer = kataster.open_fallback_layer()
        in_project = self._project_layer(source)

        self.layer_combo.blockSignals(True)
        self.layer_combo.setLayer(in_project)
        self.layer_combo.blockSignals(False)

        # the line under the combo is only needed when the combo cannot show the layer
        if name and layer is None:
            self.saved_name.setText(self.tr("Shranjen sloj: <b>{}</b> – <span style='color:#c62828'>"
                                            "vira ni mogoče odpreti</span>").format(html.escape(name)))
        elif name and in_project is None:
            self.saved_name.setText(self.tr("Shranjen sloj: <b>{}</b> (ni v tem projektu)").format(html.escape(name)))
        elif not name:
            self.saved_name.setText(self.tr("Rezervni sloj ni nastavljen."))
        else:
            self.saved_name.setText("")
        self.saved_name.setVisible(bool(self.saved_name.text()))
        self.saved_name.setToolTip(source)
        self.layer_combo.setToolTip(source)
        self.clear_button.setEnabled(bool(name))

        for combo, field in ((self.ko_field, ko_f), (self.parc_field, parc_f)):
            combo.blockSignals(True)
            combo.setLayer(layer)
            combo.setField(field if layer is not None else "")
            combo.setEnabled(layer is not None)
            combo.blockSignals(False)
        if layer is not None and not (ko_f and parc_f):
            self.error.setText(self.tr("Izberite polji za K. O. in številko parcele – brez njiju se sloj ne uporabi."))
            self.error.show()
        else:
            self.error.hide()

    def showEvent(self, event):
        super().showEvent(event)
        self._show_saved()                   # the project may have changed meanwhile

    def _layer_picked(self, layer):
        """The chosen layer stays shown in the combo. An empty choice does not forget the
        saved layer (the combo also empties when the layer leaves the project) - use ✕."""
        if layer is not None:
            try:
                kataster.save_fallback_layer(layer)
            except ValueError as e:
                self._show_saved()
                self.error.setText(str(e))
                self.error.show()
                return
        self._show_saved()

    def _fields_changed(self, *_):
        if self.ko_field.layer() is not None:
            kataster.save_fallback_fields(self.ko_field.currentField(), self.parc_field.currentField())
            if self.ko_field.currentField() and self.parc_field.currentField():
                self.error.hide()

    def _clear(self):
        kataster.clear_fallback_layer()
        self._show_saved()

    # -------------------------------------------------------------- WFS check

    def _check(self):
        def finished(exception, result=None):
            self._task = None
            self.check_button.setEnabled(True)
            ok, reason = (False, str(exception)) if exception is not None else result
            if ok:
                self.check_result.setText(self.tr("<span style='color:#2e7d32'>GURS WFS je dosegljiv.</span>"))
            else:
                self.check_result.setText(
                    self.tr("<span style='color:#c62828'>GURS WFS ni dosegljiv:</span> {}").format(
                        html.escape(" ".join(reason.split()))))

        self.check_button.setEnabled(False)
        self.check_result.setText(self.tr("Preverjam…"))
        self._task = QgsTask.fromFunction(self.tr("SiKataster: preverjanje GURS WFS"), kataster.wfs_check,
                                          on_finished=finished)
        QgsApplication.taskManager().addTask(self._task)
