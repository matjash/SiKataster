from qgis.PyQt.QtWidgets import QWidget, QVBoxLayout,  QLabel,  QScrollArea


class AboutDialog(QWidget):
    def __init__(self):
        super().__init__()
        layout = QVBoxLayout()
        self.setWindowTitle(self.tr("O vtičniku"))

        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_widget = QWidget()
        self.scroll_layout = QVBoxLayout(self.scroll_widget)
        self.scroll_area.setWidget(self.scroll_widget)
        layout.addWidget(self.scroll_area)

        # Add text with hyperlinks
        text = """
        <b>SiKataster</b> je orodje za dostop do podatkov o parcelah v Katastru nepremičnin Geodetske uprave Republike Slovenije (GURS).<br><br>

        Vtičnik je zasnovan na način, da v metapodatke slojev, ki jih ustvari, zapiše informacije o viru in datumu prevzema podatkov.<br><br>

        Podatke in spletni servis zagotavlja GURS.<br><br>

        Podatki GURS-a imajo status informacij javnega značaja in so na voljo pod pogoji mednarodne licence Creative Commons 4.0. (priznanje avtorstva).<br><br>

        Uporabnik podatkov se obvezuje, da bo pri vsaki objavi podatkov ali izdelkov zagotovil navedbo vira podatkov, ki obsega »Geodetska uprava Republike Slovenije, vrsta podatka in čas, na katerega se podatki nanašajo oziroma datum stanja zbirke podatkov«.<br><br>

        Več informacij o podatkih Katastra nepremičnin: 
        <a href='https://www.e-prostor.gov.si/podrocja/parcele-in-stavbe/kataster-nepremicnin/'>https://www.e-prostor.gov.si/podrocja/parcele-in-stavbe/kataster-nepremicnin/</a><br><br>

        Več informacij o spletnem servisu: 
        <a href='https://www.e-prostor.gov.si/fileadmin/Storitve/Javni_dostop/Spletni_servisi/GU_DO2_SIST_TD_dostopDo_podatkov_servisi_in_baza.docx'>https://www.e-prostor.gov.si/fileadmin/Storitve/Javni_dostop/Spletni_servisi/GU_DO2_SIST_TD_dostopDo_podatkov_servisi_in_baza.docx</a><br><br>
        
        Vtičnik pripravil <a href=https://github.com/matjash>Matjaž Mori</a>, ZUM d.o.o.
        """

        self.label = QLabel(text)
        self.label.setWordWrap(True)
        self.label.setOpenExternalLinks(True) 
        self.label.setMinimumWidth(50) 
        self.scroll_layout.addWidget(self.label)

        self.setLayout(layout)
