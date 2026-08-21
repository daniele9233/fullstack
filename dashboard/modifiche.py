"""Scrittura di inventario e group_vars dalla dashboard.

Sono gli unici file che si modificano da qui, ed e' una scelta: inventory.ini e
group_vars sono quello che cambia a ogni giro di laboratorio — un host che si
aggiunge, un IP che si sposta, un VIP diverso. I playbook e i ruoli restano di
sola lettura, si toccano con un editor e passano da git.

Tre reti di protezione, in quest'ordine:

  1. ELENCO CHIUSO. Si scrive solo dove dice elenco_modificabili(). Il percorso
     arriva dal browser, quindi non basta controllare che stia dentro al
     repository: deve essere esattamente uno di quei file.

  2. COPIA PRIMA DI SCRIVERE. Ogni salvataggio lascia il file precedente in
     .backup/. E' la differenza fra un errore e un pomeriggio perso.

  3. CONVALIDA DOPO AVER SCRITTO, E RITORNO INDIETRO SE NON PASSA. Il file
     nuovo viene scritto davvero e poi si chiede ad ansible-inventory di
     rileggere tutto: e' l'unico giudice che conta, perche' e' lo stesso che
     legge quando parte un playbook. Se protesta, si rimette il file di prima e
     l'errore torna al browser. Una convalida "a parte" su una copia temporanea
     non vedrebbe le interazioni fra inventario e group_vars.
"""

import glob
import os
import re
import shutil
import time
import yaml

# Quanti backup tenere per file. Venti giri di modifiche sono abbastanza per
# tornare indietro, e restano pochi kilobyte.
BACKUP_DA_TENERE = 20

# Un group_vars grosso sta sotto i 100 KB: il limite serve a non farsi riempire
# la memoria da una richiesta sbagliata, non a limitare l'uso legittimo.
DIMENSIONE_MASSIMA = 1024 * 1024

# I backup si chiamano '<file appiattito>.<data>-<ora>', piu' un eventuale
# '-N' quando due salvataggi cadono nello stesso secondo. La classe di
# caratteri non contiene '/': un nome che arriva dal browser non puo'
# diventare un percorso.
_NOME_BACKUP_RE = re.compile(r'^[A-Za-z0-9_.\-]+\.\d{8}-\d{6}(-\d+)?$')


def elenco_modificabili(repo_dir):
    """I file che la dashboard puo' riscrivere, in ordine di importanza."""
    voci = []
    inventario = os.path.join(repo_dir, 'inventory.ini')
    if os.path.exists(inventario):
        voci.append('inventory.ini')
    for percorso in sorted(glob.glob(os.path.join(repo_dir, 'group_vars', '*.yml'))):
        voci.append(os.path.relpath(percorso, repo_dir))
    return voci


def _dir_backup(repo_dir):
    return os.path.join(repo_dir, 'dashboard', '.backup')


def _prefisso(rel):
    """'group_vars/all.yml' -> 'group_vars__all.yml', per stare in una cartella piatta."""
    return rel.replace(os.sep, '__').replace('/', '__')


def elenco_backup(repo_dir, rel):
    """Backup esistenti per un file, dal piu' recente."""
    cartella = _dir_backup(repo_dir)
    prefisso = _prefisso(rel) + '.'
    fuori = []
    try:
        nomi = os.listdir(cartella)
    except OSError:
        return fuori
    for nome in nomi:
        if not nome.startswith(prefisso):
            continue
        percorso = os.path.join(cartella, nome)
        try:
            st = os.stat(percorso)
        except OSError:
            continue
        fuori.append({'nome': nome, 'quando': st.st_mtime, 'dimensione': st.st_size})
    fuori.sort(key=lambda x: x['quando'], reverse=True)
    return fuori


def _fai_backup(repo_dir, rel):
    """Copia il file attuale in .backup/ e pota i piu' vecchi. Ritorna il nome."""
    sorgente = os.path.join(repo_dir, rel)
    if not os.path.exists(sorgente):
        return None
    cartella = _dir_backup(repo_dir)
    os.makedirs(cartella, exist_ok=True)
    nome = f'{_prefisso(rel)}.{time.strftime("%Y%m%d-%H%M%S")}'
    destinazione = os.path.join(cartella, nome)
    # Due salvataggi nello stesso secondo avrebbero lo stesso nome e il secondo
    # sovrascriverebbe il primo: si aggiunge un suffisso finche' e' libero.
    n = 1
    while os.path.exists(destinazione):
        destinazione = os.path.join(cartella, f'{nome}-{n}')
        n += 1
    shutil.copy2(sorgente, destinazione)

    vecchi = elenco_backup(repo_dir, rel)[BACKUP_DA_TENERE:]
    for v in vecchi:
        try:
            os.unlink(os.path.join(cartella, v['nome']))
        except OSError:
            pass
    return os.path.basename(destinazione)


def _convalida_forma(rel, contenuto):
    """Controlli che si possono fare sul testo, prima ancora di scriverlo."""
    if len(contenuto.encode('utf-8')) > DIMENSIONE_MASSIMA:
        return f'Il file supera {DIMENSIONE_MASSIMA // 1024} KB.'

    if rel.endswith(('.yml', '.yaml')):
        try:
            dati = yaml.safe_load(contenuto)
        except yaml.YAMLError as exc:
            return f'YAML non valido: {exc}'
        # group_vars vuoto e' legittimo (file appena creato); una lista o una
        # stringa no: Ansible si aspetta un dizionario di variabili.
        if dati is not None and not isinstance(dati, dict):
            return ('Un file group_vars deve contenere un dizionario di variabili, '
                    f'non {type(dati).__name__}.')

    if rel == 'inventory.ini':
        aperte = [r for r in contenuto.splitlines()
                  if r.strip().startswith('[') and not r.strip().endswith(']')]
        if aperte:
            return f'Intestazione di gruppo non chiusa: {aperte[0].strip()!r}'
    return None


def salva(repo_dir, rel, contenuto, riconvalida):
    """Scrive un file modificabile. Ritorna (esito, errore).

    'riconvalida' e' una funzione senza argomenti che rilegge l'inventario con
    Ansible e ritorna un messaggio d'errore, oppure None se va tutto bene.
    """
    if rel not in elenco_modificabili(repo_dir):
        return None, f"'{rel}' non e' fra i file modificabili."

    errore = _convalida_forma(rel, contenuto)
    if errore:
        return None, errore

    percorso = os.path.join(repo_dir, rel)
    try:
        with open(percorso, 'r', encoding='utf-8') as f:
            precedente = f.read()
    except OSError as exc:
        return None, f'Impossibile leggere il file attuale: {exc}'

    if precedente == contenuto:
        return {'invariato': True, 'backup': None}, None

    backup = _fai_backup(repo_dir, rel)

    # Scrittura atomica: si scrive di fianco e si rinomina. Un playbook che
    # partisse in questo istante leggerebbe il file vecchio o quello nuovo, mai
    # meta' dell'uno e meta' dell'altro.
    temporaneo = percorso + '.dashboard-tmp'
    try:
        with open(temporaneo, 'w', encoding='utf-8') as f:
            f.write(contenuto)
        os.replace(temporaneo, percorso)
    except OSError as exc:
        try:
            os.unlink(temporaneo)
        except OSError:
            pass
        return None, f'Scrittura fallita: {exc}'

    problema = riconvalida()
    if problema:
        # Ansible non riesce piu' a leggere l'inventario: si torna indietro
        # subito, prima che qualcuno lanci un playbook su una configurazione
        # che non si carica.
        try:
            with open(percorso, 'w', encoding='utf-8') as f:
                f.write(precedente)
        except OSError as exc:                              # pragma: no cover
            return None, (f'{problema}\n\nE il ripristino e\' fallito: {exc}. '
                          f'Il backup e\' in dashboard/.backup/{backup}')
        return None, (f'Ansible non riesce a rileggere l\'inventario con questa '
                      f'modifica, quindi il file precedente e\' stato rimesso '
                      f'al suo posto.\n\n{problema}')

    return {'invariato': False, 'backup': backup}, None


def ripristina(repo_dir, rel, nome_backup, riconvalida):
    """Rimette un backup al posto del file. Ritorna (esito, errore)."""
    if rel not in elenco_modificabili(repo_dir):
        return None, f"'{rel}' non e' fra i file modificabili."
    # Il nome arriva dal browser: deve avere la forma di un backup di QUESTO
    # file, altrimenti si leggerebbe un percorso qualsiasi del filesystem.
    if not _NOME_BACKUP_RE.match(nome_backup or ''):
        return None, 'Nome di backup non valido.'
    if not nome_backup.startswith(_prefisso(rel) + '.'):
        return None, f"Il backup '{nome_backup}' non appartiene a {rel}."

    sorgente = os.path.join(_dir_backup(repo_dir), nome_backup)
    if not os.path.isfile(sorgente):
        return None, 'Backup non trovato.'
    try:
        with open(sorgente, 'r', encoding='utf-8') as f:
            contenuto = f.read()
    except OSError as exc:
        return None, f'Backup illeggibile: {exc}'

    return salva(repo_dir, rel, contenuto, riconvalida)
