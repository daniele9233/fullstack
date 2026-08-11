"""Conversione dei colori ANSI di Ansible in HTML per la console.

Ansible colora l'output con sequenze SGR: verde per "ok", giallo per "changed",
rosso per "failed". Mostrandole grezze in un widget di testo si vedrebbero i
codici di escape, e senza colori l'output di un playbook lungo e' illeggibile.
"""

from __future__ import annotations

import html
import re

from .theme import COLORI_ANSI, TESTO

# \033[  seguito da parametri numerici separati da ';' e chiuso da una lettera.
_SEQUENZA = re.compile(r"\x1b\[([0-9;]*)([A-Za-z])")


def in_html(riga: str) -> str:
    """Traduce una riga con codici ANSI in HTML colorato."""
    risultato = []
    aperti = 0
    posizione = 0

    for trovato in _SEQUENZA.finditer(riga):
        testo = riga[posizione:trovato.start()]
        if testo:
            risultato.append(html.escape(testo))
        posizione = trovato.end()

        # Solo 'm' e' una modifica di colore: tutto il resto (spostamenti del
        # cursore, cancellazioni di riga) si scarta, non ha senso in un log.
        if trovato.group(2) != "m":
            continue

        for codice in (trovato.group(1) or "0").split(";"):
            if codice in ("", "0"):
                risultato.append("</span>" * aperti)
                aperti = 0
            elif codice == "1":
                risultato.append("<span style='font-weight:600'>")
                aperti += 1
            elif codice in COLORI_ANSI:
                risultato.append(f"<span style='color:{COLORI_ANSI[codice]}'>")
                aperti += 1

    resto = riga[posizione:]
    if resto:
        risultato.append(html.escape(resto))
    risultato.append("</span>" * aperti)

    return "".join(risultato)


def senza_colori(riga: str) -> str:
    """La riga ripulita da ogni sequenza di escape, per il salvataggio su file."""
    return _SEQUENZA.sub("", riga)


def colore_riepilogo(riga: str) -> str | None:
    """Colore da usare per le righe che il software aggiunge di suo."""
    minuscola = riga.lower()
    if "failed" in minuscola or "errore" in minuscola or "fatal" in minuscola:
        return COLORI_ANSI["31"]
    if "changed" in minuscola:
        return COLORI_ANSI["33"]
    if "ok" in minuscola or "completato" in minuscola:
        return COLORI_ANSI["32"]
    return TESTO
