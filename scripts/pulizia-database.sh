#!/bin/bash
# Rimuove PostgreSQL, Patroni, etcd e PgBouncer da un nodo database, per
# riportarlo allo stato precedente all'installazione.
#
#   sudo scripts/pulizia-database.sh
#
# DISTRUTTIVO: cancella i dati del database e il datastore etcd.
#
# NON tocca l'etcd interno di RKE2, che sta in /var/lib/rancher/rke2/server/db
# ed e' compilato dentro al binario rke2: qui si rimuove solo il pacchetto etcd
# installato dal ruolo postgresql_cluster.
set -euo pipefail

echo "=== Fermare e disabilitare i servizi ==="
systemctl stop    patroni etcd postgresql pgbouncer 2>/dev/null || true
systemctl disable patroni etcd postgresql pgbouncer 2>/dev/null || true

# pg_dropcluster PRIMA di rimuovere i pacchetti: dopo, il comando non esiste
# piu'. E' lo strumento di postgresql-common, toglie anche la registrazione in
# /etc/postgresql e si rifiuta di agire su un cluster in esecuzione.
echo "=== Eliminare i cluster PostgreSQL registrati ==="
if command -v pg_lsclusters >/dev/null 2>&1; then
    pg_lsclusters -h 2>/dev/null | awk '{print $1, $2}' | while read -r v n; do
        [ -n "$v" ] && pg_dropcluster --stop "$v" "$n" || true
    done
fi

echo "=== Rimuovere i pacchetti ==="
apt-get purge -y \
    'percona-.*' etcd postgresql-common postgresql-client-common 2>/dev/null || true
apt-get autoremove -y --purge

echo "=== Rimuovere dati e configurazione ==="
rm -rf /var/lib/postgresql /var/lib/etcd \
       /etc/postgresql /etc/patroni /etc/etcd /etc/pgbouncer \
       /var/log/postgresql
# Il drop-in che fa leggere a etcd il file di configurazione del ruolo.
rm -rf /etc/systemd/system/etcd.service.d
rm -f  /tmp/pgpass0
systemctl daemon-reload

# Il glob senza .list e' voluto: percona-release recente scrive in deb822
# (.sources), e lasciarne indietro uno fa saltare il setup del repo alla
# prossima installazione.
echo "=== Rimuovere il repository Percona ==="
apt-get purge -y percona-release 2>/dev/null || true
rm -f /etc/apt/sources.list.d/percona-* /tmp/percona-release.deb
apt-get update

echo "=== Stato finale ==="
echo -n "  pacchetti percona/etcd residui: "
dpkg -l 2>/dev/null | grep -cE '^ii +(percona|etcd)' || true
echo -n "  porte 5432/8008/6432 occupate: "
ss -lntp 2>/dev/null | grep -cE ':(5432|8008|6432)\b' || true
echo "  (2379/2380 restano occupate se il nodo e' anche un server RKE2: e' il suo etcd)"
echo "=== Fatto ==="
