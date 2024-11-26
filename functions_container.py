from qgis.PyQt.QtCore import QThread, pyqtSignal
from qgis.core import QgsCoordinateReferenceSystem, QgsVectorLayer, QgsMessageLog, Qgis, QgsAbstractMetadataBase, QgsApplication, QgsTask, QgsMessageLog, QgsNetworkAccessManager,QgsProject, QgsLayerDefinition
from qgis.PyQt.QtGui import QColor
import processing
from qgis.core import QgsNetworkAccessManager
from qgis.PyQt.QtCore import QUrl, QEventLoop, QCoreApplication
from qgis.PyQt.QtNetwork import QNetworkRequest
from qgis.utils import iface
import os
import csv
import requests  
from datetime import datetime


MESSAGE_CATEGORY = 'SiKataster'



def tr(message):
    return QCoreApplication.translate('SiKataster', message)


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


def  connect_to_wfs(return_type, typeName=None, propertyName=None, cql_filter=None, bbox=None):
    wfs_url = "https://ipi.eprostor.gov.si/wfs-si-gurs-kn/wfs"
    params = {
        'service': 'WFS',
        'version': '2.0.0',
        'request': 'GetFeature',
        'pagingEnabled':'true',
        'pageSize':'20000',
        'restrictToRequestBBOX':'1'
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
        try:
            response = requests.get(wfs_url, params=params, timeout=10)
            response.raise_for_status()
            data = response.json()
            features = data.get('features', [])
            all_features.extend(features)
        except (requests.RequestException, requests.ConnectionError) as e:
                return {'error': tr(f'Server {wfs_url} je nedostopen')}
     
        return {'features': all_features}

    elif return_type == 'layer':
        # Return as a WFS layer directly (no pagination needed)
        url = f"{wfs_url}?{'&'.join(f'{key}={value}' for key, value in params.items())}"
        wfs_layer = QgsVectorLayer(url, typeName, "WFS")
        return wfs_layer

class LayerMetadataManager:
    def __init__(self):
        self.tr = tr
        pass

    def transfer_metadata(self, source_layer, target_layer):
        target_layer.startEditing()
        source_metadata = source_layer.metadata()
        target_layer.setMetadata(source_metadata)
        target_layer.commitChanges()

    def update_history(self, layer, history):
        layer.startEditing()
        metadata = layer.metadata()
        metadata.addHistoryItem(str(history))
        layer.setMetadata(metadata)
        layer.commitChanges()

    def update_metadata(self, layer, link_type):
        time_now = datetime.now()
        history = {
            'timestamp': time_now.isoformat(),
            'process': 'prevzem sloja',
            'title': layer.name(),
            'source': layer.publicSource()
        }
        self.update_history(layer, history)

        description = self.tr('Dostop') + ': ' + str(time_now)
        name = self.tr('Vir podatkov')
        link = QgsAbstractMetadataBase.Link(name=name, type=link_type, url=layer.publicSource())
        link.description = description

        layer.startEditing()
        layer_metadata = layer.metadata()
        layer_metadata.addLink(link)
        layer.setMetadata(layer_metadata)
        layer.commitChanges()

metadata_manager = LayerMetadataManager()

class LoadParcelsTask(QgsTask):
    def __init__(self, description=None, ko_id=None, callback=None):
        super().__init__(description, QgsTask.CanCancel)
        self.ko_id = ko_id
        self.result_list = []
        self.exception = None
        self.callback = callback
        self.connect_to_wfs = connect_to_wfs
        self.data = None
        self.tr = tr

    def run(self):
        try:
            self.data = self.connect_to_wfs(return_type='json', typeName="SI.GURS.KN:OSNOVNI_PARCELE", propertyName="ST_PARCELE", cql_filter=f"KO_ID={self.ko_id}")
            if 'error' in self.data:
                self.exception = self.data['error']
                return False
            elif 'features' in self.data:
                self.result_list = [feature['properties']['ST_PARCELE'] for feature in self.data.get('features', [])] 
                return True
        
        except Exception as e:
            self.exception = e
            return False
      
    def finished(self, result):
        if result and len(self.result_list) > 0:     
            self.result_list.sort()
            self.callback(self.result_list)
        else:
            self.result_list = []
            self.callback(self.result_list)

    def cancel(self):
        QgsMessageLog.logMessage(self.tr("Uporabnik je preklical nalaganje parcele"), MESSAGE_CATEGORY, Qgis.Info)
        super().cancel()

class LoadKoTask(QgsTask):
    def __init__(self, description=None, callback=None):
        super().__init__(description, QgsTask.CanCancel)
        self.callback = callback
        self.plugin_folder = os.path.dirname(__file__)
        self.csv_ko_file = os.path.join(self.plugin_folder, "ko.csv")
        self.exception = None
        self.tr = tr

    def run(self):
        try:
            if os.path.exists(self.csv_ko_file):
                self.ko_dict = self.load_from_csv()
                self.update_ko_csv()
                return True
            else:
                self.ko_dict = self.load_from_wfs()
                if self.ko_dict:
                    self.save_to_csv(self.ko_dict)
                    return True
                else:
                    return False

        except Exception as e:
            self.exception = e
            return False

    def finished(self, result):
        if result:
            self.callback(self.ko_dict)
        else:
            self.callback({})
            QgsMessageLog.logMessage(f"Error: {self.exception if self.exception else self.tr('Neznana napaka')}", MESSAGE_CATEGORY, Qgis.Warning)


    def load_from_wfs(self):
        data = connect_to_wfs(return_type='json', typeName="SI.GURS.KN:KATASTRSKE_OBCINE", propertyName="KO_ID,NAZIV")
        if data:
            ko_dict = {feature['properties']['KO_ID']: feature['properties']['NAZIV'] for feature in data.get('features', [])}
            return ko_dict
        
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
        ko_dict = self.load_from_wfs()
        if ko_dict:
            self.save_to_csv(ko_dict)

  

class FindParcelTask(QgsTask):
    def __init__(self, description=None, iface=None, loading_label=None, ko_id=None, parcela=None):
        super().__init__(description, QgsTask.CanCancel)
        self.description = description
        self.iface = iface
        self.loading_label = loading_label
        self.parcela = parcela
        self.ko_id = ko_id
        self.exception = None
        self.tr = tr
        self.geometry = None     
        self.flash_it = zoom_to_and_flash_geometry_from_layer

    def run(self):
        try:
            self.layer = connect_to_wfs(return_type='layer', typeName="SI.GURS.KN:PARCELE", cql_filter=f"KO_ID={self.ko_id} AND ST_PARCELE='{self.parcela}'")
            if self.layer.isValid():
                self.geometry = next(self.layer.getFeatures()).geometry()
                if self.description == 'Naloži':
                    self.local_layer = layer_to_scratch_layer(self.layer)
                    self.local_layer.setName(f"K. O. {self.ko_id}, parcela {self.parcela}") 
        
                return True
            else:
                self.exception = self.tr('Ne najdem parcele.')
                return False
        except Exception as e:
            self.exception = e
            return False
        
    def finished(self, result):
        if result:
            self.flash_it(self.iface, self.geometry)
            if self.description == 'Naloži':
                QgsProject.instance().addMapLayer(self.local_layer)  
        else:
            QgsMessageLog.logMessage(f"Error: {self.exception if self.exception else self.tr('Neznana napaka')}", MESSAGE_CATEGORY, Qgis.Warning)
            self.loading_label.setStyleSheet("color: red;")
            self.loading_label.setText(self.tr(f"Error: {self.exception if self.exception else self.tr('Neznana napaka')}"))   
            self.loading_label.setVisible(True)


class LoadQlrTask(QgsTask):
    def __init__(self, description=None, qlr_file=None, loading_label=None):
        super().__init__(description, QgsTask.CanCancel)
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
                self.exception = self.tr('Težave pri nalaganju QLR datoteke')
                return False
        except Exception as e:
            self.exception = e
            return False
    def finished(self, result):
        if result:
            QgsMessageLog.logMessage(self.tr(f"Uspešno nalaganje QLR datoteke"), MESSAGE_CATEGORY, Qgis.Info)
            self.loading_label.setVisible(False)
        else:
            self.loading_label.setStyleSheet("color: red;")
            self.loading_label.setText(self.tr(f"Error: {self.exception if self.exception else self.tr('Neznana napaka')}"))
            self.loading_label.setVisible(True)
            QgsMessageLog.logMessage(f"Error: {self.exception if self.exception else self.tr('Neznana napaka')}", MESSAGE_CATEGORY, Qgis.Warning)
        

class FetchByAreaTask(QgsTask):
    def __init__(self, description=None, loading_label=None, layer=None, buffer=None):
        super().__init__(description, QgsTask.CanCancel)
        self.description = description
        self.loading_label = loading_label
        self.selection_layer = layer
        self.buffer = buffer
        self.exception = None
        self.tr = tr

    def run(self):
        try:   
            fixed_layer = processing.run('native:fixgeometries', {
                    'INPUT': self.selection_layer,
                    'OUTPUT': 'memory:'
                })['OUTPUT']

            dissolved_layer = processing.run('native:dissolve',  {
                    'INPUT': fixed_layer,
                    'FIELD': [],
                    'OUTPUT': 'memory:'
                })['OUTPUT']

            if self.buffer != 0:
                buffer_layer = processing.run('native:buffer', {
                    'INPUT': dissolved_layer,
                    'DISTANCE': self.buffer,
                    'OUTPUT': 'memory:'
                })['OUTPUT']
                self.selection_layer = buffer_layer
            else:
                self.selection_layer = dissolved_layer

            layer_bbox = self.selection_layer.extent()
            bbox = f"{layer_bbox.xMinimum()},{layer_bbox.yMinimum()},{layer_bbox.xMaximum()},{layer_bbox.yMaximum()}"
            self.wfs_layer = connect_to_wfs(return_type='layer', typeName="SI.GURS.KN:OSNOVNI_PARCELE", bbox=bbox)
    
            self.selection = processing.run("native:extractbylocation", 
                                    {'INPUT': self.wfs_layer,
                                    'PREDICATE': [0],
                                    'INTERSECT':self.selection_layer,
                                    'OUTPUT':'TEMPORARY_OUTPUT'})['OUTPUT']
            self.local_layer = layer_to_scratch_layer(self.selection)
            return True
     
        except Exception as e:
            self.exception = e
            return False

    def finished(self, result): 
        if result:
            if self.description == self.tr('Izberi po območju, naloži sloj'):
                QgsProject.instance().addMapLayer(self.local_layer)
            self.local_layer.setName(self.tr(f"Izbor parcel"))
            self.loading_label.setVisible(False)
        else:
            self.loading_label.setStyleSheet("color: red;")
            self.loading_label.setText(self.tr(f"Error: {self.exception if self.exception else self.tr('Neznana napaka')}"))
            self.loading_label.setVisible(True)
            QgsMessageLog.logMessage(f"Error: {self.exception if self.exception else self.tr('Neznana napaka')}", MESSAGE_CATEGORY, Qgis.Warning)

            

def layer_to_scratch_layer(wfs_layer, geom_str='Polygon'):
    temp_layer = QgsVectorLayer(f'{geom_str}?crs={wfs_layer.crs().authid()}', wfs_layer.name(), "memory")    
    temp_layer_data_provider = temp_layer.dataProvider()

    # Copy fields from WFS layer to temporary layer
    temp_layer_data_provider.addAttributes(wfs_layer.fields())
    temp_layer.updateFields()
    # Copy all features from WFS layer to temporary layer
    for feature in wfs_layer.getFeatures():
        temp_layer_data_provider.addFeature(feature)
    
    metadata_manager.update_metadata(wfs_layer, 'OGC:WFS')
    metadata_manager.transfer_metadata(wfs_layer, temp_layer)
    temp_layer.updateExtents()
    return temp_layer



def zoom_to_and_flash_geometry_from_layer(iface, geometry):
    if geometry:
        canvas = iface.mapCanvas()
        canvas.setExtent(geometry.boundingBox())
        canvas.refresh()
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
        QgsMessageLog.logMessage(tr("Ni geometrije"), MESSAGE_CATEGORY, Qgis.Warning)
        return False




