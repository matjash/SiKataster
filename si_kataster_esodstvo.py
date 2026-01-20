from qgis.PyQt import QtWidgets
from qgis.core import QgsTask
from qgis.core import QgsMessageLog, Qgis
from qgis.PyQt.QtCore import QCoreApplication
from qgis.PyQt.QtWidgets import QFileDialog

import keyring
import os
import subprocess
import sys
import time

        
MESSAGE_CATEGORY = 'SiKataster'

def tr(message):
    return QCoreApplication.translate('SiKataster', message)

def check_esodstvo_credentials(username, password):
    """Check if e-Sodstvo credentials are valid"""
    try:
        # Import here to avoid circular import
        from .si_kataster_2web import verify_credentials
        return verify_credentials(username, password)
    except Exception as e:
        QgsMessageLog.logMessage(
            f"Credential check error: {str(e)}", 
            MESSAGE_CATEGORY, 
            Qgis.Warning
        )
        return False


class DownloadFolderDialog(QtWidgets.QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("Nastavi mapo za prenose"))
        
        layout = QtWidgets.QVBoxLayout(self)
        
        # Get current download folder
        from .si_kataster_2web import get_default_download_folder
        current_folder = get_default_download_folder()
        
        # Info label
        info_label = QtWidgets.QLabel(tr("Trenutna mapa za prenose PDF datotek:"))
        layout.addWidget(info_label)
        
        # Current folder display
        self.folder_label = QtWidgets.QLabel(current_folder)
        self.folder_label.setStyleSheet("QLabel { background-color: #f0f0f0; padding: 5px; }")
        layout.addWidget(self.folder_label)
        
        # Browse button
        self.browse_button = QtWidgets.QPushButton(tr("Izberi mapo..."))
        self.browse_button.clicked.connect(self.browse_folder)
        layout.addWidget(self.browse_button)
        
        # Buttons
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        
        self.selected_folder = current_folder
    
    def browse_folder(self):
        folder = QFileDialog.getExistingDirectory(
            self,
            tr("Izberi mapo za prenose"),
            self.selected_folder
        )
        if folder:
            self.selected_folder = folder
            self.folder_label.setText(folder)
    
    def get_folder(self):
        return self.selected_folder
    
    def accept(self):
        """Save the selected folder"""
        from .si_kataster_2web import set_download_folder, EsodstvoWebClient
        
        if set_download_folder(self.selected_folder):
            # Close the current session so new downloads use the new folder
            EsodstvoWebClient.close_shared_session()
            
            QgsMessageLog.logMessage(
                tr(f"Mapa za prenose spremenjena v: {self.selected_folder}"),
                MESSAGE_CATEGORY,
                Qgis.Info
            )
        
        super().accept()
    

class EsodstvoCredentialsDialog(QtWidgets.QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("Nastavi uporabniško ime in geslo e-sodstva"))

        layout = QtWidgets.QFormLayout(self)

        self.username_edit = QtWidgets.QLineEdit()
        self.password_edit = QtWidgets.QLineEdit()
        self.password_edit.setEchoMode(QtWidgets.QLineEdit.Password)

        self.show_password_checkbox = QtWidgets.QCheckBox(tr("Pokaži geslo"))
        self.show_password_checkbox.toggled.connect(self.toggle_password_visibility)

        layout.addRow(tr("Uporabniško ime:"), self.username_edit)
        layout.addRow(tr("Geslo:"), self.password_edit)
        layout.addRow("", self.show_password_checkbox)

        url_esodstvo = "https://evlozisce.sodisce.si/esodstvo/prijava.html"
        layout.addWidget(QtWidgets.QLabel(f"<a href='{url_esodstvo}'>{tr('Registracija novega uporabnika e-sodstva')}</a>"))

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
    
    def accept(self):
        """Override accept to refresh web session with new credentials"""
        username, password = self.get_credentials()
        
        # Save to keyring
        keyring.set_password("SiKataster", "esodstvo_username", username)
        keyring.set_password("SiKataster", "esodstvo_password", password)
        
        # Close old session and start new one with updated credentials
        from .si_kataster_2web import EsodstvoWebClient
        EsodstvoWebClient.close_shared_session()
        
        QgsMessageLog.logMessage(
            tr("Poverilnice posodobljene, seja bo obnovljena"),
            MESSAGE_CATEGORY,
            Qgis.Info
        )
        
        super().accept()


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
        
        # Show loading indicator immediately
        if self.loading_label:
            self.loading_label.setText(self.tr("Čakam v vrsti..."))
            self.loading_label.setStyleSheet("color: blue;")
            self.loading_label.setVisible(True)
    
    def started(self):
        """Called when task actually starts running"""
        if self.loading_label:
            self.loading_label.setText(self.tr("Prenašam..."))
       

    def run(self):
        """Execute the PDF download task - runs in background thread"""
        if self.loading_label:
            self.loading_label.setText(self.tr("Prenašam PDF..."))
            
        try:
            # Import here to avoid circular import
            from .si_kataster_2web import EsodstvoWebClient
            
            start_time = time.time()
            
            self.setProgress(10)
            
            # Log session status
            status = EsodstvoWebClient.get_session_status()
            QgsMessageLog.logMessage(
                f"Pričetek prenosa PDF-a: {status}",
                MESSAGE_CATEGORY,
                Qgis.Info
            )
            
            QgsMessageLog.logMessage(
                f"Začetek prenosa PDF-a za KO: {self.ko_id}, Parcela: {self.parcela}",
                MESSAGE_CATEGORY,
                Qgis.Info
            )
            
            self.setProgress(20)
            
            # Initialize web client with session reuse enabled
            self.web_client = EsodstvoWebClient(
                self.username, 
                self.password,
                headless=True,
                reuse_session=True
            )
            
            self.setProgress(30)
            
            # Login (will skip if already logged in)
            if not self.web_client.login():
                self.exception = self.tr('Napaka pri prijavi v e-sodstvo. Preverite uporabniško ime in geslo.')
                # Clear the stored credentials if login failed
                keyring.delete_password("SiKataster", "esodstvo_username")
                keyring.delete_password("SiKataster", "esodstvo_password")
                EsodstvoWebClient.close_shared_session()
                return False
            
            QgsMessageLog.logMessage("Prijava v e-sodstvo uspešna", MESSAGE_CATEGORY, Qgis.Info)
            
            self.setProgress(50)
            
            # Select land registry role (will skip if already selected)
            if not self.web_client.select_land_registry_role():
                self.exception = self.tr('Napaka pri izbiri vloge dostopa do zemljiške knjige')
                return False
            
            #QgsMessageLog.logMessage("Role selection successful", MESSAGE_CATEGORY, Qgis.Info)
            
            self.setProgress(70)
            
            # Fill form with parcel data
            self.web_client.fill_parcel_form(self.ko_id, self.parcela)
            
            #QgsMessageLog.logMessage("Obrazec izpolnjen", MESSAGE_CATEGORY, Qgis.Info)
            
            self.setProgress(80)
            
            # Download PDF
            self.pdf_path = self.web_client.download_pdf()
            
            self.setProgress(90)
            
            if self.pdf_path and os.path.exists(self.pdf_path):
                elapsed = time.time() - start_time
                QgsMessageLog.logMessage(
                    f"PDF prenešen: {elapsed:.1f}s: {self.pdf_path}",
                    MESSAGE_CATEGORY,
                    Qgis.Success
                )
                self.setProgress(100)
                return True
            else:
                self.exception = self.tr('PDF ni bil prenešen. Preverite podatke o parceli.')
                return False
                
        except Exception as e:
            import traceback
            error_trace = traceback.format_exc()
            QgsMessageLog.logMessage(
                f"Error in run(): {error_trace}",
                MESSAGE_CATEGORY,
                Qgis.Critical
            )
            self.exception = str(e)
            return False
        
    def finished(self, result):
        """Handle task completion"""
        # Hide loading label
        if self.loading_label:
            self.loading_label.setVisible(False)
        
        if result:
            # Success - open PDF
            QgsMessageLog.logMessage(
                f"Uspešno prenešanje PDFa: {self.pdf_path}",
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
                    f"Napaka pri prenešanju PDFa: {str(e)}",
                    MESSAGE_CATEGORY,
                    Qgis.Warning
                )
        else:
            # Error - show message
            error_msg = self.exception if self.exception else self.tr('Težava pri prenašanju PDFa')
            
            QgsMessageLog.logMessage(
                f"Task failed: {error_msg}",
                MESSAGE_CATEGORY,
                Qgis.Critical
            )
            
            if self.loading_label:
                self.loading_label.setStyleSheet("color: red;")
                self.loading_label.setText(self.tr(f"Napaka: {error_msg}"))
                self.loading_label.setVisible(True)
