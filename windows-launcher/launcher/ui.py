"""Finestra principale del software.

Tre zone, come in Semaphore: a sinistra il collegamento al controller, in alto
i playbook con il loro inventario e le opzioni, in basso la console.

Il lavoro pesante (SSH e lettura dell'output) gira su un thread di paramiko:
i widget Qt non si toccano da li', si passa dai Signal, che Qt consegna al
thread dell'interfaccia.
"""

from __future__ import annotations

import datetime
import os

from PySide6.QtCore import Qt, QObject, Signal
from PySide6.QtGui import QAction, QTextCursor
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QFileDialog, QFormLayout, QGroupBox,
    QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMainWindow, QMessageBox,
    QPlainTextEdit, QPushButton, QSpinBox, QSplitter, QTableWidget, QTableWidgetItem,
    QTextEdit, QVBoxLayout, QWidget,
)

from . import config as configurazione_modulo
from .ansi import in_html, senza_colori
from .commands import Opzioni, Playbook
from .runner import (
    ErroreConnessione, Esecuzione, apri_connessione,
    carica_chiave_sul_controller, prepara_comando, prova_connessione,
    rimuovi_chiave_dal_controller,
)
from .theme import ACCENTO, FOGLIO_DI_STILE, ROSSO, TESTO_TENUE, VERDE


class Ponte(QObject):
    """Porta gli eventi dal thread SSH al thread dell'interfaccia."""

    riga = Signal(str)
    fine = Signal(int, bool)


class Finestra(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Ansible Launcher")
        self.resize(1400, 900)

        self.configurazione = configurazione_modulo.carica()
        self.client = None
        self.esecuzione = None
        self.chiave_temporanea = ""
        self.righe_grezze: list[str] = []

        self.ponte = Ponte()
        self.ponte.riga.connect(self._scrivi_riga)
        self.ponte.fine.connect(self._al_termine)

        self._costruisci()
        self._carica_nei_campi()
        self._aggiorna_anteprima()

    # ------------------------------------------------------------------ UI --

    def _costruisci(self) -> None:
        centrale = QWidget()
        self.setCentralWidget(centrale)
        principale = QHBoxLayout(centrale)
        principale.setContentsMargins(12, 12, 12, 12)
        principale.setSpacing(12)

        principale.addWidget(self._pannello_connessione(), 0)

        destra = QSplitter(Qt.Vertical)
        destra.addWidget(self._pannello_esecuzione())
        destra.addWidget(self._pannello_console())
        destra.setSizes([520, 420])
        principale.addWidget(destra, 1)

        self._menu()
        self.statusBar().showMessage("Non collegato")

    def _menu(self) -> None:
        file_menu = self.menuBar().addMenu("File")

        salva = QAction("Salva impostazioni", self)
        salva.setShortcut("Ctrl+S")
        salva.triggered.connect(self._salva_impostazioni)
        file_menu.addAction(salva)

        esporta = QAction("Esporta log della console...", self)
        esporta.triggered.connect(self._esporta_log)
        file_menu.addAction(esporta)

        file_menu.addSeparator()
        esci = QAction("Esci", self)
        esci.triggered.connect(self.close)
        file_menu.addAction(esci)

    def _pannello_connessione(self) -> QWidget:
        gruppo = QGroupBox("Controller Ansible")
        gruppo.setFixedWidth(380)
        modulo = QFormLayout(gruppo)
        modulo.setSpacing(8)

        self.campo_host = QLineEdit()
        self.campo_host.setPlaceholderText("10.205.166.x oppure nome host")
        modulo.addRow("Indirizzo", self.campo_host)

        self.campo_porta = QSpinBox()
        self.campo_porta.setRange(1, 65535)
        self.campo_porta.setValue(22)
        modulo.addRow("Porta", self.campo_porta)

        self.campo_utente = QLineEdit()
        modulo.addRow("Utente", self.campo_utente)

        self.campo_metodo = QComboBox()
        self.campo_metodo.addItems(["chiave", "password"])
        self.campo_metodo.currentTextChanged.connect(self._cambia_metodo)
        modulo.addRow("Autenticazione", self.campo_metodo)

        riga_chiave = QHBoxLayout()
        self.campo_chiave = QLineEdit()
        self.campo_chiave.setPlaceholderText(r"C:\Users\...\DeliveryAutomation.pem")
        sfoglia = QPushButton("Sfoglia")
        sfoglia.setFixedWidth(70)
        sfoglia.clicked.connect(lambda: self._scegli_file(self.campo_chiave))
        riga_chiave.addWidget(self.campo_chiave)
        riga_chiave.addWidget(sfoglia)
        contenitore_chiave = QWidget()
        contenitore_chiave.setLayout(riga_chiave)
        riga_chiave.setContentsMargins(0, 0, 0, 0)
        modulo.addRow("Chiave .pem", contenitore_chiave)

        self.campo_passphrase = QLineEdit()
        self.campo_passphrase.setEchoMode(QLineEdit.Password)
        self.campo_passphrase.setPlaceholderText("solo se la chiave e' protetta")
        modulo.addRow("Passphrase", self.campo_passphrase)

        self.campo_password = QLineEdit()
        self.campo_password.setEchoMode(QLineEdit.Password)
        modulo.addRow("Password", self.campo_password)

        self.campo_cartella = QLineEdit()
        self.campo_cartella.setPlaceholderText("/root/kikkoBetterThanAI/fullstack")
        self.campo_cartella.textChanged.connect(self._aggiorna_anteprima)
        modulo.addRow("Cartella repo", self.campo_cartella)

        self.campo_venv = QLineEdit()
        self.campo_venv.setPlaceholderText("vuoto = nessun virtualenv")
        self.campo_venv.textChanged.connect(self._aggiorna_anteprima)
        modulo.addRow("Virtualenv", self.campo_venv)

        # --- chiave per i nodi gestiti ---
        modulo.addRow(QLabel(""))
        etichetta = QLabel("Chiave per i NODI gestiti (--private-key)")
        etichetta.setStyleSheet(f"color: {ACCENTO}; font-weight: 600;")
        modulo.addRow(etichetta)

        spiegazione = QLabel(
            "Se il file sta su questo PC viene caricato sul controller a ogni "
            "esecuzione, con permessi 0600, e rimosso alla fine."
        )
        spiegazione.setWordWrap(True)
        spiegazione.setStyleSheet(f"color: {TESTO_TENUE};")
        modulo.addRow(spiegazione)

        riga_nodi = QHBoxLayout()
        riga_nodi.setContentsMargins(0, 0, 0, 0)
        self.campo_chiave_nodi = QLineEdit()
        self.campo_chiave_nodi.setPlaceholderText("percorso su questo PC, oppure vuoto")
        self.campo_chiave_nodi.textChanged.connect(self._aggiorna_anteprima)
        sfoglia_nodi = QPushButton("Sfoglia")
        sfoglia_nodi.setFixedWidth(70)
        sfoglia_nodi.clicked.connect(lambda: self._scegli_file(self.campo_chiave_nodi))
        riga_nodi.addWidget(self.campo_chiave_nodi)
        riga_nodi.addWidget(sfoglia_nodi)
        contenitore_nodi = QWidget()
        contenitore_nodi.setLayout(riga_nodi)
        modulo.addRow("Su questo PC", contenitore_nodi)

        self.campo_chiave_nodi_remota = QLineEdit()
        self.campo_chiave_nodi_remota.setPlaceholderText("/root/.ssh/chiave.pem")
        self.campo_chiave_nodi_remota.textChanged.connect(self._aggiorna_anteprima)
        modulo.addRow("Gia' sul controller", self.campo_chiave_nodi_remota)

        modulo.addRow(QLabel(""))
        pulsanti = QHBoxLayout()
        pulsanti.setContentsMargins(0, 0, 0, 0)
        prova = QPushButton("Prova collegamento")
        prova.clicked.connect(self._prova_collegamento)
        salva = QPushButton("Salva")
        salva.clicked.connect(self._salva_impostazioni)
        pulsanti.addWidget(prova)
        pulsanti.addWidget(salva)
        contenitore_pulsanti = QWidget()
        contenitore_pulsanti.setLayout(pulsanti)
        modulo.addRow(contenitore_pulsanti)

        return gruppo

    def _pannello_esecuzione(self) -> QWidget:
        contenitore = QWidget()
        disposizione = QVBoxLayout(contenitore)
        disposizione.setContentsMargins(0, 0, 0, 0)
        disposizione.setSpacing(10)

        # --- elenco playbook ---
        gruppo_pb = QGroupBox("Playbook e inventario")
        v = QVBoxLayout(gruppo_pb)

        self.tabella = QTableWidget(0, 4)
        self.tabella.setHorizontalHeaderLabels(
            ["Nome", "Playbook", "Inventario", "Descrizione"]
        )
        # Senza un'altezza minima il gruppo si schiaccia e delle righe si vede
        # solo l'intestazione.
        self.tabella.setMinimumHeight(190)
        self.tabella.verticalHeader().setVisible(False)
        self.tabella.setSelectionBehavior(QTableWidget.SelectRows)
        self.tabella.setSelectionMode(QTableWidget.SingleSelection)
        intestazione = self.tabella.horizontalHeader()
        intestazione.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        intestazione.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        intestazione.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        intestazione.setSectionResizeMode(3, QHeaderView.Stretch)
        self.tabella.itemSelectionChanged.connect(self._aggiorna_anteprima)
        self.tabella.itemChanged.connect(self._aggiorna_anteprima)
        v.addWidget(self.tabella)

        azioni = QHBoxLayout()
        aggiungi = QPushButton("Aggiungi")
        aggiungi.clicked.connect(self._aggiungi_playbook)
        rimuovi = QPushButton("Rimuovi")
        rimuovi.clicked.connect(self._rimuovi_playbook)
        ripristina = QPushButton("Ripristina elenco")
        ripristina.clicked.connect(self._ripristina_playbook)
        azioni.addWidget(aggiungi)
        azioni.addWidget(rimuovi)
        azioni.addWidget(ripristina)
        azioni.addStretch()
        v.addLayout(azioni)
        disposizione.addWidget(gruppo_pb)

        # --- opzioni ---
        gruppo_opz = QGroupBox("Opzioni di esecuzione")
        griglia = QHBoxLayout(gruppo_opz)

        colonna1 = QFormLayout()
        self.opz_check = QCheckBox("--check (simula, non modifica)")
        self.opz_check.stateChanged.connect(self._aggiorna_anteprima)
        self.opz_diff = QCheckBox("--diff (mostra le differenze)")
        self.opz_diff.stateChanged.connect(self._aggiorna_anteprima)
        self.opz_become = QCheckBox("--become")
        self.opz_become.stateChanged.connect(self._aggiorna_anteprima)
        colonna1.addRow(self.opz_check)
        colonna1.addRow(self.opz_diff)
        colonna1.addRow(self.opz_become)

        colonna2 = QFormLayout()
        self.opz_limit = QLineEdit()
        self.opz_limit.setPlaceholderText("db_servers")
        self.opz_limit.textChanged.connect(self._aggiorna_anteprima)
        self.opz_tags = QLineEdit()
        self.opz_tags.textChanged.connect(self._aggiorna_anteprima)
        self.opz_skip = QLineEdit()
        self.opz_skip.textChanged.connect(self._aggiorna_anteprima)
        colonna2.addRow("--limit", self.opz_limit)
        colonna2.addRow("--tags", self.opz_tags)
        colonna2.addRow("--skip-tags", self.opz_skip)

        colonna3 = QFormLayout()
        self.opz_extra = QLineEdit()
        self.opz_extra.setPlaceholderText("chiave=valore, separate da spazio")
        self.opz_extra.textChanged.connect(self._aggiorna_anteprima)
        self.opz_verbosita = QSpinBox()
        self.opz_verbosita.setRange(0, 4)
        self.opz_verbosita.valueChanged.connect(self._aggiorna_anteprima)
        self.opz_forks = QSpinBox()
        self.opz_forks.setRange(0, 100)
        self.opz_forks.setSpecialValueText("default")
        self.opz_forks.valueChanged.connect(self._aggiorna_anteprima)
        colonna3.addRow("-e extra vars", self.opz_extra)
        colonna3.addRow("verbosita' (-v)", self.opz_verbosita)
        colonna3.addRow("--forks", self.opz_forks)

        for colonna in (colonna1, colonna2, colonna3):
            involucro = QWidget()
            involucro.setLayout(colonna)
            griglia.addWidget(involucro)
        disposizione.addWidget(gruppo_opz)

        # --- anteprima ed esecuzione ---
        gruppo_run = QGroupBox("Comando che verra' eseguito sul controller")
        v2 = QVBoxLayout(gruppo_run)

        # QPlainTextEdit e non QLineEdit: il comando e' lungo e va letto tutto,
        # non scorrendolo a mano fino in fondo.
        self.anteprima = QPlainTextEdit()
        self.anteprima.setObjectName("anteprima")
        self.anteprima.setReadOnly(True)
        self.anteprima.setFixedHeight(64)
        self.anteprima.setLineWrapMode(QPlainTextEdit.WidgetWidth)
        v2.addWidget(self.anteprima)

        riga = QHBoxLayout()
        self.pulsante_esegui = QPushButton("ESEGUI")
        self.pulsante_esegui.setObjectName("esegui")
        self.pulsante_esegui.clicked.connect(lambda: self._esegui(simulazione=False))

        self.pulsante_simula = QPushButton("Simula (--check --diff)")
        self.pulsante_simula.clicked.connect(lambda: self._esegui(simulazione=True))

        self.pulsante_interrompi = QPushButton("INTERROMPI")
        self.pulsante_interrompi.setObjectName("interrompi")
        self.pulsante_interrompi.setEnabled(False)
        self.pulsante_interrompi.clicked.connect(self._interrompi)

        copia = QPushButton("Copia comando")
        copia.clicked.connect(self._copia_comando)

        self.etichetta_stato = QLabel("pronto")
        self.etichetta_stato.setObjectName("stato")

        riga.addWidget(self.pulsante_esegui)
        riga.addWidget(self.pulsante_simula)
        riga.addWidget(self.pulsante_interrompi)
        riga.addWidget(copia)
        riga.addStretch()
        riga.addWidget(self.etichetta_stato)
        v2.addLayout(riga)
        disposizione.addWidget(gruppo_run)

        return contenitore

    def _pannello_console(self) -> QWidget:
        gruppo = QGroupBox("Output")
        v = QVBoxLayout(gruppo)

        self.console = QTextEdit()
        self.console.setObjectName("console")
        self.console.setReadOnly(True)
        v.addWidget(self.console)

        riga = QHBoxLayout()
        pulisci = QPushButton("Pulisci")
        pulisci.clicked.connect(self._pulisci_console)
        esporta = QPushButton("Esporta log")
        esporta.clicked.connect(self._esporta_log)
        riga.addWidget(pulisci)
        riga.addWidget(esporta)
        riga.addStretch()
        v.addLayout(riga)

        return gruppo

    # ------------------------------------------------------- configurazione --

    def _carica_nei_campi(self) -> None:
        c = self.configurazione.connessione
        self.campo_host.setText(c.host)
        self.campo_porta.setValue(c.porta)
        self.campo_utente.setText(c.utente)
        self.campo_metodo.setCurrentText(c.metodo)
        self.campo_chiave.setText(c.percorso_chiave)
        self.campo_cartella.setText(c.cartella_ansible)
        self.campo_venv.setText(c.venv)
        self.campo_chiave_nodi.setText(self.configurazione.chiave_nodi_locale)
        self.campo_chiave_nodi_remota.setText(self.configurazione.chiave_nodi_remota)
        self._cambia_metodo(c.metodo)
        self._riempi_tabella(self.configurazione.playbook)

    def _riempi_tabella(self, elenco: list[Playbook]) -> None:
        self.tabella.blockSignals(True)
        self.tabella.setRowCount(0)
        for pb in elenco:
            riga = self.tabella.rowCount()
            self.tabella.insertRow(riga)
            for colonna, valore in enumerate(
                (pb.nome, pb.percorso, pb.inventario, pb.descrizione)
            ):
                self.tabella.setItem(riga, colonna, QTableWidgetItem(valore))
        self.tabella.blockSignals(False)
        if self.tabella.rowCount():
            self.tabella.selectRow(0)

    def _leggi_tabella(self) -> list[Playbook]:
        elenco = []
        for riga in range(self.tabella.rowCount()):
            def testo(colonna: int) -> str:
                elemento = self.tabella.item(riga, colonna)
                return elemento.text().strip() if elemento else ""

            if testo(1):
                elenco.append(
                    Playbook(
                        nome=testo(0) or testo(1),
                        percorso=testo(1),
                        inventario=testo(2) or "inventory.ini",
                        descrizione=testo(3),
                    )
                )
        return elenco

    def _playbook_selezionato(self) -> Playbook | None:
        riga = self.tabella.currentRow()
        elenco = self._leggi_tabella()
        if 0 <= riga < len(elenco):
            return elenco[riga]
        return elenco[0] if elenco else None

    def _opzioni(self, simulazione: bool = False) -> Opzioni:
        chiave = self.campo_chiave_nodi_remota.text().strip()
        if not chiave and self.chiave_temporanea:
            chiave = self.chiave_temporanea
        elif not chiave and self.campo_chiave_nodi.text().strip():
            # In anteprima si mostra dove finira', prima ancora di caricarla.
            chiave = "/tmp/.launcher-<caricata-all-avvio>"

        return Opzioni(
            check=self.opz_check.isChecked() or simulazione,
            diff=self.opz_diff.isChecked() or simulazione,
            diventa_root=self.opz_become.isChecked(),
            limit=self.opz_limit.text(),
            tags=self.opz_tags.text(),
            skip_tags=self.opz_skip.text(),
            extra_vars=self.opz_extra.text().split(),
            verbosita=self.opz_verbosita.value(),
            forks=self.opz_forks.value(),
            chiave_nodi=chiave,
        )

    def _connessione_dai_campi(self):
        c = self.configurazione.connessione
        c.host = self.campo_host.text().strip()
        c.porta = self.campo_porta.value()
        c.utente = self.campo_utente.text().strip()
        c.metodo = self.campo_metodo.currentText()
        c.percorso_chiave = self.campo_chiave.text().strip()
        c.cartella_ansible = self.campo_cartella.text().strip() or "."
        c.venv = self.campo_venv.text().strip()
        return c

    def _salva_impostazioni(self) -> None:
        self._connessione_dai_campi()
        self.configurazione.playbook = self._leggi_tabella()
        self.configurazione.chiave_nodi_locale = self.campo_chiave_nodi.text().strip()
        self.configurazione.chiave_nodi_remota = self.campo_chiave_nodi_remota.text().strip()
        percorso = configurazione_modulo.salva(self.configurazione)
        self.statusBar().showMessage(f"Impostazioni salvate in {percorso}", 6000)

    # --------------------------------------------------------------- azioni --

    def _cambia_metodo(self, metodo: str) -> None:
        con_chiave = metodo == "chiave"
        self.campo_chiave.setEnabled(con_chiave)
        self.campo_passphrase.setEnabled(con_chiave)
        self.campo_password.setEnabled(not con_chiave)

    def _scegli_file(self, campo: QLineEdit) -> None:
        percorso, _ = QFileDialog.getOpenFileName(
            self, "Scegli la chiave privata", "",
            "Chiavi (*.pem *.key id_*);;Tutti i file (*.*)",
        )
        if percorso:
            campo.setText(os.path.normpath(percorso))

    def _aggiungi_playbook(self) -> None:
        riga = self.tabella.rowCount()
        self.tabella.insertRow(riga)
        for colonna, valore in enumerate(
            ("Nuovo", "playbooks/nome.yml", "inventory.ini", "")
        ):
            self.tabella.setItem(riga, colonna, QTableWidgetItem(valore))
        self.tabella.selectRow(riga)

    def _rimuovi_playbook(self) -> None:
        riga = self.tabella.currentRow()
        if riga >= 0:
            self.tabella.removeRow(riga)
            self._aggiorna_anteprima()

    def _ripristina_playbook(self) -> None:
        from .commands import PLAYBOOK_PREDEFINITI
        self._riempi_tabella(list(PLAYBOOK_PREDEFINITI))

    def _aggiorna_anteprima(self) -> None:
        playbook = self._playbook_selezionato()
        if not playbook:
            self.anteprima.setPlainText("")
            return
        comando = prepara_comando(playbook, self._opzioni(), self._connessione_dai_campi())
        self.anteprima.setPlainText(comando)
        self.anteprima.setToolTip(comando)

    def _copia_comando(self) -> None:
        QApplication.clipboard().setText(self.anteprima.toPlainText())
        self.statusBar().showMessage("Comando copiato negli appunti", 4000)

    def _prova_collegamento(self) -> None:
        try:
            riepilogo = prova_connessione(
                self._connessione_dai_campi(),
                self.campo_passphrase.text(),
                self.campo_password.text(),
            )
        except ErroreConnessione as errore:
            QMessageBox.critical(self, "Collegamento fallito", str(errore))
            self.statusBar().showMessage("Collegamento fallito")
            return
        QMessageBox.information(self, "Collegamento riuscito", riepilogo)
        self.statusBar().showMessage("Controller raggiungibile")

    # ------------------------------------------------------------ esecuzione --

    def _esegui(self, simulazione: bool) -> None:
        if self.esecuzione is not None:
            QMessageBox.warning(
                self, "Gia' in esecuzione",
                "C'e' gia' un playbook in corso. Attendi la fine oppure premi "
                "INTERROMPI.",
            )
            return

        playbook = self._playbook_selezionato()
        if not playbook:
            QMessageBox.warning(self, "Nessun playbook", "Seleziona un playbook.")
            return

        connessione = self._connessione_dai_campi()

        try:
            self.client = apri_connessione(
                connessione, self.campo_passphrase.text(), self.campo_password.text()
            )
        except ErroreConnessione as errore:
            QMessageBox.critical(self, "Collegamento fallito", str(errore))
            return

        # La chiave dei nodi, se sta su questo PC, va portata sul controller.
        self.chiave_temporanea = ""
        locale = self.campo_chiave_nodi.text().strip()
        if locale and not self.campo_chiave_nodi_remota.text().strip():
            try:
                self.chiave_temporanea = carica_chiave_sul_controller(self.client, locale)
                self._scrivi_annotazione(
                    f"chiave dei nodi caricata sul controller in {self.chiave_temporanea}"
                )
            except (ErroreConnessione, OSError) as errore:
                QMessageBox.critical(self, "Chiave non caricata", str(errore))
                self._chiudi_client()
                return

        opzioni = self._opzioni(simulazione)
        comando = prepara_comando(playbook, opzioni, connessione)

        self._pulisci_console()
        self._scrivi_annotazione(
            f"{datetime.datetime.now():%H:%M:%S}  {playbook.nome}"
            f"{'  [SIMULAZIONE]' if simulazione else ''}"
        )
        self._scrivi_annotazione(f"$ {comando}")
        self._scrivi_annotazione("")

        self.pulsante_esegui.setEnabled(False)
        self.pulsante_simula.setEnabled(False)
        self.pulsante_interrompi.setEnabled(True)
        self.etichetta_stato.setText("in esecuzione")
        self.etichetta_stato.setStyleSheet(f"color: {ACCENTO};")

        self.esecuzione = Esecuzione(
            self.client,
            comando,
            su_riga=self.ponte.riga.emit,
            su_fine=lambda esito: self.ponte.fine.emit(esito.codice_uscita, esito.interrotto),
        )
        self.esecuzione.avvia()

    def _interrompi(self) -> None:
        if self.esecuzione is not None:
            self._scrivi_annotazione("[interruzione richiesta]")
            self.esecuzione.interrompi()

    def _al_termine(self, codice: int, interrotto: bool) -> None:
        if self.chiave_temporanea and self.client is not None:
            rimuovi_chiave_dal_controller(self.client, self.chiave_temporanea)
            self._scrivi_annotazione("chiave temporanea rimossa dal controller")
            self.chiave_temporanea = ""

        self._chiudi_client()
        self.esecuzione = None

        self.pulsante_esegui.setEnabled(True)
        self.pulsante_simula.setEnabled(True)
        self.pulsante_interrompi.setEnabled(False)

        if interrotto:
            testo, colore = "interrotto", ROSSO
        elif codice == 0:
            testo, colore = "completato", VERDE
        else:
            testo, colore = f"fallito (uscita {codice})", ROSSO

        self.etichetta_stato.setText(testo)
        self.etichetta_stato.setStyleSheet(f"color: {colore};")
        self._scrivi_annotazione("")
        self._scrivi_annotazione(f"=== {testo} ===")
        self.statusBar().showMessage(f"Ultima esecuzione: {testo}", 10000)

    def _chiudi_client(self) -> None:
        if self.client is not None:
            try:
                self.client.close()
            except Exception:  # noqa: BLE001 - chiusura best effort
                pass
            self.client = None

    # --------------------------------------------------------------- console --

    def _scrivi_riga(self, riga: str) -> None:
        self.righe_grezze.append(riga)
        self.console.moveCursor(QTextCursor.End)
        self.console.insertHtml(in_html(riga) + "<br>")
        self.console.moveCursor(QTextCursor.End)

    def _scrivi_annotazione(self, testo: str) -> None:
        """Righe scritte dal software, non da Ansible: si distinguono a colpo d'occhio."""
        self.righe_grezze.append(testo)
        self.console.moveCursor(QTextCursor.End)
        self.console.insertHtml(
            f"<span style='color:{TESTO_TENUE}'>{in_html(testo)}</span><br>"
        )
        self.console.moveCursor(QTextCursor.End)

    def _pulisci_console(self) -> None:
        self.console.clear()
        self.righe_grezze = []

    def _esporta_log(self) -> None:
        if not self.righe_grezze:
            QMessageBox.information(self, "Niente da esportare", "La console e' vuota.")
            return
        predefinito = f"ansible-{datetime.datetime.now():%Y%m%d-%H%M%S}.log"
        percorso, _ = QFileDialog.getSaveFileName(
            self, "Salva il log", predefinito, "Log (*.log);;Testo (*.txt)"
        )
        if not percorso:
            return
        with open(percorso, "w", encoding="utf-8") as f:
            f.write("\n".join(senza_colori(r) for r in self.righe_grezze))
        self.statusBar().showMessage(f"Log salvato in {percorso}", 6000)

    def closeEvent(self, evento) -> None:
        if self.esecuzione is not None:
            risposta = QMessageBox.question(
                self, "Playbook in corso",
                "C'e' un playbook in esecuzione. Chiudere comunque?",
            )
            if risposta != QMessageBox.Yes:
                evento.ignore()
                return
            self.esecuzione.interrompi()
        self._chiudi_client()
        evento.accept()


def avvia() -> int:
    applicazione = QApplication([])
    applicazione.setStyleSheet(FOGLIO_DI_STILE)
    finestra = Finestra()
    finestra.show()
    return applicazione.exec()
