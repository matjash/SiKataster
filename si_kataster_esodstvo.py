from qgis.PyQt import QtWidgets
from qgis.core import QgsTask
from qgis.core import QgsMessageLog, Qgis
from qgis.PyQt.QtCore import QCoreApplication

import keyring
import os
import subprocess
import sys

# Import the web client
from .si_kataster_2web import EsodstvoWebClient, verify_credentials

        
MESSAGE_CATEGORY = 'SiKataster'

def tr(message):
    return QCoreApplication.translate('SiKataster', message)

def check_esodstvo_credentials(username, password):
    """Check if e-Sodstvo credentials are valid"""
    try:
        return verify_credentials(username, password)
    except Exception as e:
        QgsMessageLog.logMessage(
            f"Credential check error: {str(e)}", 
            MESSAGE_CATEGORY, 
            Qgis.Warning
        )
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
        self.web_client = None
       

    def run(self):
        """Execute the PDF download task"""
        try:
            QgsMessageLog.logMessage(
                f"Starting PDF download for KO: {self.ko_id}, Parcela: {self.parcela}",
                MESSAGE_CATEGORY,
                Qgis.Info
            )
            
            # Initialize web client with headless mode
            self.web_client = EsodstvoWebClient(
                self.username, 
                self.password,
                headless=True
            )
            
            # Login
            if not self.web_client.login():
                self.exception = self.tr('Napaka pri prijavi v e-sodstvo')
                return False
            
            QgsMessageLog.logMessage("Login successful", MESSAGE_CATEGORY, Qgis.Info)
            
            # Select land registry role
            if not self.web_client.select_land_registry_role():
                self.exception = self.tr('Napaka pri izbiri vloge zemljiške knjige')
                return False
            
            QgsMessageLog.logMessage("Role selection successful", MESSAGE_CATEGORY, Qgis.Info)
            
            # Fill form with parcel data
            self.web_client.fill_parcel_form(self.ko_id, self.parcela)
            
            QgsMessageLog.logMessage("Form filled", MESSAGE_CATEGORY, Qgis.Info)
            
            # Download PDF
            self.pdf_path = self.web_client.download_pdf()
            
            if self.pdf_path and os.path.exists(self.pdf_path):
                QgsMessageLog.logMessage(
                    f"PDF downloaded: {self.pdf_path}",
                    MESSAGE_CATEGORY,
                    Qgis.Success
                )
                return True
            else:
                self.exception = self.tr('PDF ni bil prenešen. Preverite podatke o parceli.')
                return False
                
        except Exception as e:
            QgsMessageLog.logMessage(
                f"Error in run(): {str(e)}",
                MESSAGE_CATEGORY,
                Qgis.Critical
            )
            self.exception = str(e)
            return False
        finally:
            # Clean up web client
            if self.web_client:
                self.web_client.close()
        
    def finished(self, result):
        """Handle task completion"""
        # Hide loading label
        if self.loading_label:
            self.loading_label.setVisible(False)
        
        if result:
            # Success - open PDF
            QgsMessageLog.logMessage(
                f"Task completed successfully. Opening PDF: {self.pdf_path}",
                MESSAGE_CATEGORY,
                Qgis.Success
            )
            
            try:
                if sys.platform.startswith('darwin'):  # macOS
                    subprocess.call(['open', self.pdf_path])
                elif os.name == 'nt':  # Windows
                    os.startfile(self.pdf_path)
                elif os.name == 'posix':  # Linux
                    subprocess.call(['xdg-open', self.pdf_path])
            except Exception as e:
                QgsMessageLog.logMessage(
                    f"Error opening PDF: {str(e)}",
                    MESSAGE_CATEGORY,
                    Qgis.Warning
                )
        else:
            # Error - show message
            error_msg = self.exception if self.exception else self.tr('Težava pri prenešanju PDFa')
            
            QgsMessageLog.logMessage(
                f"Task failed: {error_msg}",
                MESSAGE_CATEGORY,
                Qgis.Critical
            )
            
            if self.loading_label:
                self.loading_label.setStyleSheet("color: red;")
                self.loading_label.setText(self.tr(f"Napaka: {error_msg}"))
                self.loading_label.setVisible(True)