# -*- coding: utf-8 -*-
"""'Išči po parceli' tab: one parcel by K.O. and number, or all parcels under a layer.

Source is GURS WFS; when it does not answer, the fallback layer from the
'Nastavitve' tab is used (if set).
"""
import html

from qgis.PyQt.QtCore import QStringListModel, Qt
from qgis.PyQt.QtWidgets import (
    QCheckBox, QCompleter, QDoubleSpinBox, QHBoxLayout, QLabel, QLineEdit, QMenu, QPushButton, QSizePolicy, QSlider,
    QStackedWidget, QToolButton, QVBoxLayout, QWidget,
)
from qgis.core import Qgis, QgsApplication, QgsMapLayerProxyModel, QgsProject, QgsSettings, QgsTask
from qgis.gui import QgsMapLayerComboBox
from qgis.utils import iface

from . import kataster
from .kataster import log

KEY_SOURCE = "SiKataster/search_source"
SOURCE_WFS, SOURCE_FALLBACK = "wfs", "fallback"

try:
    VECTOR_FILTER = Qgis.LayerFilter.VectorLayer
except AttributeError:
    VECTOR_FILTER = QgsMapLayerProxyModel.VectorLayer


def short(text, limit=300):
    """Error text for the status line: one paragraph, not too long."""
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[:limit] + " …"


def status_label():
    """Status line that wraps, never widens the panel, and can be selected and copied."""
    label = QLabel()
    label.setWordWrap(True)
    label.setTextFormat(Qt.TextFormat.RichText)
    label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
    label.setMinimumWidth(1)
    return label


def _completer(parent, mode):
    c = QCompleter(parent)
    c.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
    c.setFilterMode(mode)
    c.setModel(QStringListModel(c))
    return c


SLIDER_STYLE = """
QSlider::groove:horizontal { border: 1px solid #bbb; background: #eee; height: 16px; border-radius: 8px; }
QSlider::handle:horizontal { background: #5c5c5c; border: 1px solid #777; width: 26px; height: 26px;
                             margin: -5px 0; border-radius: 13px; }
QSlider::sub-page:horizontal { background: #ccc; border-radius: 8px; }
QSlider::add-page:horizontal { background: #a6ce39; border-radius: 8px; }
"""


class ParcelDialog(QWidget):

    def __init__(self):
        super().__init__()
        self.iface = iface
        self._tasks = set()
        self._loud = set()                  # tasks the user is waiting for
        self._parcels_for = None            # K.O. id the parcel suggestions belong to
        self._area_source = None            # layer whose selection the checkbox follows
        self.ko = kataster.load_ko()
        self.setWindowTitle(self.tr("Išči"))

        layout = QVBoxLayout(self)

        # --- source of the search: a button with a menu, top right ----------------
        self._source = QgsSettings().value(KEY_SOURCE, SOURCE_WFS)      # remembered between sessions
        self.source_button = QToolButton()
        self.source_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.source_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self.source_button.setToolTip(self.tr("Vir za »Poišči« in predloge številk parcel. "
                                              "»Naloži kot sloj« je vedno iz GURS WFS."))
        self.source_menu = QMenu(self.source_button)
        self.source_menu.aboutToShow.connect(self._fill_source_menu)
        self.source_button.setMenu(self.source_menu)
        top = QHBoxLayout()
        top.addStretch()
        top.addWidget(self.source_button)
        layout.addLayout(top)

        # --- switch between the two search forms ------------------------------
        mode_label = QLabel(self.tr("Iskanje po parcelah  /  Izbor s presekom sloja"))
        mode_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(mode_label)
        self.search_mode_slider = QSlider(Qt.Orientation.Horizontal)
        self.search_mode_slider.setRange(0, 1)
        self.search_mode_slider.setFixedSize(70, 20)
        self.search_mode_slider.setStyleSheet(SLIDER_STYLE)
        self.search_mode_slider.setSingleStep(1)
        layout.addWidget(self.search_mode_slider, alignment=Qt.AlignmentFlag.AlignCenter)
        self.stacked_widget = QStackedWidget()
        layout.addWidget(self.stacked_widget)
        self.search_mode_slider.valueChanged.connect(self.stacked_widget.setCurrentIndex)

        # --- one parcel -------------------------------------------------------
        box = QWidget()
        box_layout = QVBoxLayout(box)
        box_layout.addWidget(QLabel(self.tr("Naziv ali KO ID:")))
        self.ko_id_input = QLineEdit()
        self.ko_completer = _completer(self.ko_id_input, Qt.MatchFlag.MatchContains)
        self.ko_id_input.setCompleter(self.ko_completer)
        self._fill_ko_completer()
        self.ko_id_input.editingFinished.connect(self._ko_changed)
        self.ko_completer.activated.connect(lambda _t: self._ko_changed())
        box_layout.addWidget(self.ko_id_input)
        box_layout.addWidget(QLabel(self.tr("Številka parcele:")))
        self.parcela_input = QLineEdit()
        self.parcela_completer = _completer(self.parcela_input, Qt.MatchFlag.MatchStartsWith)
        self.parcela_input.setCompleter(self.parcela_completer)
        self.parcela_input.returnPressed.connect(lambda: self.find(load=False))
        box_layout.addWidget(self.parcela_input)
        row = QHBoxLayout()
        self.load_button = QPushButton(self.tr("Naloži kot sloj"))
        self.load_button.setToolTip(self.tr("Vedno iz GURS WFS."))
        self.load_button.clicked.connect(lambda: self.find(load=True))
        self.find_button = QPushButton(self.tr("Poišči"))
        self.find_button.clicked.connect(lambda: self.find(load=False))
        row.addWidget(self.load_button)
        row.addWidget(self.find_button)
        box_layout.addLayout(row)
        box_layout.addStretch()
        self.stacked_widget.addWidget(box)

        # --- parcels under a layer ----------------------------------------------
        box2 = QWidget()
        box2_layout = QVBoxLayout(box2)
        box2_layout.addWidget(QLabel(self.tr("Izberi sloj za presek:")))
        self.layer_combobox = QgsMapLayerComboBox()
        self.layer_combobox.setFilters(VECTOR_FILTER)
        self.layer_combobox.layerChanged.connect(self._area_layer_changed)
        box2_layout.addWidget(self.layer_combobox)
        self.selected_features_checkbox = QCheckBox(self.tr("Le izbrane"))
        box2_layout.addWidget(self.selected_features_checkbox)
        buffer_row = QHBoxLayout()
        self.buffer_spinbox = QDoubleSpinBox()
        self.buffer_spinbox.setRange(-1000000, 1000000)
        self.buffer_spinbox.setSuffix(" m")
        buffer_row.addWidget(QLabel(self.tr("Buffer (m):")))
        buffer_row.addWidget(self.buffer_spinbox)
        box2_layout.addLayout(buffer_row)
        self.search_area_button = QPushButton(self.tr("Naloži izbrane parcele kot začasni sloj"))
        self.search_area_button.setToolTip(self.tr("Vedno iz GURS WFS."))
        self.search_area_button.clicked.connect(self.parcels_under_layer)
        box2_layout.addWidget(self.search_area_button)
        box2_layout.addStretch()
        self.stacked_widget.addWidget(box2)

        self.status = status_label()
        layout.addWidget(self.status)

        self._area_layer_changed(self.layer_combobox.currentLayer())
        self._refresh_sources()
        if kataster.ko_needs_refresh():
            self._run(self.tr("SiKataster: seznam K.O."), kataster.refresh_ko, done=self._ko_refreshed, quiet=True)

    # ---------------------------------------------------------------- helpers

    def _run(self, description, fn, *args, done=None, quiet=False):
        """Run fn in the background; done(result) on success, errors shown in the status line."""
        def finished(exception, result=None):
            self._tasks.discard(task)
            if not quiet:
                self._loud.discard(task)
                self._busy(bool(self._loud))
            if exception is not None:
                log(f"{description}: {exception}", Qgis.MessageLevel.Warning)
                if not quiet:
                    self._status(html.escape(short(exception)), error=True)
                return
            if done:
                done(result)

        task = QgsTask.fromFunction(description, fn, *args, on_finished=finished)
        self._tasks.add(task)               # keep a reference until it finishes
        if not quiet:
            self._loud.add(task)
            self._busy(True)
        QgsApplication.taskManager().addTask(task)
        return task

    def _busy(self, busy):
        for b in (self.find_button, self.load_button, self.search_area_button):
            b.setEnabled(not busy)
        if busy:
            self._status(self.tr("Iščem…"))

    def _status(self, text, error=False):
        self.status.setText(f'<span style="color:{"#c62828" if error else "#333"}">{text}</span>')

    def _fill_ko_completer(self):
        self.ko_completer.model().setStringList([kataster.ko_label(k, self.ko) for k in sorted(self.ko)])

    def _ko_refreshed(self, n):
        self.ko = kataster.load_ko()
        self._fill_ko_completer()
        log(f"Seznam K.O. osvežen ({n}).")

    def _area_layer_changed(self, layer):
        """'Le izbrane' follows the selection of the chosen layer."""
        if self._area_source is not None:
            try:
                self._area_source.selectionChanged.disconnect(self._selection_changed)
            except (TypeError, RuntimeError):
                pass
        self._area_source = layer
        if layer is not None:
            layer.selectionChanged.connect(self._selection_changed)
        self._selection_changed()

    def _selection_changed(self, *_):
        layer = self.layer_combobox.currentLayer()
        has_selection = layer is not None and layer.selectedFeatureCount() > 0
        self.selected_features_checkbox.setEnabled(has_selection)
        self.selected_features_checkbox.setChecked(has_selection)

    def _no_source(self, loading=False):
        """Status text when GURS WFS did not answer and no other source can be used."""
        reason = kataster.wfs_error()
        text = self.tr("GURS WFS ni dosegljiv.")
        if reason:
            text += " " + html.escape(short(reason))
        if loading:
            text += " " + self.tr("Nalaganje sloja je mogoče samo iz GURS WFS.")
        else:
            text += " " + self.tr("V zavihku Nastavitve lahko nastavite rezervni sloj parcel.")
        self._status(text, error=True)

    # ---------------------------------------------------------------- source

    def _refresh_sources(self):
        """Button text = the source in use. The fallback layer can be the source only while it is set."""
        own = kataster.fallback_layer()
        if own is None or self._source not in (SOURCE_WFS, SOURCE_FALLBACK):
            self._source = SOURCE_WFS       # only for now - the saved choice is kept
        self.source_button.setText(own[0].name() if self._source == SOURCE_FALLBACK else self.tr("GURS WFS"))

    def _fill_source_menu(self):
        own = kataster.fallback_layer()
        self.source_menu.clear()
        wfs = self.source_menu.addAction(self.tr("GURS WFS"))
        wfs.setCheckable(True)
        wfs.setChecked(self._source == SOURCE_WFS)
        wfs.triggered.connect(lambda *_: self._set_source(SOURCE_WFS))
        layer = self.source_menu.addAction(own[0].name() if own else self.tr("Rezervni sloj ni nastavljen"))
        layer.setCheckable(True)
        layer.setChecked(self._source == SOURCE_FALLBACK)
        layer.setEnabled(own is not None)
        layer.triggered.connect(lambda *_: self._set_source(SOURCE_FALLBACK))

    def _set_source(self, source):
        if source == self._source:
            return
        self._source = source
        QgsSettings().setValue(KEY_SOURCE, source)
        self._refresh_sources()
        self._parcels_for = None            # parcel suggestions belong to the other source
        if self.ko_id_input.text().strip():
            self._ko_changed()

    def _use_fallback(self):
        """The fallback layer (layer, ko_field, parcel_field) if it is the chosen source, else None."""
        if self._source != SOURCE_FALLBACK:
            return None
        return kataster.fallback_layer()

    def showEvent(self, event):
        super().showEvent(event)
        self._refresh_sources()             # the fallback layer may have been set meanwhile

    # ------------------------------------------------------------------- K.O.

    def current_ko(self, show_choices=True):
        ko_id, matches = kataster.resolve_ko(self.ko_id_input.text(), self.ko)
        if ko_id is None and show_choices:
            if matches:
                self._status(self.tr("Več K. O. s tem imenom – izberite s seznama."))
                self.ko_completer.setCompletionPrefix(self.ko_id_input.text().strip())
                self.ko_completer.complete()
            elif self.ko_id_input.text().strip():
                self._status(self.tr("K. O. »{}« ne obstaja.").format(html.escape(self.ko_id_input.text().strip())),
                             error=True)
        return ko_id

    def _ko_changed(self):
        ko_id = self.current_ko()
        if ko_id is None:
            return
        self.ko_id_input.setText(kataster.ko_label(ko_id, self.ko))
        if ko_id == self._parcels_for:
            return
        self._parcels_for = ko_id
        self.parcela_completer.model().setStringList([])
        if self._use_fallback():
            self._after_numbers(ko_id, None)
            return
        self._run(self.tr("SiKataster: seznam parcel"), kataster.parcel_numbers_remote, ko_id,
                  done=lambda numbers, k=ko_id: self._after_numbers(k, numbers), quiet=True)

    def _after_numbers(self, ko_id, numbers):
        if numbers is None:                       # fallback layer chosen, or GURS WFS did not answer
            own = kataster.fallback_layer()
            try:
                numbers = kataster.parcel_numbers_layer(own[0], own[1], own[2], ko_id) if own else []
            except ValueError as e:
                log(e, Qgis.MessageLevel.Warning)
                numbers = []
        if ko_id == self._parcels_for:
            self.parcela_completer.model().setStringList(numbers)

    # ----------------------------------------------------------------- search

    def find(self, load=False):
        ko_id = self.current_ko()
        parcela = kataster.normalize_parcel(self.parcela_input.text())
        if ko_id is None or not parcela:
            if ko_id is not None or not self.ko_id_input.text().strip():
                self._status(self.tr("Potrebno je vnesti K. O. in parcelo."), error=True)
            return
        name = self.tr("K. O. {}, parcela {}").format(ko_id, parcela)

        def from_layer(own):
            return kataster.find_layer(own[0], own[1], own[2], ko_id, parcela)

        own = None if load else self._use_fallback()      # loading is always from GURS WFS
        if own:
            try:
                self._show(from_layer(own), name, False)
            except ValueError as e:
                self._status(html.escape(short(e)), error=True)
            return
        self._run(self.tr("SiKataster: iskanje parcele"), kataster.find_remote, ko_id, parcela,
                  done=lambda r: self._after_remote(r, name, load, from_layer))

    def parcels_under_layer(self):
        layer = self.layer_combobox.currentLayer()
        if layer is None:
            self._status(self.tr("Izberite sloj."), error=True)
            return
        try:
            geom = kataster.area_geometry(layer, self.selected_features_checkbox.isChecked(),
                                          self.buffer_spinbox.value())
        except ValueError as e:
            self._status(html.escape(short(e)), error=True)
            return
        name = self.tr("Izbor parcel")
        self._run(self.tr("SiKataster: parcele pod slojem"), kataster.intersect_remote, geom,
                  done=lambda r: self._after_remote(r, name, True, None))

    def _after_remote(self, result, name, load, fallback_query):
        """result is None when GURS WFS did not answer. A search then uses the fallback layer,
        if there is one; a layer is loaded only from GURS WFS."""
        if result is not None:
            self._show(result, name, load)
            return
        if load:
            self._no_source(loading=True)
            return
        own = kataster.fallback_layer()
        if own is None:
            self._no_source()
            return
        try:
            self._show(fallback_query(own), name, load, fallback=True)
        except ValueError as e:
            self._status(html.escape(short(e)), error=True)

    def _show(self, result, name, load, fallback=False):
        source = html.escape(result.label)
        if fallback:
            source += self.tr(" (rezervni sloj – GURS WFS ni dosegljiv)")
        n = len(result.features)
        if n == 0:
            text = self.tr("Parcela ne obstaja.") if result.authoritative else self.tr("Ni najdeno.")
            self._status(f"{text} <i>{self.tr('Vir')}: {source}</i>", error=True)
            return
        kataster.zoom_and_flash(self.iface.mapCanvas(), result)
        if load:
            QgsProject.instance().addMapLayer(kataster.to_memory_layer(result, name))
        text = self.tr("Najdenih: {}.").format(n) if n > 1 else self.tr("Najdeno.")
        self._status(f"{text} <i>{self.tr('Vir')}: {source}</i>")
