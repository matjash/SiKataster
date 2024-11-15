from qgis.PyQt.QtCore import QThread, pyqtSignal
from qgis.core import QgsCoordinateReferenceSystem, QgsVectorLayer, QgsMessageLog, Qgis, QgsAbstractMetadataBase
from qgis.PyQt.QtGui import QColor
import processing

import os
import csv
import requests  
from datetime import datetime



def is_wfs_accessible():
    wfs_url = "https://ipi.eprostor.gov.si/wfs-si-gurs-kn/wfs?service=WFS&version=2.0.0&request=GetCapabilities"
    
    try:
        # Use HEAD request to check if the server is accessible without downloading the content
        response = requests.head(wfs_url, timeout=3)
        if response.status_code == 200:
            return True  # Server is accessible
        else:
            return False  # Server returned an error status
    except requests.Timeout:
        return False  # Server timed out
    except requests.ConnectionError:
        return False  # Unable to connect to the server
    except requests.RequestException:
        return False  # Catch all other exceptions

def connect_to_wfs(return_type, typeName=None, propertyName=None, cql_filter=None, bbox=None, count=1000):
    # Base WFS URL
    wfs_url = "https://ipi.eprostor.gov.si/wfs-si-gurs-kn/wfs"
    
    # Basic parameters
    params = {
        "service": "WFS",
        "version": "2.0.0",
        "request": "GetFeature",
        "count": count,  # Limit the number of features per page
    }

    # Add optional parameters if provided
    if typeName:
        params["typeName"] = typeName
    if propertyName:
        params["propertyName"] = propertyName
    if cql_filter:
        params["CQL_FILTER"] = cql_filter
    if bbox:
        params["BBOX"] = bbox
    all_features = []
    if return_type == 'json':
        params["outputFormat"] = "application/json"
        startIndex = 0
        while True:
            # Set the startIndex for pagination
            params["startIndex"] = startIndex
            try:
                # Make the request
                response = requests.get(wfs_url, params=params, timeout=10)
                response.raise_for_status()
                data = response.json()
                
                # Extract features and append them to the all_features list
                features = data.get('features', [])
                all_features.extend(features)
                
                # If no more features are returned, break the loop
                if len(features) < count:
                    break

                # Increment startIndex to fetch the next page of data
                startIndex += count

            except (requests.RequestException, requests.ConnectionError) as e:
                return {'error': 'ipi.eprostor.gov.si je nedostopen'}

        # Return all features after paging is complete
        return {'features': all_features}

    elif return_type == 'layer':
        # Return as a WFS layer directly (no pagination needed)
        url = f"{wfs_url}?{'&'.join(f'{key}={value}' for key, value in params.items())}"
        wfs_layer = QgsVectorLayer(url, typeName, "WFS")
        return wfs_layer

class LoadParcelWorker(QThread):
    data_loaded = pyqtSignal(list)
    def __init__(self, ko_id):
        super().__init__()
        self.plugin_folder = os.path.dirname(__file__)
        self.csv_parcel_file = os.path.join(self.plugin_folder, 'parcele.csv')
        self.ko_id = ko_id

    def run(self):
        parcel_list_ko = self.parcel_by_ko_id(self.ko_id)
        self.data_loaded.emit(parcel_list_ko)


    def parcel_by_ko_id(self, ko_id):
        data = connect_to_wfs(return_type='json', typeName="SI.GURS.KN:OSNOVNI_PARCELE", propertyName="ST_PARCELE", cql_filter=f"KO_ID={ko_id}")
        if data:
            parcel_list = [feature['properties']['ST_PARCELE'] for feature in data.get('features', [])]
            return parcel_list  
     
class LoadKoWorker(QThread):
    data_loaded = pyqtSignal(dict)
    def __init__(self):
        super().__init__()
        self.plugin_folder = os.path.dirname(__file__)
        self.csv_ko_file = os.path.join(self.plugin_folder, "ko.csv")

    def run(self):
        if os.path.exists(self.csv_ko_file):
            ko_dict = self.load_from_csv()
            self.data_loaded.emit(ko_dict)
            self.update_ko_csv()
        else:
            ko_dict = self.get_ko_id_and_naziv_from_wfs()
            if ko_dict:
                self.save_to_csv(ko_dict)
                self.data_loaded.emit(ko_dict)
            else:
                QgsMessageLog.logMessage("No data retrieved from WFS", 'Plugin', Qgis.Warning)
            self.update_ko_csv()


    def load_from_csv(self):
        ko_dict = {}
        with open(self.csv_ko_file, mode='r', newline='', encoding='utf-8') as file:
            reader = csv.DictReader(file)
            for row in reader:
                ko_dict[row['KO_ID']] = row['NAZIV']
        return ko_dict

    def save_to_csv(self, ko_dict):
        with open(self.csv_ko_file, mode='w', newline='', encoding='utf-8') as file:
            writer = csv.writer(file)
            writer.writerow(['KO_ID', 'NAZIV'])  # Header
            for ko_id, naziv in ko_dict.items():
                writer.writerow([ko_id, naziv])

    def update_ko_csv(self):
        ko_dict = self.get_ko_id_and_naziv_from_wfs()
        if ko_dict:
            self.save_to_csv(ko_dict)
    
    def get_ko_id_and_naziv_from_wfs(self):
        data = connect_to_wfs(return_type='json', typeName="SI.GURS.KN:KATASTRSKE_OBCINE", propertyName="KO_ID,NAZIV")
        if data:
            ko_dict = {feature['properties']['KO_ID']: feature['properties']['NAZIV'] for feature in data.get('features', [])}
            return ko_dict
        return {}

def load_filtered_parcel_layer(type_name, cql_filter):
    layer = connect_to_wfs(return_type='layer', typeName=type_name, cql_filter=cql_filter)
    if not layer.isValid():
        QgsMessageLog.logMessage("Failed to load layer: Layer is not valid.", level=Qgis.Critical)
        return None
    else:
        QgsMessageLog.logMessage("Layer loaded successfully.", level=Qgis.Info)
        return layer

def copy_wfs_to_scratch_layer(wfs_layer, geom_str='Polygon'):
    # Create the temporary memory layer
    temp_layer = QgsVectorLayer(f'{geom_str}?crs={wfs_layer.crs().authid()}', wfs_layer.name(), "memory")    
    
    temp_layer_data_provider = temp_layer.dataProvider()

    # Copy fields from WFS layer to temporary layer
    temp_layer_data_provider.addAttributes(wfs_layer.fields())
    temp_layer.updateFields()
    # Copy all features from WFS layer to temporary layer
    for feature in wfs_layer.getFeatures():
        temp_layer_data_provider.addFeature(feature)
    
    update_metadata(wfs_layer, 'OGC:WFS')
    transfer_metadata(wfs_layer, temp_layer)
    temp_layer.updateExtents()
    return temp_layer

def layer_to_single_geometry(layer):
    if layer and layer.isValid():
        result = processing.run("native:dissolve", {'INPUT': layer, 'OUTPUT': 'memory:'})
        dissolved_layer = result['OUTPUT']
        dissolved_feature = next(dissolved_layer.getFeatures())
        dissolved_geometry = dissolved_feature.geometry()
        type_oif = dissolved_geometry.type()
        QgsMessageLog.logMessage(f"Geometry type: {type_oif}", level=Qgis.Info)
        return dissolved_geometry
    else:
        QgsMessageLog.logMessage("Layer is not valid.", level=Qgis.Critical)
        return None

def zoom_to_and_flash_geometry_from_layer(iface, geometry):
    if geometry:
        canvas = iface.mapCanvas()
        canvas.setExtent(geometry.boundingBox())
        canvas.refresh()
        # Flash the geometry (optional)
        canvas.flashGeometries(
            [geometry], 
            QgsCoordinateReferenceSystem('EPSG:3794'),
            QColor(255, 66, 0),  # Flash color (orange)
            QColor(0, 66, 0),    # Secondary color (green)
            flashes=3,  
            duration=500  
        )
        return True
    else:
        QgsMessageLog.logMessage("Geometry is not valid.", level=Qgis.Critical)
        return False

def add_metadata_to_layer(layer):
    layer.setMetadata({'metadata': {'name': 'Katastarska obcina'}})

def transfer_metadata(source_layer, target_layer):
    target_layer.startEditing()
    source_metadata = source_layer.metadata() 
    target_layer.setMetadata(source_metadata)
    target_layer.commitChanges()

#update history
def update_history(layer, history):
    layer.startEditing()
    metadata = layer.metadata()
    metadata.addHistoryItem(str(history))
    layer.setMetadata(metadata)
    layer.commitChanges()


def update_metadata(layer, link_type):
    #append history in metadata
    time_now = datetime.now()
    history = {}
    history['timestamp'] = time_now.isoformat()
    history['process'] = 'prevzem sloja'
    history['title'] = layer.name() 
    history['source'] = layer.publicSource()
    update_history(layer, history)

    description = 'Dostop' + ': ' + str(time_now)
    name = 'Vir podatkov'
    link = QgsAbstractMetadataBase.Link(name=name, type=link_type, url=layer.publicSource())
    link.description = description
    layer.startEditing()
    layer_metadata = layer.metadata()
    layer_metadata.addLink(link)
    layer.setMetadata(layer_metadata)
    layer.commitChanges()