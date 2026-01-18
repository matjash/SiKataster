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
except ImportError as e:
    raise ImportError(
        "Missing dependency: selenium\n"
        "Install with: python -m pip install selenium"
    ) from e


class EsodstvoWebClient:
    """Client for automating e-Sodstvo web interactions"""
    
    def __init__(self, username, password, headless=True, download_dir=None):
        """
        Initialize the web client
        
        Args:
            username: e-Sodstvo username
            password: e-Sodstvo password
            headless: Run browser in headless mode (default: True)
            download_dir: Directory for PDF downloads (default: temp directory)
        """
        self.username = username
        self.password = password
        self.download_dir = download_dir or tempfile.gettempdir()
        self.driver = None
        self.wait = None
        self._setup_driver(headless)
    
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
        self.wait = WebDriverWait(self.driver, 3)
    
    def login(self):
        """
        Log in to e-Sodstvo
        
        Returns:
            bool: True if login successful
            
        Raises:
            TimeoutException: If login elements not found
            WebDriverException: If driver error occurs
        """
        try:
            self.driver.get("https://evlozisce.sodisce.si/esodstvo/prijava.html?type=navaden")
            
            # Fill login form
            self._fill_input(By.NAME, "j_username", self.username)
            self._fill_input(By.NAME, "j_password", self.password)
            self._click_element(By.XPATH, "//input[@value='Prijavi se']")
            
            # Minimize window if not headless
            try:
                self.driver.minimize_window()
            except:
                pass
            
            return True
        except Exception as e:
            raise Exception(f"Login failed: {str(e)}")
    
    def select_land_registry_role(self):
        """
        Navigate through role selection to land registry (zemljiška knjiga)
        
        Returns:
            bool: True if navigation successful
        """
        max_attempts = 5
        
        for attempt in range(max_attempts):
            try:
                # Find and click role selection links
                links = self.driver.find_elements(By.CSS_SELECTOR, "a.multiple_role_switch")
                
                for i in range(len(links)):
                    try:
                        # Re-fetch link to avoid stale element
                        fresh_link = self.driver.find_elements(By.CSS_SELECTOR, "a.multiple_role_switch")[i]
                        
                        if fresh_link.is_displayed():
                            self.driver.execute_script("arguments[0].scrollIntoView(true);", fresh_link)
                            time.sleep(0.25)
                            self.driver.execute_script("arguments[0].click();", fresh_link)
                            
                            # Wait for and click land registry option
                            self.wait.until(EC.presence_of_element_located((
                                By.XPATH, "//p[contains(text(), 'eZK-opravila – zemljiška knjiga')]"
                            )))
                            self._click_element(By.XPATH, "//p[contains(text(), 'eZK-opravila – zemljiška knjiga')]")
                            return True
                    except:
                        continue
                
                time.sleep(0.5)
            except:
                continue
        
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
        before = set(os.listdir(self.download_dir))

        pdf_button = self.wait.until(EC.element_to_be_clickable((By.ID, "btn_pdf")))
        self.driver.execute_script("arguments[0].scrollIntoView(true);", pdf_button)
    
        pdf_button.click()

        # Wait for a new PDF file to appear
        for _ in range(8):  # ~10s
            time.sleep(0.25)
            after = set(os.listdir(self.download_dir))
            new = [f for f in (after - before) if f.lower().endswith(".pdf")]
            if new:
                return os.path.join(self.download_dir, new[0])

        # Optional: check if page shows an error message
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
    
    def close(self):
        """Close the browser and clean up"""
        if self.driver:
            try:
                self.driver.quit()
            except:
                pass
    
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
        with EsodstvoWebClient(username, password, headless=True) as client:
            client.login()
            return True
    except Exception:
        return False
