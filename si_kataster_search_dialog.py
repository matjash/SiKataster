from qgis.PyQt.QtWidgets import QWidget,  QLineEdit, QPushButton, QLabel, QCompleter, QHBoxLayout, QVBoxLayout
from qgis.PyQt.QtCore import QStringListModel, Qt
from qgis.utils import iface
from qgis.core import QgsProject
from qgis.core import QgsCoordinateReferenceSystem, QgsVectorLayer, QgsMessageLog, Qgis, QgsAbstractMetadataBase, QgsApplication, QgsTask, QgsMessageLog, QgsFeatureRequest, QgsProcessingFeatureSourceDefinition
from PyQt5.QtWidgets import QWidget, QVBoxLayout, QLabel, QLineEdit, QCompleter, QPushButton, QSlider, QHBoxLayout, QStackedWidget, QComboBox,QCheckBox, QDoubleSpinBox
from PyQt5.QtCore import Qt
import processing
from .functions_container import (LoadKoTask, 
                                  LoadParcelsTask,
                                  FindParcelTask,
                                  is_wfs_accessible, 
                                  FetchByAreaTask)
        
MESSAGE_CATEGORY = 'SiKataster'




class ParcelDialog(QWidget):
    def __init__(self):
        super().__init__()
        self.iface = iface
        layout = QVBoxLayout()
        self.setWindowTitle(self.tr("Išči"))

        # Create a label for the slider
        self.search_mode_label = QLabel(self.tr("Iskanje po parcelah  /  Izbor s presekom sloja"))
        self.search_mode_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.search_mode_label)

        # Create the slider
        self.search_mode_slider = QSlider(Qt.Horizontal)
        self.search_mode_slider.setRange(0, 1)  # 0 = Parcel Search, 1 = Area Search
        self.search_mode_slider.setFixedSize(70, 20)  # Narrower and thicker
        self.search_mode_slider.setStyleSheet(self.get_slider_styles())
        self.search_mode_slider.setValue(0)  # Default to parcel search mode
        self.search_mode_slider.setSingleStep(1)
        layout.addWidget(self.search_mode_slider, alignment=Qt.AlignCenter)

        # Connect the slider's valueChanged signal to the switch_search_mode method
        self.search_mode_slider.valueChanged.connect(self.switch_search_mode)

        # Add stacked widget to switch between two search forms
        self.stacked_widget = QStackedWidget()
        layout.addWidget(self.stacked_widget)

        # First widget (KO and Parcel number search)
        self.parcel_search_widget = QWidget()
        parcel_search_layout = QVBoxLayout()

        self.ko_id_label = QLabel(self.tr('Naziv ali KO ID:'))
        self.ko_id_input = QLineEdit()
        parcel_search_layout.addWidget(self.ko_id_label)
        parcel_search_layout.addWidget(self.ko_id_input)

        self.ko_completer = QCompleter([], self)
        self.ko_completer.setCaseSensitivity(Qt.CaseInsensitive)
        self.ko_completer.setFilterMode(Qt.MatchContains)
        self.ko_id_input.setCompleter(self.ko_completer)

        self.parcela_label = QLabel(self.tr('Številka parcele:'))
        self.parcela_input = QLineEdit()
        parcel_search_layout.addWidget(self.parcela_label)
        parcel_search_layout.addWidget(self.parcela_input)

        self.parcela_completer = QCompleter([], self)
        self.parcela_completer.setCaseSensitivity(Qt.CaseInsensitive)
        self.parcela_completer.setFilterMode(Qt.MatchStartsWith)
        self.parcela_input.setCompleter(self.parcela_completer)

        self.loading_label = QLabel(self.tr('Nalaganje...'))
        self.loading_label.setVisible(False)
        parcel_search_layout.addWidget(self.loading_label)

        self.parcel_search_widget.setLayout(parcel_search_layout)

        # Create a horizontal layout for buttons in the parcel search mode
        button_layout = QHBoxLayout()

        # "Naloži" button (smaller)
        self.load_button = QPushButton(self.tr('Naloži kot sloj'))
        self.load_button.setFixedWidth(80)
        button_layout.addWidget(self.load_button)

        # "Poišči" button
        self.find_button = QPushButton(self.tr('Poišči'))
        button_layout.addWidget(self.find_button)

        # Add the horizontal button layout to the parcel_search_widget layout
        parcel_search_layout.addLayout(button_layout)

        self.parcel_search_widget.setLayout(parcel_search_layout)

        self.stacked_widget.addWidget(self.parcel_search_widget)

        # Second widget (Layer selection and search by area)
        self.area_search_widget = QWidget()
        area_search_layout = QVBoxLayout()

        self.layer_label = QLabel(self.tr('Izberi sloj za presek:'))
        area_search_layout.addWidget(self.layer_label)

        # ComboBox for selecting layers from the layer tree
        self.layer_combobox = QComboBox()
        self.populate_layer_combobox()
        area_search_layout.addWidget(self.layer_combobox)

        # Add a checkbox for "Selected features only"
        self.selected_features_checkbox = QCheckBox(self.tr("Le izbrane"))
        area_search_layout.addWidget(self.selected_features_checkbox)
        
        #Add a buffer spinbox
        buffer_layout = QHBoxLayout()
        self.buffer_spinbox = QDoubleSpinBox()
        self.buffer_spinbox.setRange(-1000000, 1000000)  
        self.buffer_spinbox.setSuffix(" m")  
        self.buffer_spinbox.setValue(0) 
        buffer_layout.addWidget(QLabel(self.tr("Buffer (m):")))
        buffer_layout.addWidget(self.buffer_spinbox)
        area_search_layout.addLayout(buffer_layout)


        # Add a search button for area-based search
        self.search_area_button = QPushButton(self.tr('Naloži izbrane parcele kot začasni sloj'))
        area_search_layout.addWidget(self.search_area_button)
        area_search_layout.addWidget(self.loading_label)


        self.area_search_widget.setLayout(area_search_layout)
        self.stacked_widget.addWidget(self.area_search_widget)

        self.setLayout(layout)

        self.current_layer = None

        # Connect signals
        self.find_button.clicked.connect(self.find_parcel)
        self.load_button.clicked.connect(self.load_parcel)
        self.ko_id_input.editingFinished.connect(self.load_parcels_for_selected_ko)
        self.search_area_button.clicked.connect(self.fetch_to_layer)
        self.search_mode_slider.valueChanged.connect(self.switch_search_mode)
        self.layer_combobox.currentIndexChanged.connect(self.update_selected_features_checkbox)
        QgsProject.instance().layersAdded.connect(self.populate_layer_combobox)
        QgsProject.instance().layersRemoved.connect(self.populate_layer_combobox)
        self.iface.layerTreeView().currentLayerChanged.connect(self.populate_layer_combobox)
        self.layer_combobox.currentIndexChanged.connect(self.update_selected_features_checkbox)
        self.layer_combobox.currentIndexChanged.connect(self.on_layer_selection_change)
    

        if not is_wfs_accessible():
            self.loading_label.setText(self.tr('Server ni dostopen.'))
            self.loading_label.setVisible(True)
            self.loading_label.setStyleSheet("color: red;")
        else:
            self.load_ko()

    def on_layer_selection_change(self):
        if self.current_layer:
            try:
                self.current_layer.selectionChanged.disconnect(self.update_selected_features_checkbox)
            except TypeError:
                pass 
        selected_layer = self.layer_combobox.currentData()
        self.current_layer = selected_layer
        if self.current_layer and isinstance(self.current_layer, QgsVectorLayer):
            self.current_layer.selectionChanged.connect(self.update_selected_features_checkbox)
        self.update_selected_features_checkbox()

    def update_selected_features_checkbox(self):
        selected_layer_id = self.layer_combobox.currentData()
        selected_layer = selected_layer_id
        if selected_layer and isinstance(selected_layer, QgsVectorLayer):
            selected_features = selected_layer.selectedFeatures()
            if len(selected_features) > 0:
                self.selected_features_checkbox.setEnabled(True)
                self.selected_features_checkbox.setChecked(True)
            else:
                self.selected_features_checkbox.setChecked(False)
                self.selected_features_checkbox.setEnabled(False)
        else:
            self.selected_features_checkbox.setChecked(False)
            self.selected_features_checkbox.setEnabled(False)  

    def get_slider_styles(self):
        return """
        QSlider::groove:horizontal {
            border: 1px solid #bbb;
            background: #eee;
            height: 16px;  /* Thickness */
            border-radius: 8px;
        }

        QSlider::handle:horizontal {
            background: #5c5c5c;
            border: 1px solid #777;
            width: 26px;  /* Width of the slider handle */
            height: 26px; /* Makes the handle a square */
            margin: -5px 0;
            border-radius: 13px;
        }

        QSlider::sub-page:horizontal {
            background: #ccc;
            border-radius: 8px;
        }

        QSlider::add-page:horizontal {
            background: #a6ce39;
            
            border-radius: 8px;
        }
        """
    
    # Switch between parcel search and area search
    def switch_search_mode(self):
        if self.search_mode_slider.value() == 0:
            self.stacked_widget.setCurrentWidget(self.parcel_search_widget)
        else:
            self.stacked_widget.setCurrentWidget(self.area_search_widget)

    def populate_layer_combobox(self):
        layers = QgsProject.instance().mapLayers().values()
        self.layer_combobox.clear() 
        for layer in layers:
            if isinstance(layer, QgsVectorLayer):
                self.layer_combobox.addItem(layer.name(), layer)

    def fetch_to_layer(self):
        self.selected_layer = self.layer_combobox.currentData()
        self.buffer_value = self.buffer_spinbox.value()
        self.loading_label.setText(self.tr('Iskanje parcel...'))
        self.loading_label.setVisible(True)
        self.loading_label.setStyleSheet("color: black;")

        if self.selected_features_checkbox.isChecked():
            self.selected_layer = QgsProcessingFeatureSourceDefinition(self.selected_layer.id(), True)
   
        self.fetch_by_area_task = FetchByAreaTask(description=self.tr('Izberi po območju, naloži sloj'), layer=self.selected_layer, buffer=self.buffer_value, loading_label=self.loading_label)
        QgsApplication.taskManager().addTask(self.fetch_by_area_task)


    def load_ko(self):
        self.loading_label.setVisible(True)
        self.load_ko_task = LoadKoTask(description=self.tr('Branje ko-jev'), callback=self.update_ko_completer)
        QgsApplication.taskManager().addTask(self.load_ko_task)
   
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

            self.load_parcels_task = LoadParcelsTask(description=self.tr('Branje parcel'), ko_id=ko_id, callback=self.update_parcel_completer)
            QgsApplication.taskManager().addTask(self.load_parcels_task)

    def update_parcel_completer(self, parcel_list):
        self.loading_label.setVisible(False)
        if len(parcel_list) == 0:
            self.loading_label.setStyleSheet("color: red;")
            self.loading_label.setText(self.tr('Ne najdem parcel za K. O.'))
        else:
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
                self.find_parcel_task= FindParcelTask(description=self.tr('Iskanje'),iface=self.iface, loading_label=self.loading_label, ko_id=ko_id, parcela=parcela)
                QgsApplication.taskManager().addTask(self.find_parcel_task)
            
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
                self.load_parcel_task= FindParcelTask(description=self.tr('Naloži'),iface=self.iface, loading_label=self.loading_label, ko_id=ko_id, parcela=parcela)
                QgsApplication.taskManager().addTask(self.load_parcel_task)
           
            else:
                self.loading_label.setStyleSheet("color: black;")
                self.loading_label.setText(self.tr('Potrebno je vnesti K. O. in parcelo'))  
                self.loading_label.setVisible(True)

