"""Sonde di stato: che cosa sta girando davvero sui nodi.

La dashboard di partenza leggeva tutto da 'kubectl': un cluster Kubernetes
racconta da solo che cosa ha dentro. Qui i nodi sono macchine, e l'unico modo
per sapere come stanno e' andare a chiedere: una chiamata ad-hoc di Ansible per
area, con uno script in SOLA LETTURA che stampa righe 'CHIAVE valore'.

Tutto quello che gli script lanciano e' interrogazione: systemctl is-active,
ip addr, df, findmnt, patronictl list, gluster volume list, pcs status,
kafka-metadata-quorum.sh describe, kubectl get nodes. Niente scrive, niente
riavvia, niente crea file — l'unica eccezione e' LOG_DIR=/tmp/kafka-cli, che
esiste apposta per NON lasciare file di root dentro /opt/kafka/logs (e' la
stessa precauzione che prescrive VERIFICA-KAFKA).

Il costo e' SSH, quindi lento: una fotografia si tiene per TTL secondi e si
rinfresca in un thread di sfondo. Chi chiama riceve sempre subito l'ultima
fotografia, con scritto quanto e' vecchia e se ce n'e' una in arrivo.
"""

import json
import subprocess
import threading
import time


# ---------------------------------------------------------------------------
# Le cinque aree, nell'ordine in cui site.yml le installa.
# 'gruppi' sono i gruppi di inventory.ini; 'servizi' le unit systemd che i
# ruoli creano e che quindi devono risultare attive.
# ---------------------------------------------------------------------------
AREE = [
    {
        'chiave': 'bilanciatori',
        'nome': 'Bilanciatori',
        'tipo': 'keepalived · haproxy · nginx',
        'playbook': 'playbooks/10-bilanciatori.yml',
        'gruppi': ['nginx_servers'],
        'servizi': ['keepalived', 'haproxy', 'nginx'],
    },
    {
        'chiave': 'rancher',
        'nome': 'Rancher / RKE2',
        'tipo': 'cluster locale + downstream',
        'playbook': 'playbooks/20-rancher.yml',
        'gruppi': ['masters', 'new_managers', 'workers', 'ingress'],
        # Qui basta UNO dei due: i master e i manager del cluster downstream
        # girano rke2-server, i worker e i nodi ingress rke2-agent. Pretenderli
        # entrambi su ogni nodo dichiarerebbe degradata un'area che sta bene.
        'servizi': ['rke2-server', 'rke2-agent'],
        'modo': 'uno',
    },
    {
        'chiave': 'database',
        'nome': 'Database PostgreSQL',
        'tipo': 'Patroni · etcd',
        'playbook': 'playbooks/30-database.yml',
        'gruppi': ['db_servers'],
        'servizi': ['etcd', 'patroni'],
    },
    {
        'chiave': 'storage',
        'nome': 'Storage',
        'tipo': 'GlusterFS · Ganesha NFS · Pacemaker',
        'playbook': 'playbooks/40-storage.yml',
        'gruppi': ['gluster_servers'],
        'servizi': ['glusterd', 'nfs-ganesha', 'pcsd'],
    },
    {
        'chiave': 'kafka',
        'nome': 'Kafka',
        'tipo': 'KRaft · SASL_SSL',
        'playbook': 'playbooks/50-kafka.yml',
        'gruppi': ['kafka_servers'],
        'servizi': ['kafka'],
    },
]

AREE_PER_CHIAVE = {a['chiave']: a for a in AREE}

# Prologo comune a tutti gli script: le due funzioni che servono ovunque.
#
# svc: 'systemctl is-active' esce diverso da zero quando il servizio non e'
# attivo, e quello e' proprio il caso che interessa. Non si usa '|| echo':
# stamperebbe due righe quando systemctl ha gia' risposto 'inactive'.
#
# disco: findmnt --target risale al mount che CONTIENE il percorso. Se il
# target coincide con il percorso c'e' un disco dedicato, altrimenti sotto c'e'
# il filesystem di sistema — la distinzione che i playbook di database e Kafka
# raccontano a ogni esecuzione.
_PROLOGO = r'''
svc() {
  for s in "$@"; do
    st=$(systemctl is-active "$s" 2>/dev/null)
    [ -n "$st" ] || st=sconosciuto
    echo "SVC $s $st"
  done
}
disco() {
  p="$1"
  if [ ! -e "$p" ]; then echo "DISCO $p|assente||||||"; return; fi
  t=$(findmnt -n -o TARGET --target "$p" 2>/dev/null | head -1)
  s=$(findmnt -n -o SOURCE --target "$p" 2>/dev/null | head -1)
  f=$(findmnt -n -o FSTYPE --target "$p" 2>/dev/null | head -1)
  d=$(df -P -h "$p" 2>/dev/null | tail -1 | awk '{print $2"|"$3"|"$4"|"$5}')
  echo "DISCO $p|$t|$s|$f|$d"
}
echo "IP4 $(ip -o -4 addr show 2>/dev/null | awk '{print $4}' | tr '\n' ' ')"
'''


def _script_bilanciatori(ctx):
    return _PROLOGO + f'''
svc keepalived haproxy nginx
disco /
ss -lnt 2>/dev/null | awk '{{print "PORTA " $4}}' | grep -E ':({ctx["porta_stats"]}|80|443|{ctx["porta_primary"]}|{ctx["porta_standby"]})$' || true
exit 0
'''


def _script_rancher(ctx):
    return _PROLOGO + f'''
svc rke2-server rke2-agent rancher-system-agent
disco {ctx["mount_rancher"]}
KC=/root/.kube/config
[ -r "$KC" ] || KC=/etc/rancher/rke2/rke2.yaml
KB=$(command -v kubectl 2>/dev/null)
[ -n "$KB" ] || KB=/var/lib/rancher/rke2/bin/kubectl
if [ -r "$KC" ] && [ -x "$KB" ]; then
  "$KB" --kubeconfig "$KC" get nodes --no-headers 2>/dev/null | while read -r n st ru ve rest; do
    echo "NODO $n|$st|$ve"
  done
fi
exit 0
'''


def _script_database(ctx):
    return _PROLOGO + f'''
svc etcd patroni
disco {ctx["mount_postgres"]}
echo "PATRONI_INIZIO"
patronictl -c /etc/patroni/patroni.yml list -f json 2>/dev/null || true
echo "PATRONI_FINE"
exit 0
'''


def _script_storage(ctx):
    return _PROLOGO + f'''
svc glusterd nfs-ganesha pcsd corosync pacemaker
disco {ctx["brick_root"]}
gluster volume list 2>/dev/null | while read -r v; do
  st=$(gluster volume info "$v" 2>/dev/null | awk -F': ' '/^Status:/ {{print $2; exit}}')
  echo "VOLUME $v|$st"
done
pcs status resources 2>/dev/null | head -20 | while IFS= read -r r; do echo "PCS $r"; done
exit 0
'''


def _script_kafka(ctx):
    return _PROLOGO + f'''
svc kafka
disco {ctx["kafka_data"]}
echo "QUORUM_INIZIO"
JH=$(grep -oP 'JAVA_HOME=\\K.*' /etc/systemd/system/kafka.service 2>/dev/null | tr -d '"')
BIN=/opt/kafka/bin/kafka-metadata-quorum.sh
CFG=/opt/kafka/client.properties
if [ -n "$JH" ] && [ -x "$BIN" ] && [ -r "$CFG" ]; then
  JAVA_HOME="$JH" PATH="$JH/bin:$PATH" LOG_DIR=/tmp/kafka-cli \
    "$BIN" --bootstrap-server {ctx["kafka_bootstrap"]} --command-config "$CFG" \
    describe --status 2>&1 | head -20
else
  echo "(kafka non installato su questo nodo, oppure JAVA_HOME non leggibile dalla unit)"
fi
echo "QUORUM_FINE"
exit 0
'''


SCRIPT = {
    'bilanciatori': _script_bilanciatori,
    'rancher': _script_rancher,
    'database': _script_database,
    'storage': _script_storage,
    'kafka': _script_kafka,
}


def _json_ansible(testo):
    """Estrae il JSON dal callback 'json' di Ansible.

    I warning ('[WARNING]: ...') escono prima del JSON e lo rendono non
    parsabile tutto intero: si riparte dalla prima graffa aperta.
    """
    if not testo:
        return None
    try:
        return json.loads(testo)
    except json.JSONDecodeError:
        pass
    inizio = testo.find('{')
    fine = testo.rfind('}')
    if inizio < 0 or fine <= inizio:
        return None
    try:
        return json.loads(testo[inizio:fine + 1])
    except json.JSONDecodeError:
        return None


def _righe_host(dati):
    """Da JSON di ansible ad-hoc a {host: {'rc':.., 'stdout':.., 'errore':..}}."""
    esiti = {}
    for play in (dati or {}).get('plays', []) or []:
        for task in play.get('tasks', []) or []:
            for host, esito in (task.get('hosts') or {}).items():
                if esito.get('unreachable'):
                    esiti[host] = {'raggiungibile': False,
                                   'errore': (esito.get('msg') or 'host irraggiungibile')[:300],
                                   'stdout': ''}
                elif esito.get('failed') and not esito.get('stdout'):
                    esiti[host] = {'raggiungibile': True,
                                   'errore': (esito.get('msg') or 'comando fallito')[:300],
                                   'stdout': esito.get('stdout') or ''}
                else:
                    esiti[host] = {'raggiungibile': True, 'errore': None,
                                   'stdout': esito.get('stdout') or ''}
    return esiti


def _analizza(stdout):
    """Trasforma le righe 'CHIAVE valore' dello script in una struttura."""
    servizi, indirizzi, dischi = {}, [], []
    nodi, volumi, pcs, porte = [], [], [], []
    patroni_righe, quorum_righe = [], []
    dentro_patroni = dentro_quorum = False

    for riga in (stdout or '').splitlines():
        riga = riga.rstrip()
        if riga == 'PATRONI_INIZIO':
            dentro_patroni = True
            continue
        if riga == 'PATRONI_FINE':
            dentro_patroni = False
            continue
        if riga == 'QUORUM_INIZIO':
            dentro_quorum = True
            continue
        if riga == 'QUORUM_FINE':
            dentro_quorum = False
            continue
        if dentro_patroni:
            patroni_righe.append(riga)
            continue
        if dentro_quorum:
            quorum_righe.append(riga)
            continue

        if riga.startswith('SVC '):
            pezzi = riga.split()
            if len(pezzi) >= 3:
                servizi[pezzi[1]] = pezzi[2]
        elif riga.startswith('IP4 '):
            indirizzi = [x.split('/')[0] for x in riga[4:].split() if x]
        elif riga.startswith('DISCO '):
            campi = riga[6:].split('|')
            campi += [''] * (8 - len(campi))
            punto, target = campi[0], campi[1]
            dischi.append({
                'punto': punto,
                'presente': target != 'assente',
                'dedicato': bool(target) and target == punto,
                'montato_su': target if target != 'assente' else None,
                'sorgente': campi[2] or None,
                'fs': campi[3] or None,
                'dimensione': campi[4] or None,
                'usato': campi[5] or None,
                'disponibile': campi[6] or None,
                'percentuale': campi[7] or None,
            })
        elif riga.startswith('NODO '):
            campi = riga[5:].split('|')
            campi += [''] * (3 - len(campi))
            nodi.append({'nome': campi[0], 'stato': campi[1], 'versione': campi[2]})
        elif riga.startswith('VOLUME '):
            campi = riga[7:].split('|')
            campi += [''] * (2 - len(campi))
            volumi.append({'nome': campi[0], 'stato': campi[1] or 'sconosciuto'})
        elif riga.startswith('PCS '):
            pcs.append(riga[4:])
        elif riga.startswith('PORTA '):
            porte.append(riga[6:])

    dettagli = {}
    if nodi:
        dettagli['nodi'] = nodi
    if volumi:
        dettagli['volumi'] = volumi
    if pcs:
        dettagli['pcs'] = pcs
    if porte:
        dettagli['porte'] = sorted(set(porte))

    if patroni_righe:
        testo = '\n'.join(patroni_righe).strip()
        if testo:
            try:
                membri = json.loads(testo)
                dettagli['patroni'] = [{
                    'membro': m.get('Member'),
                    'ruolo': m.get('Role'),
                    'stato': m.get('State'),
                    'lag': m.get('Lag in MB'),
                    'timeline': m.get('TL'),
                } for m in membri if isinstance(m, dict)]
            except (json.JSONDecodeError, TypeError):
                dettagli['patroni_grezzo'] = testo[:2000]

    if quorum_righe:
        testo = '\n'.join(r for r in quorum_righe if r.strip())
        if testo:
            dettagli['quorum'] = testo[:2000]
            for riga in quorum_righe:
                if riga.startswith('LeaderId:'):
                    dettagli['quorum_leader'] = riga.split(':', 1)[1].strip()

    return {'servizi': servizi, 'indirizzi': indirizzi, 'dischi': dischi,
            'dettagli': dettagli}


class Raccoglitore:
    """Tiene una fotografia dello stato e la rinfresca in sottofondo."""

    TTL = 20            # secondi: oltre, la fotografia e' da rifare
    TIMEOUT_AREA = 90   # secondi: oltre, l'area si dichiara in timeout

    def __init__(self, repo_dir, costruisci_env, inventario):
        self._repo_dir = repo_dir
        self._costruisci_env = costruisci_env
        self._inv = inventario
        self._lock = threading.Lock()
        self._foto = None
        self._in_corso = False

    # -- API pubblica -------------------------------------------------------

    def fotografia(self, attendi=0.0):
        """Ultima fotografia disponibile; ne avvia una nuova se e' vecchia.

        'attendi' e' il tempo massimo che si sta ad aspettare quando NON c'e'
        ancora nessuna fotografia: al primo caricamento della pagina evita di
        rispondere con un cruscotto vuoto se le sonde sono veloci.
        """
        self._avvia_se_serve()
        if self._foto is None and attendi > 0:
            scadenza = time.time() + attendi
            while self._foto is None and time.time() < scadenza:
                time.sleep(0.1)

        with self._lock:
            foto = dict(self._foto) if self._foto else {
                'aree': [], 'host': {'totale': 0, 'raggiungibili': 0},
                'aggiornato_a': None, 'errore': None,
            }
            foto['in_corso'] = self._in_corso
        return foto

    def invalida(self):
        """Forza il prossimo giro: usata dal pulsante Aggiorna della GUI."""
        with self._lock:
            if self._foto:
                self._foto = dict(self._foto)
                self._foto['aggiornato_a'] = 0
        self._avvia_se_serve()

    # -- interni ------------------------------------------------------------

    def _avvia_se_serve(self):
        with self._lock:
            if self._in_corso:
                return
            fresca = (self._foto and self._foto.get('aggiornato_a')
                      and (time.time() - self._foto['aggiornato_a']) < self.TTL)
            if fresca:
                return
            self._in_corso = True
        threading.Thread(target=self._giro, daemon=True).start()

    def _giro(self):
        try:
            foto = self._raccogli()
        except Exception as exc:                            # pragma: no cover
            foto = {'aree': [], 'host': {'totale': 0, 'raggiungibili': 0},
                    'errore': f'raccolta fallita: {exc}'}
        foto['aggiornato_a'] = time.time()
        with self._lock:
            self._foto = foto
            self._in_corso = False

    def _contesto(self):
        """Valori che gli script hanno bisogno di conoscere.

        Vengono da group_vars via ansible-inventory. I default sono quelli di
        group_vars/all.yml: servono solo quando ansible non e' disponibile e
        l'inventario e' stato letto a mano da inventory.ini.
        """
        v = self._inv.variabile
        host_kafka = self._inv.host_del_gruppo('kafka_servers')
        porta_broker = v('kafka_broker_port', 9092)
        bootstrap = ','.join(f'{self._inv.indirizzo(h)}:{porta_broker}'
                             for h in host_kafka) or f'127.0.0.1:{porta_broker}'
        return {
            'porta_stats': v('haproxy_stats_port', 7000),
            'porta_primary': v('haproxy_primary_port', 5000),
            'porta_standby': v('haproxy_standby_port', 5001),
            'mount_rancher': v('disk_mount_point', '/var/lib/rancher'),
            'mount_postgres': v('postgres_mount_point', '/var/lib/postgresql'),
            'brick_root': v('gluster_brick_root', '/bricks'),
            'kafka_data': v('kafka_data_dir', '/opt/kafka/data'),
            'kafka_bootstrap': bootstrap,
        }

    def _raccogli(self):
        ctx = self._contesto()
        inv = self._inv.carica()
        risultati = {}
        thread = []

        for area in AREE:
            host = []
            for gruppo in area['gruppi']:
                host.extend(inv.get('gruppi', {}).get(gruppo, []))
            if not host:
                risultati[area['chiave']] = {'assente': True, 'esiti': {}}
                continue
            t = threading.Thread(target=self._sonda_area,
                                 args=(area, ctx, risultati), daemon=True)
            t.start()
            thread.append(t)

        for t in thread:
            t.join(timeout=self.TIMEOUT_AREA + 5)

        aree, totale, raggiungibili = [], 0, 0
        for area in AREE:
            grezzo = risultati.get(area['chiave']) or {
                'esiti': {}, 'errore': 'sonda non completata (timeout)'}
            scheda = self._componi_area(area, grezzo, inv)
            totale += len(scheda['host'])
            raggiungibili += sum(1 for h in scheda['host'] if h['raggiungibile'])
            aree.append(scheda)

        return {'aree': aree,
                'host': {'totale': totale, 'raggiungibili': raggiungibili},
                'errore': inv.get('errore')}

    def _sonda_area(self, area, ctx, risultati):
        pattern = ':'.join(area['gruppi'])
        script = SCRIPT[area['chiave']](ctx)
        env = self._costruisci_env()
        env['ANSIBLE_STDOUT_CALLBACK'] = 'json'
        env['ANSIBLE_LOAD_CALLBACK_PLUGINS'] = '1'
        # I fatti non servono a nessuna sonda e costano una connessione in piu'
        # per host: gather_facts qui sarebbe puro tempo perso.
        env['ANSIBLE_GATHERING'] = 'explicit'
        cmd = ['ansible', pattern, '-m', 'shell', '-a', script,
               '-T', '10', '-f', '10']
        try:
            r = subprocess.run(cmd, capture_output=True, text=True,
                               cwd=self._repo_dir, env=env,
                               timeout=self.TIMEOUT_AREA)
            dati = _json_ansible(r.stdout)
            if dati is None:
                errore = ((r.stderr or r.stdout) or 'nessun output').strip()[:300]
                risultati[area['chiave']] = {'esiti': {}, 'errore': errore}
                return
            risultati[area['chiave']] = {'esiti': _righe_host(dati), 'errore': None}
        except FileNotFoundError:
            risultati[area['chiave']] = {'esiti': {},
                                         'errore': 'ansible non trovato nel PATH'}
        except subprocess.TimeoutExpired:
            risultati[area['chiave']] = {
                'esiti': {}, 'errore': f'sonda in timeout (>{self.TIMEOUT_AREA}s)'}
        except Exception as exc:                            # pragma: no cover
            risultati[area['chiave']] = {'esiti': {}, 'errore': str(exc)}

    def _componi_area(self, area, grezzo, inv):
        scheda = {
            'chiave': area['chiave'],
            'nome': area['nome'],
            'tipo': area['tipo'],
            'playbook': area['playbook'],
            'gruppi': area['gruppi'],
            'servizi_attesi': area['servizi'],
            'modo': area.get('modo', 'tutti'),
            'host': [],
            'note': [],
            'errore': grezzo.get('errore'),
            'stato': 'sconosciuto',
        }

        if grezzo.get('assente'):
            scheda['stato'] = 'assente'
            scheda['note'].append(
                'Nessun host in ' + ' / '.join(f'[{g}]' for g in area['gruppi'])
                + ': area non in inventario.')
            return scheda

        nomi = []
        for gruppo in area['gruppi']:
            for h in inv.get('gruppi', {}).get(gruppo, []):
                if h not in nomi:
                    nomi.append(h)

        esiti = grezzo.get('esiti') or {}
        for nome in nomi:
            esito = esiti.get(nome)
            scheda_host = {
                'nome': nome,
                'indirizzo': self._inv.indirizzo(nome),
                'gruppo': next((g for g in area['gruppi']
                                if nome in inv.get('gruppi', {}).get(g, [])), None),
                'raggiungibile': False,
                'errore': (esito or {}).get('errore') or scheda['errore'] or 'nessuna risposta',
                'servizi': {}, 'dischi': [], 'dettagli': {}, 'indirizzi': [],
            }
            if esito and esito.get('raggiungibile'):
                analisi = _analizza(esito.get('stdout'))
                scheda_host.update({
                    'raggiungibile': True,
                    'errore': esito.get('errore'),
                    'servizi': analisi['servizi'],
                    'dischi': analisi['dischi'],
                    'dettagli': analisi['dettagli'],
                    'indirizzi': analisi['indirizzi'],
                })
            scheda['host'].append(scheda_host)

        if scheda['errore']:
            scheda['note'].append(f"Sonda non riuscita: {scheda['errore']}")

        self._decora(area, scheda)
        scheda['stato'] = self._stato_area(area, scheda)
        return scheda

    def _decora(self, area, scheda):
        """Le poche cose che si capiscono solo guardando l'area intera."""
        v = self._inv.variabile

        if area['chiave'] == 'bilanciatori':
            for etichetta, vip in (('console Rancher', v('vip_rancher_ip')),
                                   ('Patroni/PgBouncer', v('vip_patroni_ip'))):
                if not vip:
                    continue
                titolare = next((h['nome'] for h in scheda['host']
                                 if vip in h.get('indirizzi', [])), None)
                scheda['note'].append(
                    f'VIP {etichetta} {vip}: '
                    + (f'su {titolare}' if titolare else 'non assegnato a nessun nodo'))

        if area['chiave'] == 'storage':
            vip = v('nfs_vip_ip')
            if vip:
                titolare = next((h['nome'] for h in scheda['host']
                                 if vip in h.get('indirizzi', [])), None)
                scheda['note'].append(
                    f'VIP NFS {vip}: '
                    + (f'su {titolare}' if titolare else 'non assegnato a nessun nodo'))

        if area['chiave'] == 'database':
            for h in scheda['host']:
                membri = h.get('dettagli', {}).get('patroni')
                if membri:
                    leader = next((m['membro'] for m in membri
                                   if (m.get('ruolo') or '').lower() in ('leader', 'primary')), None)
                    scheda['note'].append(
                        f'Cluster Patroni: {len(membri)} membri, leader '
                        + (leader or 'NON eletto'))
                    scheda['patroni'] = membri
                    break

        if area['chiave'] == 'kafka':
            for h in scheda['host']:
                leader = h.get('dettagli', {}).get('quorum_leader')
                if leader:
                    scheda['note'].append(f'Quorum KRaft: LeaderId {leader}')
                    break

        if area['chiave'] == 'rancher':
            for h in scheda['host']:
                nodi = h.get('dettagli', {}).get('nodi')
                if nodi:
                    pronti = sum(1 for n in nodi if n.get('stato') == 'Ready')
                    scheda['note'].append(
                        f'Cluster RKE2: {pronti}/{len(nodi)} nodi Ready '
                        f'(visto da {h["nome"]})')
                    scheda['nodi'] = nodi
                    break

    @staticmethod
    def _stato_area(area, scheda):
        host = scheda['host']
        if not host:
            return 'assente'
        raggiungibili = [h for h in host if h['raggiungibile']]
        if not raggiungibili:
            # Due cose diverse che non vanno confuse: la sonda e' partita e
            # nessuno ha risposto (i nodi sono giu'), oppure la sonda non e'
            # partita affatto — ansible non installato, timeout, output
            # illeggibile. Nel secondo caso dei nodi non si sa niente, e dire
            # "guasto" sarebbe inventarselo.
            return 'sconosciuto' if scheda.get('errore') else 'guasto'

        # Un servizio conta solo dove systemd lo conosce: 'sconosciuto' vuol
        # dire che quella unit non e' installata su quel nodo, e su un nodo che
        # non ha quel ruolo non e' un guasto — e' il caso normale.
        #
        # modo 'uno': al nodo basta avere attivo uno dei servizi elencati
        # (rke2-server OPPURE rke2-agent). modo 'tutti' (predefinito): li vuole
        # tutti, ed e' quello giusto per bilanciatori, database, storage e Kafka,
        # dove ogni nodo fa girare l'intera terna.
        uno_basta = area.get('modo') == 'uno'
        attivi = mancanti = 0
        for h in raggiungibili:
            noti = {s: st for s, st in (h.get('servizi') or {}).items()
                    if st != 'sconosciuto' and s in area['servizi']}
            if not noti:
                continue
            su = sum(1 for st in noti.values() if st == 'active')
            giu = sum(1 for st in noti.values()
                      if st in ('inactive', 'failed', 'activating', 'deactivating'))
            if uno_basta:
                if su:
                    attivi += 1
                elif giu:
                    mancanti += 1
            else:
                attivi += su
                mancanti += giu

        if attivi == 0 and mancanti == 0:
            return 'assente'
        if mancanti or len(raggiungibili) != len(host):
            return 'degradato'
        return 'sano'
