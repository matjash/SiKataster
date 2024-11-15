from PyQt5 import QtWidgets
from qgis.core import QgsProject, QgsVectorLayer, QgsMessageLog, Qgis
from qgis.utils import iface

import processing
from .functions_container import connect_to_wfs

class PresekDialog(QtWidgets.QWidget):
    def __init__(self):
        super().__init__()
        self.iface = iface
        # Create layout for the Presek Parcel tab
        layout = QtWidgets.QVBoxLayout()

        # Label for instructions
        self.label = QtWidgets.QLabel(self.tr("Izberi sloj za presek"))
        layout.addWidget(self.label)

        # Create a combo box to list vector layers
        self.layer_combobox = QtWidgets.QComboBox()
        self.load_vector_layers()  # Initial load of layers
        layout.addWidget(self.layer_combobox)

        # Button to fetch features
        self.fetch_button = QtWidgets.QPushButton("Fetch Intersecting Features")
        self.fetch_button.clicked.connect(self.fetch_intersecting_features)
        layout.addWidget(self.fetch_button)

        self.result_label = QtWidgets.QLabel("Result will be displayed here.")
        layout.addWidget(self.result_label)

        self.setLayout(layout)
        self.iface.layerTreeView().currentLayerChanged.connect(self.load_vector_layers)
          

    def load_vector_layers(self):
        """Load all vector layers into the combo box"""
        layers = QgsProject.instance().mapLayers().values()
        self.layer_combobox.clear()  # Clear existing items before loading new layers
        for layer in layers:
            if isinstance(layer, QgsVectorLayer):
                self.layer_combobox.addItem(layer.name(), layer)

    def fetch_intersecting_features(self):
        """Fetch features from WFS that intersect with the selected vector layer"""
        layer = self.layer_combobox.currentData()
        if layer:
            # Update the combo box with the latest layers every time the button is clicked
            self.load_vector_layers()

            # Fetch features from WFS that intersect with this bbox
            self.get_intersecting_wfs_features(layer)

    
    def get_intersecting_wfs_features(self, layer):
        # Get the bounding box of the selected layer
        bbox = layer.extent()

        # Perform dissolve operation on the layer
        dissolved_layer = processing.run('native:dissolve',  {
            'INPUT': layer,
            'FIELD': [],  # Empty list to dissolve all features
            'OUTPUT': 'memory:', 
        })['OUTPUT']

        # Collect all dissolved geometries (in case there are multiple dissolved features)
        dissolved_geometries = []
        for feature in dissolved_layer.getFeatures():
            dissolved_geometries.append(feature.geometry())

        # Build the WFS request URL
        base_url = "https://ipi.eprostor.gov.si/wfs-si-gurs-kn/wfs"
        params = {
            "request": "GetFeature",
            "typeName": "SI.GURS.KN:OSNOVNI_PARCELE",
            "srsname": "EPSG:3794",
            "BBOX": f"{bbox.xMinimum()},{bbox.yMinimum()},{bbox.xMaximum()},{bbox.yMaximum()}",
            "outputFormat": "application/json"  # Make sure to specify the format
        }
        url = f"{base_url}?{'&'.join(f'{key}={value}' for key, value in params.items())}"
        QgsMessageLog.logMessage(f"Request URL: {url}", level=Qgis.Info)

        bbox = f"{bbox.xMinimum()},{bbox.yMinimum()},{bbox.xMaximum()},{bbox.yMaximum()}"
        parcele_layer = connect_to_wfs(return_type='layer', typeName="SI.GURS.KN:OSNOVNI_PARCELE", bbox=bbox)
   

        located = processing.run("native:extractbylocation", 
                                 {'INPUT': parcele_layer,
                                  'PREDICATE': [0],
                                  'INTERSECT':layer,
                                  'OUTPUT':'TEMPORARY_OUTPUT'})
        QgsProject.instance().addMapLayer(located['OUTPUT'])

        """
        if not bbox_features_layer.isValid():
            QgsMessageLog.logMessage("Failed to load WFS layer.", level=Qgis.Critical)
            return []

        # List to store the intersecting features
        intersecting_features = []

        # Iterate over WFS features and check if they intersect with any dissolved geometry
        for wfs_feature in bbox_features_layer.getFeatures():
            wfs_geometry = wfs_feature.geometry()

            # Check intersection with all dissolved geometries
            for dissolved_geometry in dissolved_geometries:
                if wfs_geometry.intersects(dissolved_geometry):
                    intersecting_features.append(wfs_feature)
                    break  # Stop checking further geometries once an intersection is found
        """
        return True
