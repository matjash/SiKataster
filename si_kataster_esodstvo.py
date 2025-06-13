from qgis.PyQt import QtWidgets
from qgis.core import QgsTask
from qgis.core import QgsMessageLog, Qgis
from qgis.PyQt.QtCore import  QCoreApplication

import keyring
import os
import subprocess
import sys


        
MESSAGE_CATEGORY = 'SiKataster'

def tr(message):
    return QCoreApplication.translate('SiKataster', message)

def check_esodstvo_credentials(username, password):
    try:
        # Preveri če je geslo in usernae veljavno na strani esodstvo


        return True
    except Exception as e:
        return False
    

class EsodstvoCredentialsDialog(QtWidgets.QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Nastavi uporabniško ime in geslo e-sodstva")

        layout = QtWidgets.QFormLayout(self)

        self.username_edit = QtWidgets.QLineEdit()
        self.password_edit = QtWidgets.QLineEdit()
        self.password_edit.setEchoMode(QtWidgets.QLineEdit.Password)

        self.show_password_checkbox = QtWidgets.QCheckBox("Pokaži geslo")
        self.show_password_checkbox.toggled.connect(self.toggle_password_visibility)

        layout.addRow("Uporabniško ime:", self.username_edit)
        layout.addRow("Geslo:", self.password_edit)
        layout.addRow("", self.show_password_checkbox)

        url_esodstvo = "https://evlozisce.sodisce.si/esodstvo/prijava.html"
        layout.addWidget(QtWidgets.QLabel(f"<a href='{url_esodstvo}'>Registracija novega uporabnika e-sodstva</a>"))

        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        # Load saved credentials from keyring
        saved_username = keyring.get_password("SiKataster", "esodstvo_username")
        saved_password = keyring.get_password("SiKataster", "esodstvo_password")
        if saved_username:
            self.username_edit.setText(saved_username)
        if saved_password:
            self.password_edit.setText(saved_password)

    def toggle_password_visibility(self, checked):
        self.password_edit.setEchoMode(QtWidgets.QLineEdit.Normal if checked else QtWidgets.QLineEdit.Password)


    def get_credentials(self):
        return self.username_edit.text(), self.password_edit.text()



class FetchZKPdfTask(QgsTask):
    def __init__(self, description=None, iface=None, loading_label=None, ko_id=None, parcela=None, username=None, password=None):
        super().__init__(description, QgsTask.CanCancel)
        self.description = description
        self.iface = iface
        self.loading_label = loading_label
        self.parcela = parcela
        self.ko_id = ko_id
        self.exception = None
        self.tr = tr
        self.username = username
        self.password = password
        self.pdf_path = None    
       

    def run(self):
        try:
            ##tukaj je logika za prenešanje pdf-ja

            

            #če je pdf uspešno prenešen, vrni True in nastavi self.pdf_path 
            self.pdf_path = 'path/to/pdf'
            
            if self.pdf_path is not None:
                return True
            #Če ni uspešno prenešen, vrni False in vzrok težave
            else:
                self.exception = self.tr('Težave pri prenešanju PDF')
                return False
        except Exception as e:
            self.exception = e
            return False
        
    def finished(self, result):
        #Če je pdf uspešno prenešen, odpri pdf
        if result:
            if sys.platform.startswith('darwin'):  # macOS
                subprocess.call(['open', self.pdf_path])
            elif os.name == 'nt':  # Windows
                os.startfile(self.pdf_path)
            elif os.name == 'posix':  # Linux
                subprocess.call(['xdg-open', self.pdf_path])
            
        else:
            self.loading_label.setStyleSheet("color: red;")
            self.loading_label.setText(self.tr(f"Error: {self.exception if self.exception else self.tr('Težava pri prenešanju PDFa')}"))   
            self.loading_label.setVisible(True)
