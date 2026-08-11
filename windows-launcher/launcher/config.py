"""Impostazioni del software, salvate in %APPDATA%\\AnsibleLauncher\\config.json.

NESSUNA PASSWORD VIENE SALVATA SU DISCO. La chiave SSH si indica per percorso,
cosi' il file privato resta dove sta e il software si limita a leggerlo al
momento del collegamento. La passphrase e l'eventuale password SSH vivono in
memoria per la durata della sessione e non finiscono mai nel file di
configurazione.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field

from .commands import PLAYBOOK_PREDEFINITI, Playbook


def cartella_configurazione() -> str:
    """%APPDATA%\\AnsibleLauncher su Windows, ~/.config/... altrove."""
    base = os.environ.get("APPDATA")
    if base:
        return os.path.join(base, "AnsibleLauncher")
    return os.path.join(os.path.expanduser("~"), ".config", "ansible-launcher")


def percorso_configurazione() -> str:
    return os.path.join(cartella_configurazione(), "config.json")


@dataclass
class Connessione:
    """Come si raggiunge il CONTROLLER Ansible, non i nodi gestiti."""

    host: str = ""
    porta: int = 22
    utente: str = "root"
    # "chiave" oppure "password". La password non viene mai salvata.
    metodo: str = "chiave"
    percorso_chiave: str = ""
    # Cartella del repository Ansible sul controller.
    cartella_ansible: str = "/root/kikkoBetterThanAI/fullstack"
    # Virtualenv da attivare prima di ansible-playbook. Vuoto = nessuno.
    venv: str = ""


@dataclass
class Configurazione:
    connessione: Connessione = field(default_factory=Connessione)
    playbook: list[Playbook] = field(default_factory=lambda: list(PLAYBOOK_PREDEFINITI))
    # Chiave usata da Ansible per raggiungere i NODI GESTITI (--private-key).
    # Se punta a un file locale su Windows, viene caricata sul controller in una
    # posizione temporanea al momento dell'esecuzione e rimossa alla fine.
    chiave_nodi_locale: str = ""
    chiave_nodi_remota: str = ""

    def to_dict(self) -> dict:
        return {
            "connessione": asdict(self.connessione),
            "playbook": [p.to_dict() for p in self.playbook],
            "chiave_nodi_locale": self.chiave_nodi_locale,
            "chiave_nodi_remota": self.chiave_nodi_remota,
        }

    @staticmethod
    def from_dict(d: dict) -> "Configurazione":
        conn = Connessione(**{
            k: v for k, v in (d.get("connessione") or {}).items()
            if k in Connessione.__dataclass_fields__
        })
        elenco = [Playbook.from_dict(p) for p in d.get("playbook") or []]
        return Configurazione(
            connessione=conn,
            playbook=elenco or list(PLAYBOOK_PREDEFINITI),
            chiave_nodi_locale=d.get("chiave_nodi_locale", ""),
            chiave_nodi_remota=d.get("chiave_nodi_remota", ""),
        )


def carica() -> Configurazione:
    percorso = percorso_configurazione()
    if not os.path.exists(percorso):
        return Configurazione()
    try:
        with open(percorso, "r", encoding="utf-8") as f:
            return Configurazione.from_dict(json.load(f))
    except (OSError, ValueError, TypeError):
        # Un file rovinato non deve impedire l'avvio: si riparte dai valori
        # predefiniti e al primo salvataggio viene riscritto.
        return Configurazione()


def salva(configurazione: Configurazione) -> str:
    cartella = cartella_configurazione()
    os.makedirs(cartella, exist_ok=True)
    percorso = percorso_configurazione()
    with open(percorso, "w", encoding="utf-8") as f:
        json.dump(configurazione.to_dict(), f, indent=2, ensure_ascii=False)
    return percorso
