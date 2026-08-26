"""Lettura dell'inventario Ansible per la dashboard.

Due cose servono a chi sta sopra:

  carica()        la fotografia dell'inventario — gruppi, host, indirizzi, e le
                  poche variabili di group_vars che le sonde e la GUI usano
                  (VIP, porte, punti di mount).

  risolvi_limite() che cosa mettere dopo --limit, a partire da quello che ha
                  scelto la GUI. E' una allowlist: senza, il campo "limita a"
                  sarebbe un modo per infilare stringhe arbitrarie nella riga
                  di comando. Accetta un gruppo, un nome di host, e soprattutto
                  l'INDIRIZZO di un host, che ritraduce nel nome. La GUI sceglie
                  i nodi per indirizzo — l'hostname di un server di laboratorio
                  cambia, l'IP con cui lo si raggiunge no — mentre Ansible i
                  pattern li confronta solo con i nomi e con i gruppi.

La fonte e' 'ansible-inventory --list', che risolve inventory.ini e group_vars
insieme. Quando ansible non c'e' — prima installazione, venv non attivo — si
ripiega su una lettura diretta di inventory.ini: l'elenco degli host resta
giusto, le variabili di group_vars no, e chi legge lo sa dal campo 'fonte'.
"""

import configparser
import json
import os
import re
import subprocess
import time

# Variabili di group_vars che servono alle sonde e alla GUI. Si prendono
# dall'hostvars del primo host che le ha: sono tutte definite in
# group_vars/all.yml, uguali per tutti.
VARIABILI_INTERESSANTI = [
    'rancher_domain',
    'nginx_server_name',
    'vip_rancher_ip',
    'vip_patroni_ip',
    'nfs_vip_ip',
    'haproxy_stats_port',
    'haproxy_primary_port',
    'haproxy_standby_port',
    'postgres_version',
    'postgres_mount_point',
    'kafka_version',
    'kafka_broker_port',
    'kafka_controller_port',
    'kafka_data_dir',
    'gluster_brick_root',
    'disk_mount_point',
    'cluster_name',
    'rancher_version',
]

# Gruppi che ansible-inventory restituisce sempre e che non sono aree.
GRUPPI_TECNICI = {'all', 'ungrouped', 'local'}


def _esegui(cmd, cwd, env, timeout=30):
    """Ritorna (stdout, errore, stderr). stderr serve anche quando va bene:
    ansible-inventory segnala i guai li' dentro senza cambiare codice di uscita.
    """
    try:
        r = subprocess.run(cmd, capture_output=True, text=True,
                           cwd=cwd, env=env, timeout=timeout)
        if r.returncode != 0:
            return None, ((r.stderr or r.stdout) or '').strip()[:400], r.stderr
        return r.stdout, None, r.stderr
    except FileNotFoundError:
        return None, f'{cmd[0]} non trovato nel PATH', ''
    except subprocess.TimeoutExpired:
        return None, f'{cmd[0]} in timeout (>{timeout}s)', ''
    except Exception as exc:                                # pragma: no cover
        return None, str(exc), ''


_PARSE_FALLITO_RE = re.compile(
    r'Failed to parse inventory(?: with .*? plugin)?:?\s*(.*)')


def _motivo_parse_fallito(stderr):
    """Estrae dal warning di ansible-inventory il motivo, se c'e' stato un guaio."""
    testo = (stderr or '')
    if 'Failed to parse inventory' not in testo:
        return None
    for riga in testo.splitlines():
        m = _PARSE_FALLITO_RE.search(riga)
        if m and m.group(1).strip():
            return 'Ansible non riesce a leggere l\'inventario: ' + m.group(1).strip()
    return 'Ansible non riesce a leggere l\'inventario.'


def _da_ansible_inventory(repo_dir, env):
    """Inventario risolto da Ansible: gruppi, host e variabili di group_vars."""
    out, err, stderr = _esegui(['ansible-inventory', '--list'], repo_dir, env)
    if out is None:
        return None, err

    # ATTENZIONE AL CODICE DI USCITA: quando il plugin ini non riesce a leggere
    # inventory.ini, ansible-inventory NON fallisce. Stampa un warning su stderr,
    # ripiega su un inventario vuoto ed esce con 0. Fidandosi del solo returncode
    # si prende quell'inventario vuoto per buono, e da li' in poi ogni playbook
    # "funziona" senza toccare nessun host. Il messaggio e' l'unico segnale.
    guasto = _motivo_parse_fallito(stderr)
    if guasto:
        return None, guasto

    try:
        data = json.loads(out or '{}')
    except json.JSONDecodeError as exc:
        return None, f'ansible-inventory: output non JSON ({exc})'

    hostvars = (data.get('_meta') or {}).get('hostvars') or {}

    gruppi = {}
    for nome, contenuto in data.items():
        if nome == '_meta' or nome in GRUPPI_TECNICI:
            continue
        if not isinstance(contenuto, dict):
            continue
        host = contenuto.get('hosts') or []
        if not isinstance(host, list):
            continue
        gruppi[nome] = host

    # 'local' esiste davvero in inventory.ini e serve a 20-rancher.yml, ma non
    # e' un'area da monitorare: lo si tiene fuori dai gruppi e basta.

    host = {}
    for nome in sorted(hostvars):
        hv = hostvars[nome] or {}
        host[nome] = {
            'nome': nome,
            'indirizzo': hv.get('ansible_host') or nome,
            'utente': hv.get('ansible_user'),
            'connessione': hv.get('ansible_connection'),
        }

    variabili = {}
    for chiave in VARIABILI_INTERESSANTI:
        for hv in hostvars.values():
            if isinstance(hv, dict) and chiave in hv:
                valore = hv[chiave]
                # Le variabili non risolte ('{{ ... }}') non servono a nessuno:
                # ansible-inventory non le espande e mostrarle confonde.
                if isinstance(valore, str) and '{{' in valore:
                    continue
                variabili[chiave] = valore
                break

    return {'gruppi': gruppi, 'host': host, 'variabili': variabili,
            'fonte': 'ansible-inventory'}, None


def _da_inventory_ini(repo_dir):
    """Ripiego quando ansible non c'e': si legge inventory.ini a mano.

    Basta per popolare la pagina Inventario e per validare --limit. Le
    variabili di group_vars restano vuote: le sonde ripiegano sui loro
    valori di default e lo dicono.
    """
    percorso = os.path.join(repo_dir, 'inventory.ini')
    if not os.path.exists(percorso):
        return None, 'inventory.ini non trovato'

    parser = configparser.ConfigParser(allow_no_value=True, delimiters=('=',))
    # I nomi di gruppo sono case sensitive e le righe host non sono chiavi:
    # si legge il file a mano invece di combattere con configparser.
    del parser

    gruppi, host = {}, {}
    gruppo_corrente = None
    try:
        with open(percorso, 'r', encoding='utf-8') as f:
            righe = f.readlines()
    except OSError as exc:
        return None, str(exc)

    for riga in righe:
        riga = riga.strip()
        if not riga or riga.startswith('#') or riga.startswith(';'):
            continue
        if riga.startswith('[') and riga.endswith(']'):
            nome = riga[1:-1].strip()
            # [gruppo:vars] e [gruppo:children] non sono elenchi di host.
            if ':' in nome:
                gruppo_corrente = None
                continue
            gruppo_corrente = nome
            if nome not in GRUPPI_TECNICI:
                gruppi.setdefault(nome, [])
            continue
        if gruppo_corrente is None or gruppo_corrente in GRUPPI_TECNICI:
            continue
        pezzi = riga.split()
        nome_host = pezzi[0]
        campi = {}
        for pezzo in pezzi[1:]:
            if '=' in pezzo:
                k, v = pezzo.split('=', 1)
                campi[k] = v
        gruppi[gruppo_corrente].append(nome_host)
        host.setdefault(nome_host, {
            'nome': nome_host,
            'indirizzo': campi.get('ansible_host') or nome_host,
            'utente': campi.get('ansible_user'),
            'connessione': campi.get('ansible_connection'),
        })

    return {'gruppi': gruppi, 'host': host, 'variabili': {},
            'fonte': 'inventory.ini'}, None


class Inventario:
    """Fotografia dell'inventario con una cache breve.

    ansible-inventory ci mette qualche centinaio di millisecondi e
    l'inventario non cambia mentre la pagina e' aperta: 30 secondi di cache
    evitano di rilanciarlo a ogni polling della GUI.
    """

    TTL = 30

    def __init__(self, repo_dir, costruisci_env):
        self._repo_dir = repo_dir
        self._costruisci_env = costruisci_env
        self._dati = None
        self._letto_a = 0

    def carica(self, forza=False):
        adesso = time.time()
        if not forza and self._dati is not None and (adesso - self._letto_a) < self.TTL:
            return self._dati

        dati, errore = _da_ansible_inventory(self._repo_dir, self._costruisci_env())
        if dati is None:
            ripiego, errore_ini = _da_inventory_ini(self._repo_dir)
            if ripiego is None:
                dati = {'gruppi': {}, 'host': {}, 'variabili': {}, 'fonte': None,
                        'errore': errore_ini or errore}
            else:
                ripiego['errore'] = errore
                dati = ripiego
        else:
            dati['errore'] = None

        dati['ramo'] = self._ramo_git()
        self._dati = dati
        self._letto_a = adesso
        return dati

    def _ramo_git(self):
        out, _, _ = _esegui(['git', 'rev-parse', '--abbrev-ref', 'HEAD'],
                            self._repo_dir, os.environ.copy(), timeout=10)
        return (out or '').strip() or None

    def per_indirizzo(self):
        """Indice inverso di ansible_host: {indirizzo: [nomi di inventario]}.

        Piu' righe di inventario possono avere lo stesso ansible_host — sono
        alias della STESSA macchina, e chi sceglie quell'indirizzo li vuole
        tutti.
        """
        indice = {}
        for nome, scheda in (self.carica().get('host') or {}).items():
            indirizzo = (scheda or {}).get('indirizzo')
            if indirizzo:
                indice.setdefault(indirizzo, []).append(nome)
        for nomi in indice.values():
            nomi.sort()
        return indice

    def risolvi_limite(self, valore):
        """Da quello che sceglie la GUI al pattern da mettere dopo --limit.

        La GUI propone gli host per INDIRIZZO, non per nome: in laboratorio
        l'hostname di un server cambia spesso, l'indirizzo con cui lo si
        raggiunge no. Ansible pero' i pattern li confronta con i NOMI di
        inventario e con i gruppi — dentro ansible_host non guarda — quindi
        l'indirizzo va ritradotto qui, prima che finisca sulla riga di comando.

        Ritorna (pattern, host, errore):
          pattern  quello che va dopo --limit ('' se non si limita niente)
          host     i nomi di inventario che quel pattern seleziona
          errore   il testo da mostrare all'utente, oppure None
        """
        valore = (valore or '').strip()
        if not valore:
            return '', [], None

        dati = self.carica()
        gruppi = dati.get('gruppi') or {}
        host = dati.get('host') or {}

        # Un nome di gruppo o di host passa com'era: la dashboard di prima
        # mandava quelli, e un segnalibro o una pagina non ricaricata non deve
        # smettere di funzionare per il cambio di etichetta.
        if valore in gruppi:
            return valore, list(gruppi[valore]), None
        if valore in host:
            return valore, [valore], None
        if valore in GRUPPI_TECNICI:
            return valore, [], None

        nomi = self.per_indirizzo().get(valore, [])
        if not nomi:
            return None, [], (f"'{valore}' non e' un host, un gruppo ne' un "
                              "indirizzo dell'inventario")
        return ':'.join(nomi), nomi, None

    def host_del_gruppo(self, gruppo):
        return list(self.carica().get('gruppi', {}).get(gruppo, []))

    def indirizzo(self, host):
        scheda = self.carica().get('host', {}).get(host) or {}
        return scheda.get('indirizzo') or host

    def variabile(self, chiave, predefinito=None):
        return self.carica().get('variabili', {}).get(chiave, predefinito)
