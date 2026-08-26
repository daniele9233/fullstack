"""Dashboard web per il repository Ansible fullstack.

Lancia i playbook di playbooks/, ne trasmette l'output al browser riga per
riga, e mostra lo stato reale delle cinque aree interrogando i nodi via SSH
(vedi sonde.py).

Sostituisce la verifica preliminare che il README dichiara rimossa: "I ruoli
preflight_* e il playbook playbooks/00-verifica.yml sono stati rimossi. Al loro
posto arriveranno una dashboard web e dei controlli scritti meglio."

Si serve su 127.0.0.1 e NON ha autenticazione: l'accesso da fuori passa da un
tunnel SSH, la porta non va esposta sulla rete.
"""

from flask import Flask, request, jsonify, render_template
from datetime import datetime, timezone
import hashlib
import subprocess
import threading
import shlex
import shutil
import os
import re

from inventario import Inventario
from sonde import Raccoglitore, AREE
import modifiche

app = Flask(__name__)

# La radice del repository: dashboard/ sta dentro, quindi si sale di uno.
# E' anche la working directory di ogni comando, perche' ansible.cfg
# (inventory, roles_path, log_path) e' li' e vale solo da li'.
REPO_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))

# Il venv e' FACOLTATIVO, al contrario della dashboard da cui questa deriva.
# fullstack gira con l'Ansible di sistema (README: core 2.18.8 su
# /usr/bin/python3); chi invece lo tiene in un virtualenv esporta
# ANSIBLE_VENV=/percorso/al/venv prima di avviare.
VENV = os.environ.get('ANSIBLE_VENV', '').strip() or None
if VENV and not os.path.isdir(VENV):
    VENV = None

_job_lock = threading.Lock()
_job_state = {
    'status': 'idle',   # idle | running | success | failed
    'output': [],
    'azione': None,
}


# ---------------------------------------------------------------------------
# COMANDI
#
# Una voce per pulsante. E' una allowlist: /api/run accetta solo queste chiavi,
# quindi dal browser non si puo' comporre una riga di comando arbitraria.
#
# fullstack non ha tag e non ha vault: ogni area e' un playbook a se in
# playbooks/, e site.yml li mette in fila con import_playbook. Le varianti con
# -e sono quelle che i playbook stessi documentano nei propri commenti di testa.
# ---------------------------------------------------------------------------
def _pb(*args):
    return {'exe': 'ansible-playbook', 'args': list(args)}


PLAYBOOK_AREA = {
    'bilanciatori': 'playbooks/10-bilanciatori.yml',
    'rancher':      'playbooks/20-rancher.yml',
    'database':     'playbooks/30-database.yml',
    'storage':      'playbooks/40-storage.yml',
    'kafka':        'playbooks/50-kafka.yml',
}

COMANDI = {
    # -- installazione ------------------------------------------------------
    'installa_tutto':        _pb('site.yml'),
    'installa_bilanciatori': _pb(PLAYBOOK_AREA['bilanciatori']),
    'installa_rancher':      _pb(PLAYBOOK_AREA['rancher']),
    'installa_database':     _pb(PLAYBOOK_AREA['database']),
    'installa_storage':      _pb(PLAYBOOK_AREA['storage']),
    'installa_kafka':        _pb(PLAYBOOK_AREA['kafka']),

    # varianti Kafka documentate in playbooks/50-kafka.yml
    'kafka_collaudo':        _pb(PLAYBOOK_AREA['kafka'], '-e', 'kafka_smoke_test=true'),
    'kafka_java_apt':        _pb(PLAYBOOK_AREA['kafka'], '-e', 'kafka_java_install_method=apt'),

    # -- prova a vuoto ------------------------------------------------------
    'prova_tutto':           _pb('site.yml', '--check', '--diff'),
    'prova_bilanciatori':    _pb(PLAYBOOK_AREA['bilanciatori'], '--check', '--diff'),
    'prova_rancher':         _pb(PLAYBOOK_AREA['rancher'], '--check', '--diff'),
    'prova_database':        _pb(PLAYBOOK_AREA['database'], '--check', '--diff'),
    'prova_storage':         _pb(PLAYBOOK_AREA['storage'], '--check', '--diff'),
    'prova_kafka':           _pb(PLAYBOOK_AREA['kafka'], '--check', '--diff'),

    # -- controlli di sintassi (non toccano i nodi) -------------------------
    'sintassi_tutto':        _pb('site.yml', '--syntax-check'),
    'elenco_host':           {'exe': 'ansible-inventory', 'args': ['--graph', '--vars']},

    # -- operazioni distruttive --------------------------------------------
    # Sostituiscono gli uninstall-*.sh dell'altro repository, che qui non
    # esistono: le operazioni che distruggono qualcosa in fullstack sono le
    # extra-vars che i ruoli prevedono gia'.
    'azzera_nodi':           _pb(PLAYBOOK_AREA['rancher'], '-e', 'clean_nodes_before_join=true'),
    'reinit_postgres':       _pb(PLAYBOOK_AREA['database'], '-e', 'postgres_force_reinit=true'),
    'rigenera_certificati_kafka': _pb(PLAYBOOK_AREA['kafka'], '-e',
                                      'kafka_force_regenerate_certs=true'),

    # Azzeramento completo: toglie dal nodo tutto quello che questo repository
    # installa, qualunque ruolo abbia avuto. Vedi playbooks/99-azzera.yml.
    'azzera_nodo':           _pb('playbooks/99-azzera.yml', '-e', 'azzera_conferma=AZZERA'),
}

DISTRUTTIVE = {'azzera_nodi', 'reinit_postgres', 'rigenera_certificati_kafka', 'azzera_nodo'}

# Azioni per cui "su chi" va DETTO, non lasciato al valore predefinito. Il
# playbook si protegge da solo pretendendo azzera_conferma, ma quella la passa la
# dashboard: qui serve la seconda meta' del vincolo.
#
# Non vuol dire "non si puo' prendere tutto": si puo', scegliendo "tutti i nodi
# dell'inventario" nella tendina, che vale --limit all ("all" e' un gruppo vero
# di Ansible ed e' gia' in Inventario.nomi_validi). Vuol dire che la riga di
# comando piu' corta non puo' essere quella che tocca piu' macchine.
RICHIEDE_LIMIT = {'azzera_nodo'}

# --limit accetta solo nomi presenti in inventario (vedi Inventario.nomi_validi).
# Questa e' la prima rete: la forma. Un nome di host o gruppo Ansible non
# contiene spazi ne' metacaratteri di shell.
_NOME_LIMIT_RE = re.compile(r'^[A-Za-z0-9_.:\-]{1,120}$')


def _env_base():
    """Ambiente comune a ogni comando lanciato dalla dashboard."""
    env = os.environ.copy()
    if VENV:
        env['VIRTUAL_ENV'] = VENV
        env['PATH'] = f'{VENV}/bin:' + env.get('PATH', '')
        env.pop('PYTHONHOME', None)
    env['ANSIBLE_FORCE_COLOR'] = '1'
    env['PYTHONUNBUFFERED'] = '1'
    return env


inventario = Inventario(REPO_DIR, _env_base)
raccoglitore = Raccoglitore(REPO_DIR, _env_base, inventario)


def _esegui(cmd_info, limit):
    try:
        env = _env_base()
        parti = [cmd_info['exe']] + list(cmd_info['args'])
        if limit:
            parti += ['--limit', limit]

        quoted = ' '.join(shlex.quote(p) for p in parti)
        if VENV:
            shell_cmd = (
                f'source {shlex.quote(VENV)}/bin/activate'
                f' && printf "\\033[2m[venv] %s\\033[0m\\n" "$VIRTUAL_ENV"'
                f' && exec {quoted}'
            )
        else:
            shell_cmd = f'exec {quoted}'

        _job_state['output'].append(f'\x1b[2m$ {quoted}\x1b[0m')

        proc = subprocess.Popen(
            ['bash', '-c', shell_cmd],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=env,
            cwd=REPO_DIR,
            bufsize=1,
            universal_newlines=True,
        )

        for riga in iter(proc.stdout.readline, ''):
            _job_state['output'].append(riga.rstrip('\n'))

        proc.wait()
        _job_state['status'] = 'success' if proc.returncode == 0 else 'failed'

    except Exception as exc:
        _job_state['output'].append(f'[ERRORE INTERNO] {exc}')
        _job_state['status'] = 'failed'

    finally:
        # Un playbook appena finito ha quasi certamente cambiato lo stato dei
        # nodi: la prossima lettura del cruscotto deve ripartire da zero.
        try:
            raccoglitore.invalida()
        except Exception:                                   # pragma: no cover
            pass
        _job_lock.release()


# ---------------------------------------------------------------------------
# Pagina e job
# ---------------------------------------------------------------------------
VENDOR_DIR = os.path.join(os.path.dirname(__file__), 'static', 'vendor')


def _vendor():
    """Quali librerie ci sono in locale, cosi' la pagina non chiami la CDN.

    Su una rete senza uscita cdn.tailwindcss.com non risponde e la dashboard
    resta senza tema e senza icone. Chi ha quel problema mette i tre file in
    static/vendor/ e la pagina li prende da li' (vedi static/vendor/LEGGIMI).
    """
    return {
        'tailwind': os.path.exists(os.path.join(VENDOR_DIR, 'tailwind.js')),
        'lucide':   os.path.exists(os.path.join(VENDOR_DIR, 'lucide.js')),
        'chart':    os.path.exists(os.path.join(VENDOR_DIR, 'chart.js')),
    }


@app.route('/')
def index():
    return render_template('index.html', vendor=_vendor())


@app.route('/api/output')
def api_output():
    since = request.args.get('since', '0')
    try:
        since = max(0, int(since))
    except ValueError:
        since = 0
    righe = _job_state['output'][since:]
    return jsonify({
        'lines': righe,
        'total': len(_job_state['output']),
        'status': _job_state['status'],
        'azione': _job_state['azione'],
    })


@app.route('/api/run', methods=['POST'])
def api_run():
    dati = request.get_json(force=True, silent=True) or {}
    azione = dati.get('azione') or dati.get('action') or ''
    limit = (dati.get('limit') or '').strip()

    if azione not in COMANDI:
        return jsonify({'error': 'Azione non valida'}), 400

    if azione in RICHIEDE_LIMIT and not limit:
        return jsonify({'error': 'Questa operazione richiede di scegliere su quali '
                                 'host agire. Per prenderli tutti la scelta c\'e\' '
                                 'ed e\' "tutti i nodi dell\'inventario" (--limit '
                                 'all): va indicata, non lasciata come valore '
                                 'vuoto.'}), 400

    if limit:
        if not _NOME_LIMIT_RE.match(limit):
            return jsonify({'error': 'Il filtro --limit contiene caratteri non ammessi'}), 400
        validi = inventario.nomi_validi()
        # Ansible accetta anche 'a:b' e 'a:!b'; qui si tiene la forma semplice,
        # cioe' UN host o UN gruppo, e si controlla che esista davvero.
        if limit not in validi:
            return jsonify({'error': f"'{limit}' non e' un host ne' un gruppo dell'inventario"}), 400

    if not _job_lock.acquire(blocking=False):
        return jsonify({'error': "Un job e' gia' in esecuzione. Attendi il completamento."}), 409

    _job_state['output'] = []
    _job_state['status'] = 'running'
    _job_state['azione'] = azione

    threading.Thread(target=_esegui, args=(COMANDI[azione], limit), daemon=True).start()
    return jsonify({'status': 'started', 'azione': azione, 'limit': limit or None})


@app.route('/api/file')
def api_file():
    rel = request.args.get('path', '')
    full = os.path.normpath(os.path.join(REPO_DIR, rel))
    radice = os.path.normpath(REPO_DIR)
    if full != radice and not full.startswith(radice + os.sep):
        return jsonify({'error': 'Percorso non consentito'}), 403
    # I due file .pem con la chiave privata non si leggono mai, nemmeno da
    # dentro il repository: la dashboard non ha motivo di mostrarli.
    if os.path.basename(full) in ('privkey.pem',):
        return jsonify({'error': 'File non consultabile dalla dashboard'}), 403
    try:
        with open(full, 'r', encoding='utf-8', errors='replace') as f:
            return jsonify({'content': f.read(), 'path': rel})
    except FileNotFoundError:
        return jsonify({'error': 'File non trovato'}), 404
    except IsADirectoryError:
        return jsonify({'error': 'Il percorso e\' una directory'}), 400
    except Exception as exc:
        return jsonify({'error': str(exc)}), 500


# ---------------------------------------------------------------------------
# Modifica di inventario e group_vars
#
# Sono gli unici file scrivibili dalla dashboard: quelli che cambiano a ogni
# giro di laboratorio. I playbook e i ruoli restano di sola lettura.
# ---------------------------------------------------------------------------
def _riconvalida_inventario():
    """Rilegge l'inventario con Ansible. Ritorna l'errore, o None se va bene.

    E' il giudice vero: se ansible-inventory non riesce a caricare il file, non
    ci riuscira' nemmeno ansible-playbook fra due minuti.
    """
    dati = inventario.carica(forza=True)
    errore = dati.get('errore')
    if not errore:
        return None
    # Quando ansible non e' installato l'errore c'e' sempre e non dipende dalla
    # modifica: bloccare il salvataggio per questo vorrebbe dire non poter mai
    # correggere l'inventario su una macchina non ancora preparata.
    if 'non trovato nel PATH' in errore or 'timeout' in errore:
        return None
    return errore


@app.route('/api/modificabili')
def api_modificabili():
    elenco = []
    for rel in modifiche.elenco_modificabili(REPO_DIR):
        percorso = os.path.join(REPO_DIR, rel)
        try:
            st = os.stat(percorso)
            dimensione, modificato = st.st_size, st.st_mtime
        except OSError:
            dimensione, modificato = None, None
        elenco.append({
            'percorso': rel,
            'dimensione': dimensione,
            'modificato': modificato,
            'backup': modifiche.elenco_backup(REPO_DIR, rel),
        })
    return jsonify({'file': elenco})


@app.route('/api/salva', methods=['POST'])
def api_salva():
    dati = request.get_json(force=True, silent=True) or {}
    rel = (dati.get('percorso') or '').strip()
    contenuto = dati.get('contenuto')

    if not isinstance(contenuto, str):
        return jsonify({'error': "Manca il contenuto da salvare."}), 400

    # Riscrivere l'inventario mentre un playbook lo sta leggendo e' il modo piu'
    # rapido per ottenere un'esecuzione che nessuno riesce a spiegare.
    if _job_state['status'] == 'running':
        return jsonify({'error': "C'e' un job in esecuzione: aspetta che finisca "
                                 'prima di modificare inventario o variabili.'}), 409

    esito, errore = modifiche.salva(REPO_DIR, rel, contenuto, _riconvalida_inventario)
    if errore:
        return jsonify({'error': errore}), 400

    # L'inventario e' cambiato: la prossima lettura dello stato deve ripartire
    # da zero, altrimenti il cruscotto mostrerebbe host che non ci sono piu'.
    raccoglitore.invalida()
    return jsonify({'ok': True, 'invariato': esito['invariato'],
                    'backup': esito['backup'],
                    'backup_disponibili': modifiche.elenco_backup(REPO_DIR, rel)})


@app.route('/api/ripristina', methods=['POST'])
def api_ripristina():
    dati = request.get_json(force=True, silent=True) or {}
    rel = (dati.get('percorso') or '').strip()
    nome = (dati.get('backup') or '').strip()

    if _job_state['status'] == 'running':
        return jsonify({'error': "C'e' un job in esecuzione: aspetta che finisca."}), 409

    esito, errore = modifiche.ripristina(REPO_DIR, rel, nome, _riconvalida_inventario)
    if errore:
        return jsonify({'error': errore}), 400

    raccoglitore.invalida()
    return jsonify({'ok': True, 'invariato': esito['invariato'],
                    'backup': esito['backup'],
                    'backup_disponibili': modifiche.elenco_backup(REPO_DIR, rel)})


# ---------------------------------------------------------------------------
# Inventario
# ---------------------------------------------------------------------------
@app.route('/api/inventario')
def api_inventario():
    dati = inventario.carica(forza=request.args.get('forza') == '1')
    gruppi = []
    for area in AREE:
        for nome in area['gruppi']:
            if nome in dati.get('gruppi', {}):
                gruppi.append({'nome': nome, 'area': area['chiave'],
                               'host': dati['gruppi'][nome]})
    # I gruppi che non appartengono a nessuna area (per esempio [local]) vanno
    # mostrati lo stesso: fanno parte dell'inventario e --limit li accetta.
    noti = {g['nome'] for g in gruppi}
    for nome, host in sorted(dati.get('gruppi', {}).items()):
        if nome not in noti:
            gruppi.append({'nome': nome, 'area': None, 'host': host})

    return jsonify({
        'gruppi': gruppi,
        'host': dati.get('host', {}),
        'variabili': dati.get('variabili', {}),
        'fonte': dati.get('fonte'),
        'ramo': dati.get('ramo'),
        'error': dati.get('errore'),
    })


# ---------------------------------------------------------------------------
# Stato live
# ---------------------------------------------------------------------------
@app.route('/api/health')
def api_health():
    """Riepilogo per il cruscotto: host raggiungibili e stato delle aree."""
    if request.args.get('forza') == '1':
        raccoglitore.invalida()
    foto = raccoglitore.fotografia(attendi=2.0)
    aree = foto.get('aree', [])
    return jsonify({
        'host': foto.get('host', {'totale': 0, 'raggiungibili': 0}),
        'aree': [{'chiave': a['chiave'], 'nome': a['nome'], 'stato': a['stato'],
                  'note': a.get('note', [])} for a in aree],
        'installate': sum(1 for a in aree if a['stato'] in ('sano', 'degradato')),
        'sane': sum(1 for a in aree if a['stato'] == 'sano'),
        'totale_aree': len(aree) or len(AREE),
        'aggiornato_a': foto.get('aggiornato_a'),
        'in_corso': foto.get('in_corso', False),
        'error': foto.get('errore'),
    })


@app.route('/api/stack')
def api_stack():
    """Inventario dettagliato delle aree: host, servizi, dettagli per area."""
    if request.args.get('forza') == '1':
        raccoglitore.invalida()
    foto = raccoglitore.fotografia(attendi=2.0)
    return jsonify({
        'aree': foto.get('aree', []),
        'aggiornato_a': foto.get('aggiornato_a'),
        'in_corso': foto.get('in_corso', False),
        'error': foto.get('errore'),
    })


@app.route('/api/dischi')
def api_dischi():
    """Dove finiscono i dati: i mount che i ruoli usano, host per host.

    In fullstack i dischi si montano A MANO (README, "I DISCHI SI MONTANO A
    MANO"): questa pagina serve a vedere in un colpo solo quali nodi hanno un
    disco dedicato e quali stanno scrivendo sulla partizione di sistema.
    """
    foto = raccoglitore.fotografia(attendi=2.0)
    righe = []
    for area in foto.get('aree', []):
        for host in area.get('host', []):
            for disco in host.get('dischi', []):
                righe.append({
                    'area': area['nome'],
                    'host': host['nome'],
                    'raggiungibile': host['raggiungibile'],
                    **disco,
                })
    return jsonify({
        'dischi': righe,
        'aggiornato_a': foto.get('aggiornato_a'),
        'in_corso': foto.get('in_corso', False),
        'error': foto.get('errore'),
    })


# ---------------------------------------------------------------------------
# Certificati
#
# Non c'e' nessun Secret da leggere: in fullstack i certificati sono file nel
# repository, e stanno in DUE posti che il README avverte di tenere allineati
# (righe 22-35). La card li confronta e segnala la divergenza, che e' proprio
# l'errore che quel paragrafo descrive: rinnovarne uno solo.
# ---------------------------------------------------------------------------
CERTIFICATI = [
    {'chiave': 'nginx', 'nome': 'Bilanciatori (nginx)',
     'percorso': 'files/fullchain.pem',
     'usato_da': 'nginx_install, via local_ssl_cert_path'},
    {'chiave': 'rancher', 'nome': 'Rancher / cert-manager',
     'percorso': 'roles/master1_helm_cert_manager_install/files/fullchain.pem',
     'usato_da': 'master1_helm_cert_manager_install, via fullchain_pem_file'},
]


def _parse_dn(dn):
    """Parsa un Distinguished Name openssl ('CN = x, O = y') in dizionario."""
    out = {}
    for pezzo in re.split(r'[,/]', dn or ''):
        if '=' in pezzo:
            k, v = pezzo.split('=', 1)
            out[k.strip().upper()] = v.strip()
    return out


def _parse_data_openssl(s):
    """'Jul 31 23:59:59 2027 GMT' -> datetime UTC (None se non parsabile)."""
    s = ' '.join((s or '').replace('GMT', '').split())
    try:
        return datetime.strptime(s, '%b %d %H:%M:%S %Y').replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _leggi_certificato(scheda):
    info = {'chiave': scheda['chiave'], 'nome': scheda['nome'],
            'percorso': scheda['percorso'], 'usato_da': scheda['usato_da'],
            'trovato': False, 'error': None}

    percorso = os.path.join(REPO_DIR, scheda['percorso'])
    try:
        with open(percorso, 'rb') as f:
            pem = f.read()
    except FileNotFoundError:
        info['error'] = 'file non presente nel repository'
        return info
    except OSError as exc:
        info['error'] = str(exc)
        return info

    openssl = shutil.which('openssl')
    if not openssl:
        info['error'] = 'openssl non trovato nel PATH'
        return info

    def _openssl(args):
        try:
            r = subprocess.run([openssl, 'x509', '-noout'] + args,
                               input=pem, capture_output=True, timeout=10)
            return (r.returncode,
                    (r.stdout or b'').decode('utf-8', 'replace'),
                    (r.stderr or b'').decode('utf-8', 'replace'))
        except Exception as exc:                            # pragma: no cover
            return -1, '', str(exc)

    rc, txt, err = _openssl(['-issuer', '-subject', '-startdate', '-enddate', '-fingerprint',
                             '-sha256'])
    if rc != 0:
        info['error'] = (err.strip()[:200] or 'openssl x509 fallito')
        return info

    info['trovato'] = True
    emittente = soggetto = None
    scade_il = None
    for riga in txt.splitlines():
        riga = riga.strip()
        if riga.startswith('issuer='):
            emittente = riga[len('issuer='):].strip()
        elif riga.startswith('subject='):
            soggetto = riga[len('subject='):].strip()
        elif riga.startswith('notBefore='):
            dt = _parse_data_openssl(riga[len('notBefore='):])
            info['emesso_il'] = dt.isoformat().replace('+00:00', 'Z') if dt else None
        elif riga.startswith('notAfter='):
            scade_il = _parse_data_openssl(riga[len('notAfter='):])
            info['scade_il'] = scade_il.isoformat().replace('+00:00', 'Z') if scade_il else None
        elif 'Fingerprint=' in riga:
            info['impronta'] = riga.split('=', 1)[1].strip()

    sans = []
    rc2, txt2, _ = _openssl(['-ext', 'subjectAltName'])
    if rc2 == 0:
        for riga in txt2.splitlines():
            if 'DNS:' in riga:
                sans = [p.strip()[4:] for p in riga.split(',') if p.strip().startswith('DNS:')]

    dn_emittente = _parse_dn(emittente)
    dn_soggetto = _parse_dn(soggetto)
    info.update({
        'emittente': emittente,
        'soggetto': soggetto,
        'emittente_cn': dn_emittente.get('CN'),
        'emittente_o': dn_emittente.get('O'),
        'cn': dn_soggetto.get('CN'),
        'sans': sans,
        'autofirmato': bool(emittente and soggetto and emittente == soggetto),
    })
    if scade_il:
        info['giorni_rimasti'] = (scade_il - datetime.now(timezone.utc)).days
    if 'impronta' not in info:
        info['impronta'] = hashlib.sha256(pem).hexdigest().upper()
    return info


@app.route('/api/cert')
def api_cert():
    certificati = [_leggi_certificato(c) for c in CERTIFICATI]
    impronte = {c['impronta'] for c in certificati if c.get('trovato') and c.get('impronta')}
    allineati = None
    if len([c for c in certificati if c.get('trovato')]) == len(CERTIFICATI):
        allineati = len(impronte) == 1
    return jsonify({
        'certificati': certificati,
        'allineati': allineati,
        'dominio': inventario.variabile('rancher_domain'),
    })


def _porta():
    """Porta di ascolto: 8080, o quella in DASHBOARD_PORT.

    Serve quando la 8080 e' gia' occupata da qualcos'altro sul control node
    (spesso da un'altra copia di questa stessa dashboard rimasta accesa).
    """
    grezza = os.environ.get('DASHBOARD_PORT', '').strip()
    if not grezza:
        return 8080
    try:
        porta = int(grezza)
    except ValueError:
        raise SystemExit(f"DASHBOARD_PORT='{grezza}' non e' un numero.")
    if not 1 <= porta <= 65535:
        raise SystemExit(f'DASHBOARD_PORT={porta} fuori intervallo (1-65535).')
    return porta


if __name__ == '__main__':
    # L'ascolto sta su localhost perche' la dashboard NON ha autenticazione e
    # lancia playbook: chi la apre puo' installare e distruggere. Da un'altra
    # macchina ci si arriva con un tunnel SSH, non aprendo la porta.
    #
    # DASHBOARD_HOST esiste per chi ha una ragione precisa per cambiarla — una
    # rete di gestione isolata, un reverse proxy che autentica davanti. Se il
    # valore non e' di loopback la dashboard lo dice a chiare lettere e parte
    # lo stesso: la decisione resta di chi la avvia.
    host = os.environ.get('DASHBOARD_HOST', '').strip() or '127.0.0.1'
    porta = _porta()
    if host not in ('127.0.0.1', 'localhost', '::1'):
        print(f'\n  ATTENZIONE: ascolto su {host}:{porta}, non su localhost.')
        print('  La dashboard non ha autenticazione e lancia playbook:')
        print('  chiunque raggiunga questa porta puo' + "'" + ' installare e distruggere.')
        print('  Mettila dietro a un tunnel SSH o a un proxy che autentichi.\n')
    app.run(host=host, port=porta, debug=False, threaded=True)
