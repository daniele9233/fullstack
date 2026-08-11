#!/bin/bash
# Rimuove nginx, haproxy e keepalived da un bilanciatore, per riportarlo allo
# stato precedente all'installazione.
#
#   sudo scripts/pulizia-bilanciatori.sh
#
# DISTRUTTIVO: il VIP smette di essere annunciato e il traffico verso Rancher e
# verso Patroni si ferma. Su una coppia in HA va fatto un nodo alla volta, se si
# vuole tenere il servizio in piedi.
set -euo pipefail

# keepalived per primo: fermandolo il nodo rilascia il VIP e l'altro
# bilanciatore se lo prende. Fermando prima nginx si otterrebbe lo stesso
# effetto tramite l'abbassamento di priorita', ma con qualche secondo di
# finestra in cui il VIP e' ancora qui e il servizio dietro non c'e' piu'.
echo "=== Fermare e disabilitare i servizi ==="
systemctl stop    keepalived nginx haproxy 2>/dev/null || true
systemctl disable keepalived nginx haproxy 2>/dev/null || true

echo "=== Rimuovere i pacchetti ==="
# netcat-openbsd resta: e' un'utilita' generica, la installa keepalived_install
# solo perche' serve agli script di controllo, ma puo' servire ad altro.
apt-get purge -y keepalived nginx nginx-common haproxy 2>/dev/null || true
apt-get autoremove -y --purge

echo "=== Rimuovere la configurazione ==="
rm -rf /etc/keepalived \
       /etc/nginx \
       /etc/haproxy \
       /var/lib/haproxy \
       /var/log/nginx
systemctl daemon-reload

# keepalived_install imposta net.ipv4.ip_nonlocal_bind=1, che il modulo sysctl
# di Ansible scrive in /etc/sysctl.conf. Senza rimuoverlo il nodo resterebbe
# con un'impostazione di rete che nessuno usa piu'.
echo "=== Ripristinare net.ipv4.ip_nonlocal_bind ==="
sed -i '/^net\.ipv4\.ip_nonlocal_bind/d' /etc/sysctl.conf 2>/dev/null || true
sysctl -w net.ipv4.ip_nonlocal_bind=0 >/dev/null 2>&1 || true

echo "=== Stato finale ==="
echo -n "  pacchetti residui: "
dpkg -l 2>/dev/null | grep -cE '^ii +(nginx|haproxy|keepalived)' || true
echo -n "  porte 80/443/5000/5001/7000 occupate: "
ss -lntp 2>/dev/null | grep -cE ':(80|443|5000|5001|7000)\b' || true
echo -n "  VIP ancora assegnati a questo nodo: "
ip -br addr 2>/dev/null | grep -c 'secondary' || true
echo "=== Fatto ==="
