"""Esecuzione dei playbook sul controller Ansible, via SSH.

Ansible non gira su Windows: il software si collega al controller e lancia li'
ansible-playbook, riportando l'output riga per riga mentre esce.

Si usa paramiko e non il comando ssh di sistema: e' una libreria Python pura,
funziona su Windows senza dipendere da OpenSSH installato, e permette di
leggere lo stream mentre arriva invece di aspettare la fine.
"""

from __future__ import annotations

import os
import posixpath
import shlex
import socket
import threading
import time
from dataclasses import dataclass

import paramiko

from .commands import Opzioni, Playbook, comando_remoto


class ErroreConnessione(Exception):
    """Il controller non e' raggiungibile o le credenziali non vanno."""


@dataclass
class Esito:
    codice_uscita: int
    interrotto: bool = False


def _chiave_privata(percorso: str, passphrase: str | None):
    """Carica una chiave privata provando i formati piu' diffusi.

    paramiko non riconosce il formato dal contenuto: va tentato uno per uno.
    Un .pem di AWS di solito e' RSA, le chiavi generate di recente da ssh-keygen
    sono Ed25519, e OpenSSH usa un contenitore proprio per entrambe.
    """
    percorso = os.path.expanduser(percorso)
    if not os.path.exists(percorso):
        raise ErroreConnessione(f"La chiave non esiste: {percorso}")

    errori = []
    for tipo in (paramiko.RSAKey, paramiko.Ed25519Key, paramiko.ECDSAKey, paramiko.DSSKey):
        try:
            return tipo.from_private_key_file(percorso, password=passphrase or None)
        except paramiko.PasswordRequiredException:
            raise ErroreConnessione(
                "La chiave e' protetta da passphrase: inseriscila nel campo apposito."
            )
        except paramiko.SSHException as errore:
            errori.append(f"{tipo.__name__}: {errore}")

    raise ErroreConnessione(
        "Formato della chiave non riconosciuto.\n" + "\n".join(errori)
    )


def apri_connessione(connessione, passphrase: str = "", password: str = "") -> paramiko.SSHClient:
    """Apre la sessione SSH verso il controller."""
    if not connessione.host.strip():
        raise ErroreConnessione("Indirizzo del controller non indicato.")

    client = paramiko.SSHClient()
    client.load_system_host_keys()
    # AutoAddPolicy: il controller e' una macchina nota dell'infrastruttura e
    # non si vuole bloccare l'utente al primo collegamento. Su una rete non
    # fidata andrebbe sostituita con RejectPolicy e un known_hosts popolato.
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    try:
        if connessione.metodo == "chiave":
            chiave = _chiave_privata(connessione.percorso_chiave, passphrase)
            client.connect(
                hostname=connessione.host.strip(),
                port=int(connessione.porta),
                username=connessione.utente.strip(),
                pkey=chiave,
                timeout=15,
                allow_agent=False,
                look_for_keys=False,
            )
        else:
            client.connect(
                hostname=connessione.host.strip(),
                port=int(connessione.porta),
                username=connessione.utente.strip(),
                password=password,
                timeout=15,
                allow_agent=False,
                look_for_keys=False,
            )
    except paramiko.AuthenticationException:
        raise ErroreConnessione(
            "Autenticazione rifiutata: controlla utente, chiave o password."
        )
    except (socket.timeout, socket.error, OSError) as errore:
        raise ErroreConnessione(f"Controller non raggiungibile: {errore}")
    except paramiko.SSHException as errore:
        raise ErroreConnessione(f"Errore SSH: {errore}")

    return client


def prova_connessione(connessione, passphrase: str = "", password: str = "") -> str:
    """Verifica il collegamento e restituisce un riepilogo di cosa ha trovato."""
    client = apri_connessione(connessione, passphrase, password)
    try:
        righe = []
        for etichetta, comando in (
            ("utente", "whoami"),
            ("sistema", "cat /etc/os-release 2>/dev/null | sed -n 's/^PRETTY_NAME=//p' | tr -d '\"'"),
            ("ansible", "ansible --version 2>/dev/null | head -1 || echo 'NON TROVATO'"),
        ):
            _, stdout, _ = client.exec_command(comando, timeout=20)
            righe.append(f"{etichetta}: {stdout.read().decode('utf-8', 'replace').strip()}")

        cartella = shlex.quote(connessione.cartella_ansible)
        _, stdout, _ = client.exec_command(
            f"test -d {cartella} && echo presente || echo MANCANTE", timeout=20
        )
        righe.append(
            f"cartella {connessione.cartella_ansible}: "
            f"{stdout.read().decode('utf-8', 'replace').strip()}"
        )
        return "\n".join(righe)
    finally:
        client.close()


def carica_chiave_sul_controller(client: paramiko.SSHClient, percorso_locale: str) -> str:
    """Copia una chiave dal PC Windows al controller, con permessi 0600.

    Serve quando la chiave dei nodi gestiti sta sul desktop e non sul
    controller. Il file va in /tmp con il permesso che SSH pretende: una chiave
    leggibile da altri viene rifiutata con "UNPROTECTED PRIVATE KEY FILE".
    """
    percorso_locale = os.path.expanduser(percorso_locale)
    if not os.path.exists(percorso_locale):
        raise ErroreConnessione(f"La chiave dei nodi non esiste: {percorso_locale}")

    nome = os.path.basename(percorso_locale)
    remoto = posixpath.join("/tmp", f".launcher-{int(time.time())}-{nome}")

    sftp = client.open_sftp()
    try:
        sftp.put(percorso_locale, remoto)
        sftp.chmod(remoto, 0o600)
    finally:
        sftp.close()
    return remoto


def rimuovi_chiave_dal_controller(client: paramiko.SSHClient, percorso_remoto: str) -> None:
    if not percorso_remoto:
        return
    try:
        sftp = client.open_sftp()
        try:
            sftp.remove(percorso_remoto)
        finally:
            sftp.close()
    except (OSError, paramiko.SSHException):
        # Una chiave temporanea rimasta in /tmp non giustifica un errore
        # all'utente: il playbook e' gia' finito.
        pass


class Esecuzione:
    """Un playbook in corso: legge lo stream e permette di interromperlo."""

    def __init__(self, client: paramiko.SSHClient, comando: str, su_riga, su_fine):
        self._client = client
        self._comando = comando
        self._su_riga = su_riga
        self._su_fine = su_fine
        self._canale = None
        self._interrotto = False
        self._thread = None

    def avvia(self) -> None:
        self._thread = threading.Thread(target=self._esegui, daemon=True)
        self._thread.start()

    def interrompi(self) -> None:
        """Manda Ctrl-C al playbook, come farebbe un utente al terminale."""
        self._interrotto = True
        if self._canale is not None:
            try:
                self._canale.send("\x03")
                time.sleep(0.5)
                self._canale.close()
            except (OSError, paramiko.SSHException):
                pass

    def _esegui(self) -> None:
        codice = -1
        try:
            trasporto = self._client.get_transport()
            self._canale = trasporto.open_session()
            # get_pty: senza uno pseudo-terminale Ansible bufferizza l'output e
            # lo consegna a blocchi alla fine, invece che riga per riga.
            self._canale.get_pty(term="xterm-256color", width=200, height=50)
            self._canale.exec_command(self._comando)

            resto = b""
            while True:
                if self._canale.recv_ready():
                    dati = self._canale.recv(65536)
                    if not dati:
                        break
                    resto += dati
                    *righe, resto = resto.split(b"\n")
                    for riga in righe:
                        self._su_riga(riga.decode("utf-8", "replace").rstrip("\r"))
                elif self._canale.exit_status_ready():
                    break
                else:
                    time.sleep(0.05)

            # Quello che resta nel buffer dopo l'ultimo a capo.
            dati = self._canale.recv(65536) if self._canale.recv_ready() else b""
            resto += dati
            if resto.strip():
                self._su_riga(resto.decode("utf-8", "replace").rstrip("\r"))

            codice = self._canale.recv_exit_status()
        except Exception as errore:  # noqa: BLE001 - va mostrato all'utente
            self._su_riga(f"[ERRORE] {errore}")
        finally:
            self._su_fine(Esito(codice_uscita=codice, interrotto=self._interrotto))


def prepara_comando(
    playbook: Playbook,
    opzioni: Opzioni,
    connessione,
) -> str:
    """Il comando completo che verra' eseguito sul controller."""
    return comando_remoto(
        playbook,
        opzioni,
        cartella_ansible=connessione.cartella_ansible,
        venv=connessione.venv,
    )
