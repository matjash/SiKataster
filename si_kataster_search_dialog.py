from qgis.PyQt.QtWidgets import QWidget,  QLineEdit, QPushButton, QLabel, QCompleter, QHBoxLayout, QVBoxLayout
from qgis.PyQt.QtCore import QStringListModel, Qt
from qgis.utils import iface
from qgis.core import QgsProject

from .functions_container import (LoadKoWorker, 
                                  LoadParcelWorker, 
                                  is_wfs_accessible, 
                                  load_filtered_parcel_layer, 
                                  zoom_to_and_flash_geometry_from_layer,
                                  layer_to_single_geometry,
                                  copy_wfs_to_scratch_layer)
        
class ParcelDialog(QWidget):
    def __init__(self):
        super().__init__()
        self.iface = iface
        layout = QVBoxLayout()
        self.setWindowTitle(self.tr("Išči parcelo"))

        self.ko_id_label = QLabel(self.tr('Naziv ali KO ID:'))
        self.ko_id_input = QLineEdit()
        layout.addWidget(self.ko_id_label)
        layout.addWidget(self.ko_id_input)

        self.ko_completer = QCompleter([], self)
        self.ko_completer.setCaseSensitivity(Qt.CaseInsensitive)
        self.ko_completer.setFilterMode(Qt.MatchContains)
        self.ko_id_input.setCompleter(self.ko_completer)

        self.parcela_label = QLabel(self.tr('Številka parcele:'))
        self.parcela_input = QLineEdit()
        layout.addWidget(self.parcela_label)
        layout.addWidget(self.parcela_input)

        self.parcela_completer = QCompleter([], self)
        self.parcela_completer.setCaseSensitivity(Qt.CaseInsensitive)
        self.parcela_completer.setFilterMode(Qt.MatchStartsWith)
        self.parcela_input.setCompleter(self.parcela_completer)

        self.loading_label = QLabel(self.tr('Nalaganje...'))
        self.loading_label.setVisible(False)
        layout.addWidget(self.loading_label)

        # Create a horizontal layout for buttons
        button_layout = QHBoxLayout()

        # "Naloži" button (smaller)
        self.load_button = QPushButton(self.tr('Naloži kot sloj'))
        self.load_button.setFixedWidth(80)
        button_layout.addWidget(self.load_button)

        # "Poišči" button
        self.find_button = QPushButton(self.tr('Poišči'))
        button_layout.addWidget(self.find_button)

        # Add the horizontal button layout to the main layout
        layout.addLayout(button_layout)

        self.setLayout(layout)

        self.find_button.clicked.connect(self.find_parcel)
        self.load_button.clicked.connect(self.load_parcel)
        self.ko_id_input.editingFinished.connect(self.load_parcels_for_selected_ko)
        if not is_wfs_accessible():
            self.loading_label.setText(self.tr('Server ni dostopen.'))
            self.loading_label.setVisible(True)
            self.loading_label.setStyleSheet("color: red;")
        else:
            self.load_ko()

    def load_ko(self):
        self.loading_label.setVisible(True)
        self.worker = LoadKoWorker()
        self.worker.data_loaded.connect(self.update_ko_completer)
        self.worker.start()

    def update_ko_completer(self, ko_dict):
        self.loading_label.setVisible(False)
        combined_list = [f"{ko_id} - {ko_dict[ko_id]}" for ko_id in ko_dict]
        self.ko_completer.setModel(QStringListModel(combined_list))

    def load_parcels_for_selected_ko(self):
        ko_id_text = self.ko_id_input.text()
        if " - " in ko_id_text:
            ko_id = ko_id_text.split(" - ")[0]
            
            self.loading_label.setText(self.tr('Berem parcele...'))
            self.loading_label.setStyleSheet("color: black;")
            self.loading_label.setVisible(True)

            self.parcel_worker = LoadParcelWorker(ko_id)
            self.parcel_worker.data_loaded.connect(self.update_parcel_completer)
            self.parcel_worker.start()

    def update_parcel_completer(self, parcel_list):
        self.loading_label.setVisible(False)
        parcel_ids = [parcel for parcel in parcel_list]
        self.parcela_completer.setModel(QStringListModel(parcel_ids))


    def find_parcel(self):
        if not is_wfs_accessible():
            self.loading_label.setText(self.tr('Server ni dostopen.'))
            self.loading_label.setVisible(True)
            self.loading_label.setStyleSheet("color: red;")
        else:
            self.loading_label.setVisible(False)
            ko_id_or_naziv = self.ko_id_input.text()
            parcela = self.parcela_input.text()
            if ko_id_or_naziv and parcela:
                ko_id = ko_id_or_naziv.split(" - ")[0]
                layer = load_filtered_parcel_layer("SI.GURS.KN:OSNOVNI_PARCELE", f"KO_ID={ko_id} AND ST_PARCELE='{parcela}'")
                if not layer.isValid():
                    self.loading_label.setStyleSheet("color: red;")
                    self.loading_label.setText(self.tr('Ne najdem parcele.'))   
                    self.loading_label.setVisible(True)
                else:       
                    geometry = layer_to_single_geometry(layer)         
                    flashed = zoom_to_and_flash_geometry_from_layer(self.iface, geometry)
                    if not flashed:
                        self.loading_label.setStyleSheet("color: red;")
                        self.loading_label.setText(self.tr('Ne najdem parcele.'))   
                        self.loading_label.setVisible(True)
            
            else:
                self.loading_label.setStyleSheet("color: black;")
                self.loading_label.setText(self.tr('Potrebno je vnesti K. O. in parcelo')) 
                self.loading_label.setVisible(True)

    def load_parcel(self):
        if not is_wfs_accessible():
            self.loading_label.setText(self.tr('Server ni dostopen.'))
            self.loading_label.setVisible(True)
            self.loading_label.setStyleSheet("color: red;")
        else:
            self.loading_label.setVisible(False)
            ko_id_or_naziv = self.ko_id_input.text()
            parcela = self.parcela_input.text()
            if ko_id_or_naziv and parcela:
                ko_id = ko_id_or_naziv.split(" - ")[0]
                layer = load_filtered_parcel_layer("SI.GURS.KN:PARCELE", f"KO_ID={ko_id} AND ST_PARCELE='{parcela}'")
                if not layer.isValid():
                    self.loading_label.setStyleSheet("color: red;")
                    self.loading_label.setText(self.tr('Ne najdem parcele.'))   
                    self.loading_label.setVisible(True)
                else:     
                    local_layer = copy_wfs_to_scratch_layer(layer)
                    local_layer.setName(f"K. O. {ko_id}, parcela {parcela}") 
                    QgsProject.instance().addMapLayer(local_layer)     
                    geometry = layer_to_single_geometry(local_layer)             
                    zoom_to_and_flash_geometry_from_layer(self.iface, geometry)
            
            else:
                self.loading_label.setStyleSheet("color: black;")
                self.loading_label.setText(self.tr('Potrebno je vnesti K. O. in parcelo'))  
                self.loading_label.setVisible(True)

# To call the dialog in QGIS
def open_parcel_dialog(iface):
    dialog = ParcelDialog(iface)
    if dialog.exec_():
        print("Search confirmed")
    else:
        print("Search canceled")
