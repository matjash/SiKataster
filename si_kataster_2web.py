"""
Web automation module for e-Sodstvo (Slovenian court system)
Handles login, navigation, and PDF download for land registry queries
"""

try:
    from selenium import webdriver
    from selenium.common.exceptions import TimeoutException, WebDriverException
    from selenium.webdriver.common.by import By
    from selenium.webdriver.common.keys import Keys
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.webdriver.firefox.options import Options as FirefoxOptions
    import time
    import tempfile
    import os
    from pathlib import Path
except ImportError as e:
    raise ImportError(
        "Missing dependency: selenium\n"
        "Install with: python -m pip install selenium"
    ) from e

from qgis.core import QgsMessageLog, Qgis
from qgis.PyQt.QtCore import QCoreApplication
import keyring


def tr(message):
    """Translate message"""
    return QCoreApplication.translate('SiKataster', message)


def get_default_download_folder():
    """Get the default downloads folder for the OS"""
    # Try to get custom folder from settings first
    custom_folder = keyring.get_password("SiKataster", "download_folder")
    if custom_folder and os.path.exists(custom_folder):
        return custom_folder
    
    # Otherwise use system default Downloads folder
    if os.name == 'nt':  # Windows
        import winreg
        # Try to get Downloads folder from registry
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, 
                              r'SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders') as key:
                downloads_folder = winreg.QueryValueEx(key, '{374DE290-123F-4565-9164-39C4925E467B}')[0]
                return downloads_folder
        except:
            pass
        # Fallback to user home Downloads
        return str(Path.home() / "Downloads")
    else:  # macOS and Linux
        downloads_folder = str(Path.home() / "Downloads")
        if os.path.exists(downloads_folder):
            return downloads_folder
        return str(Path.home())


def set_download_folder(folder_path):
    """Set custom download folder"""
    if folder_path and os.path.exists(folder_path):
        keyring.set_password("SiKataster", "download_folder", folder_path)
        return True
    return False


class EsodstvoWebClient:
    """Client for automating e-Sodstvo web interactions"""
    
    # Class-level driver instance (shared across instances)
    _shared_driver = None
    _shared_wait = None
    _is_logged_in = False
    _is_role_selected = False
    _current_username = None
    
    def __init__(self, username, password, headless=True, download_dir=None, reuse_session=True):
        """
        Initialize the web client
        
        Args:
            username: e-Sodstvo username
            password: e-Sodstvo password
            headless: Run browser in headless mode (default: True)
            download_dir: Directory for PDF downloads (default: system Downloads folder)
            reuse_session: Reuse existing browser session if available (default: True)
        """
        self.username = username
        self.password = password
        self.download_dir = download_dir or get_default_download_folder()
        self.reuse_session = reuse_session
        
        if reuse_session and EsodstvoWebClient._shared_driver:
            # Reuse existing driver
            QgsMessageLog.logMessage(
                tr("Ponovno uporabljam obstoječo sejo brskalnika"),
                "SiKataster",
                Qgis.Info
            )
            self.driver = EsodstvoWebClient._shared_driver
            self.wait = EsodstvoWebClient._shared_wait
        else:
            # Create new driver
            QgsMessageLog.logMessage(
                tr("Ustvarjam novo sejo brskalnika"),
                "SiKataster",
                Qgis.Info
            )
            self.driver = None
            self.wait = None
            self._setup_driver(headless)
            
            if reuse_session:
                # Store as shared driver
                EsodstvoWebClient._shared_driver = self.driver
                EsodstvoWebClient._shared_wait = self.wait
    
    def _setup_driver(self, headless):
        """Configure and initialize Firefox WebDriver"""
        options = FirefoxOptions()
        if headless:
            options.add_argument('--headless')
        
        # Configure download preferences
        options.set_preference("browser.download.folderList", 2)
        options.set_preference("browser.download.dir", self.download_dir)
        options.set_preference("browser.download.useDownloadDir", True)
        options.set_preference("browser.helperApps.neverAsk.saveToDisk", "application/pdf")
        options.set_preference("pdfjs.disabled", True)
        
        # Performance optimizations
        options.set_preference("permissions.default.image", 2)   
        options.set_preference("browser.cache.disk.enable", False)
        options.set_preference("browser.cache.memory.enable", False)
        options.page_load_strategy = "eager"

        self.driver = webdriver.Firefox(options=options)
        self.wait = WebDriverWait(self.driver, 15)
        
        # Reset session state when creating new driver
        EsodstvoWebClient._is_logged_in = False
        EsodstvoWebClient._is_role_selected = False
        EsodstvoWebClient._current_username = None
    
    def login(self):
        """
        Log in to e-Sodstvo
        
        Returns:
            bool: True if login successful
            
        Raises:
            TimeoutException: If login elements not found
            WebDriverException: If driver error occurs
        """
        # Check if already logged in with same user
        if (EsodstvoWebClient._is_logged_in and 
            EsodstvoWebClient._current_username == self.username and
            EsodstvoWebClient._shared_driver is not None):
            # Verify we're actually logged in by checking the page
            try:
                current_url = self.driver.current_url
                # Check if we're not at login error or login page
                if 'login_error' not in current_url and 'prijava.html' not in current_url:
                    if self._is_at_role_selection() or self._is_at_land_registry_form():
                        QgsMessageLog.logMessage(
                            tr("Že prijavljen, preskakujem prijavo"),
                            "SiKataster",
                            Qgis.Info
                        )
                        return True
            except:
                pass
        
        # If we're here, we need to login (fresh or re-login)
        try:
            QgsMessageLog.logMessage(
                tr("Navigacija na prijavno stran"),
                "SiKataster",
                Qgis.Info
            )
            
            self.driver.get("https://evlozisce.sodisce.si/esodstvo/prijava.html?type=navaden")
            
            # Wait for page to load
            time.sleep(1)
            
            # Fill login form
            self._fill_input(By.NAME, "j_username", self.username)
            self._fill_input(By.NAME, "j_password", self.password)
            self._click_element(By.XPATH, "//input[@value='Prijavi se']")
            
            # Wait for redirect after login
            time.sleep(2)
            
            # Check if login was successful
            current_url = self.driver.current_url
            if 'login_error=1' in current_url:
                QgsMessageLog.logMessage(
                    tr("Prijava neuspešna - neveljavne poverilnice"),
                    "SiKataster",
                    Qgis.Critical
                )
                # Clear the saved state
                EsodstvoWebClient._is_logged_in = False
                EsodstvoWebClient._current_username = None
                return False
            
            # Minimize window if not headless
            try:
                self.driver.minimize_window()
            except:
                pass
            
            # Mark as logged in
            EsodstvoWebClient._is_logged_in = True
            EsodstvoWebClient._current_username = self.username
            
            QgsMessageLog.logMessage(
                tr(f"Prijava uspešna, na URL: {current_url}"),
                "SiKataster",
                Qgis.Info
            )
            
            return True
        except Exception as e:
            QgsMessageLog.logMessage(
                tr(f"Izjema pri prijavi: {str(e)}"),
                "SiKataster",
                Qgis.Critical
            )
            raise Exception(tr(f"Prijava neuspešna: {str(e)}"))
    
    def select_land_registry_role(self):
        """
        Navigate through role selection to land registry (zemljiška knjiga)
        
        Returns:
            bool: True if navigation successful
        """
        # Check if role already selected
        if (EsodstvoWebClient._is_role_selected and 
            EsodstvoWebClient._shared_driver is not None):
            try:
                if self._is_at_land_registry_form():
                    QgsMessageLog.logMessage(
                        tr("Že na obrazcu zemljiške knjige, preskakujem izbiro vloge"),
                        "SiKataster",
                        Qgis.Info
                    )
                    return True
            except:
                pass
        
        # Check if we're already at the land registry form
        if self._is_at_land_registry_form():
            EsodstvoWebClient._is_role_selected = True
            QgsMessageLog.logMessage(
                tr("Na obrazcu zemljiške knjige"),
                "SiKataster",
                Qgis.Info
            )
            return True
        
        # Wait a bit for page to stabilize after login
        time.sleep(1)
        
        # Check if we're at role selection page
        if not self._is_at_role_selection():
            # Not at role selection, might already be past it
            if self._is_at_land_registry_form():
                EsodstvoWebClient._is_role_selected = True
                return True
            
            QgsMessageLog.logMessage(
                tr(f"Nisem na strani za izbiro vloge. Trenutni URL: {self.driver.current_url}"),
                "SiKataster",
                Qgis.Warning
            )
            return False
        
        max_attempts = 5
        
        for attempt in range(max_attempts):
            try:
                # Find and click role selection links
                links = self.driver.find_elements(By.CSS_SELECTOR, "a.multiple_role_switch")
                
                if not links:
                    # No role selection links - might already be at the form
                    if self._is_at_land_registry_form():
                        EsodstvoWebClient._is_role_selected = True
                        return True
                
                for i in range(len(links)):
                    try:
                        # Re-fetch link to avoid stale element
                        fresh_link = self.driver.find_elements(By.CSS_SELECTOR, "a.multiple_role_switch")[i]
                        
                        if fresh_link.is_displayed():
                            self.driver.execute_script("arguments[0].scrollIntoView(true);", fresh_link)
                            time.sleep(0.5)
                            self.driver.execute_script("arguments[0].click();", fresh_link)
                            
                            # Wait for and click land registry option
                            self.wait.until(EC.presence_of_element_located((
                                By.XPATH, "//p[contains(text(), 'eZK-opravila – zemljiška knjiga')]"
                            )))
                            time.sleep(0.3)
                            self._click_element(By.XPATH, "//p[contains(text(), 'eZK-opravila – zemljiška knjiga')]")
                            
                            # Wait for form to load
                            time.sleep(2)
                            
                            # Verify we're at the form
                            if self._is_at_land_registry_form():
                                EsodstvoWebClient._is_role_selected = True
                                QgsMessageLog.logMessage(
                                    tr("Vloga uspešno izbrana"),
                                    "SiKataster",
                                    Qgis.Info
                                )
                                return True
                    except Exception as e:
                        QgsMessageLog.logMessage(
                            tr(f"Poskus {i} neuspešen: {str(e)}"),
                            "SiKataster",
                            Qgis.Warning
                        )
                        continue
                
                time.sleep(0.5)
            except Exception as e:
                QgsMessageLog.logMessage(
                    tr(f"Poskus izbire vloge {attempt} neuspešen: {str(e)}"),
                    "SiKataster",
                    Qgis.Warning
                )
                continue
        
        return False
    
    def _is_at_role_selection(self):
        """Check if currently at role selection page"""
        try:
            links = self.driver.find_elements(By.CSS_SELECTOR, "a.multiple_role_switch")
            return len(links) > 0
        except:
            return False
    
    def _is_at_land_registry_form(self):
        """Check if currently at land registry form page"""
        try:
            # Check for the cadastral municipality input field
            elem = self.driver.find_elements(By.ID, "idZnakNep.katastrskaObcina.idsrcsifrant")
            if elem:
                return True
            # Alternative check - look for accordion header
            h2 = self.driver.find_elements(By.CSS_SELECTOR, "h2.first.ui-accordion-header")
            return len(h2) > 0
        except:
            return False
    
    def fill_parcel_form(self, ko_id, parcela):
        """
        Fill in the land parcel search form
        
        Args:
            ko_id: Cadastral municipality ID (katastrska občina)
            parcela: Parcel number (parcelna številka)
        """
        # Expand accordion if needed
        h2 = self.wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "h2.first.ui-accordion-header")))
        
        if h2.get_attribute("aria-expanded") != "true":
            self.driver.execute_script("arguments[0].scrollIntoView(true);", h2)
            h2.click()
            time.sleep(0.25)
        
        # Fill form fields
        self._fill_input(By.ID, "idZnakNep.katastrskaObcina.idsrcsifrant", ko_id)
        self.driver.find_element(By.ID, "idZnakNep.katastrskaObcina.idsrcsifrant").send_keys(Keys.TAB)
        
        self._fill_input(By.ID, "idZnakNep.parcelnaStevilka", parcela)
        self.driver.find_element(By.ID, "idZnakNep.parcelnaStevilka").send_keys(Keys.TAB)

    def download_pdf(self):
        """
        Click PDF download button and handle the download
        
        Returns:
            str: Path to downloaded PDF file, or None if download failed
        """
        before = set(os.listdir(self.download_dir))

        pdf_button = self.wait.until(EC.element_to_be_clickable((By.ID, "btn_pdf")))
        self.driver.execute_script("arguments[0].scrollIntoView(true);", pdf_button)
        pdf_button.click()

        # Wait for a new PDF file to appear
        for _ in range(8):  # ~2 seconds
            time.sleep(0.25)
            after = set(os.listdir(self.download_dir))
            new = [f for f in (after - before) if f.lower().endswith(".pdf")]
            if new:
                return os.path.join(self.download_dir, new[0])

        return None

    def _get_latest_pdf(self):
        """Get the most recently downloaded PDF file"""
        files = [os.path.join(self.download_dir, f) for f in os.listdir(self.download_dir) 
                 if f.endswith('.pdf')]
        if not files:
            return None
        return max(files, key=os.path.getctime)
    
    def _fill_input(self, by, selector, text):
        """Helper: Fill input field"""
        elem = self.wait.until(EC.visibility_of_element_located((by, selector)))
        elem.clear()
        elem.send_keys(text)
    
    def _click_element(self, by, selector):
        """Helper: Click element with scroll into view"""
        elem = self.wait.until(EC.element_to_be_clickable((by, selector)))
        self.driver.execute_script("arguments[0].scrollIntoView(true);", elem)
        time.sleep(0.25)
        elem.click()
    
    def close(self, force=False):
        """
        Close the browser and clean up
        
        Args:
            force: Force close even if using shared session (default: False)
        """
        if self.driver and (force or not self.reuse_session):
            try:
                self.driver.quit()
                if self.driver == EsodstvoWebClient._shared_driver:
                    EsodstvoWebClient._shared_driver = None
                    EsodstvoWebClient._shared_wait = None
                    EsodstvoWebClient._is_logged_in = False
                    EsodstvoWebClient._is_role_selected = False
                    EsodstvoWebClient._current_username = None
            except:
                pass
    
    @classmethod
    def close_shared_session(cls):
        """Close the shared browser session"""
        if cls._shared_driver:
            try:
                cls._shared_driver.quit()
            except:
                pass
            finally:
                cls._shared_driver = None
                cls._shared_wait = None
                cls._is_logged_in = False
                cls._is_role_selected = False
                cls._current_username = None
    
    @classmethod
    def get_session_status(cls):
        """Get current session status for debugging"""
        status = {
            'has_driver': cls._shared_driver is not None,
            'is_logged_in': cls._is_logged_in,
            'is_role_selected': cls._is_role_selected,
            'username': cls._current_username
        }
        
        # Add actual page state if driver exists
        if cls._shared_driver:
            try:
                # Check actual page state
                client = EsodstvoWebClient.__new__(EsodstvoWebClient)
                client.driver = cls._shared_driver
                client.wait = cls._shared_wait
                
                status['actual_at_role_selection'] = client._is_at_role_selection()
                status['actual_at_form'] = client._is_at_land_registry_form()
                status['current_url'] = cls._shared_driver.current_url
            except:
                pass
        
        return status
    
    def __enter__(self):
        """Context manager entry"""
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit"""
        self.close()


def verify_credentials(username, password):
    """
    Verify e-Sodstvo credentials by attempting login
    
    Args:
        username: e-Sodstvo username
        password: e-Sodstvo password
        
    Returns:
        bool: True if credentials are valid
    """
    try:
        # Don't reuse session for credential verification
        client = EsodstvoWebClient(username, password, headless=True, reuse_session=False)
        result = client.login()
        client.close(force=True)
        return result
    except Exception:
        return False


def initialize_session(username, password, headless=True):
    """
    Initialize and return a persistent session for reuse
    
    Args:
        username: e-Sodstvo username
        password: e-Sodstvo password
        headless: Run browser in headless mode (default: True)
        
    Returns:
        EsodstvoWebClient: Initialized client ready for reuse
    """
    client = EsodstvoWebClient(username, password, headless=headless, reuse_session=True)
    if not client.login():
        return None
    if not client.select_land_registry_role():
        return None
    return client
