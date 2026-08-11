#!/bin/bash
# Rimuove RKE2, K3s e l'agent Rancher da un nodo, per riportarlo allo stato
# precedente all'installazione.
#
#   sudo scripts/pulizia-kubernetes.sh          pulisce, NON riavvia
#   sudo RIAVVIA=1 scripts/pulizia-kubernetes.sh   pulisce e riavvia
#
# Derivato dal delete.sh in uso sui server, con quattro correzioni segnalate
# nel punto in cui si applicano.
#
# DISTRUTTIVO: smonta il cluster sul nodo dove viene eseguito.
set -euo pipefail

RIAVVIA="${RIAVVIA:-0}"

echo "=== Fermare e disabilitare i servizi ==="
# CORREZIONE: c'e' anche rancher-system-agent. Sui nodi manager, worker e
# ingress e' il solo servizio presente, e lasciandolo vivo il nodo si
# ripresenta al cluster con la vecchia identita'.
for svc in rancher-system-agent rke2-server rke2-agent k3s k3s-agent; do
    if systemctl is-active --quiet "$svc" 2>/dev/null; then
        systemctl stop "$svc"
        echo "  fermato $svc"
    fi
    if systemctl is-enabled --quiet "$svc" 2>/dev/null; then
        systemctl disable "$svc"
        echo "  disabilitato $svc"
    fi
done

# CORREZIONE: gli script ufficiali vanno PRIMA della rimozione manuale, perche'
# fermano containerd e kubelet e smontano i filesystem di rke2. Dopo aver
# cancellato i binari non esistono piu'. rke2-killall.sh per primo: fermare
# l'unit non basta, il supervisor sopravvive e tiene la porta 9345.
echo "=== Script di uninstall ufficiali, se presenti ==="
for s in /usr/local/bin/rke2-killall.sh \
         /usr/local/bin/rancher-system-agent-uninstall.sh \
         /usr/local/bin/rke2-uninstall.sh \
         /usr/local/bin/rke2-agent-uninstall.sh; do
    if [ -x "$s" ]; then
        echo "  eseguo $s"
        "$s" || true
    fi
done

echo "=== Uccidere eventuali processi residui ==="
pkill -f rke2 || true
pkill -f k3s || true

echo "=== Rimuovere file e directory ==="
# NOTA: si rimuovono le sottodirectory di /var/lib/rancher, mai la directory
# stessa: e' il mount point del disco dati.
#
# CORREZIONE: c'era /var/lib/cni e /opt/cni ma NON /etc/cni/net.d. E' proprio
# quella la directory che fa danni: un conflist di un CNI non piu' installato
# vince per ordine alfabetico su quello nuovo e blocca ogni pod in
# ContainerCreating con 'failed to find plugin ... in path [/opt/cni/bin]'.
for path in \
    /usr/local/bin/rke2* \
    /usr/local/bin/k3s* \
    /usr/local/bin/kubectl \
    /usr/local/bin/helm \
    /usr/local/bin/rancher-system-agent \
    /etc/rancher/rke2 \
    /etc/rancher/k3s \
    /etc/rancher/agent \
    /var/lib/rancher/rke2 \
    /var/lib/rancher/k3s \
    /var/lib/rancher/agent \
    /var/lib/kubelet \
    /etc/cni/net.d \
    /var/lib/cni \
    /opt/cni \
    "$HOME/.kube" \
    /etc/systemd/system/rke2* \
    /etc/systemd/system/k3s* \
    /etc/systemd/system/rancher-system-agent.service \
    /etc/systemd/system/rancher-system-agent.env \
    /usr/local/lib/systemd/system/rke2* \
    /etc/init.d/rke2; do
    rm -rf $path || true
done

systemctl daemon-reload

echo "=== Smontare filesystem residui ==="
for mount_point in /run/k3s /var/lib/rancher/k3s /var/lib/kubelet; do
    umount -l "$mount_point" 2>/dev/null || true
done

echo "=== Rimuovere le interfacce virtuali CNI ==="
for iface in cni0 flannel.1 vxlan.calico; do
    ip link delete "$iface" 2>/dev/null || true
done

# ATTENZIONE: questo azzera TUTTE le regole, non solo quelle di Kubernetes.
# Su questi nodi va bene perche' ufw e' disattivato da host_prep e nessun altro
# servizio scrive regole, ma su una macchina con un firewall configurato a mano
# le regole vanno perse. Aggiunta la tabella mangle, che kube-proxy usa e che il
# delete.sh originale non toccava.
echo "=== Pulire iptables (filter, nat, mangle) ==="
for tabella in filter nat mangle; do
    iptables -t "$tabella" -F || true
    iptables -t "$tabella" -X || true
done
if command -v ip6tables >/dev/null 2>&1; then
    for tabella in filter nat mangle; do
        ip6tables -t "$tabella" -F || true
        ip6tables -t "$tabella" -X || true
    done
fi

# NOTA: il delete.sh originale finiva con un blocco "helm uninstall cert-manager"
# e "kubectl delete namespace cert-manager". Non veniva mai eseguito, perche' i
# binari helm e kubectl erano gia' stati cancellati poche righe sopra e i due
# "command -v" fallivano. Non e' stato reintrodotto: disinstallare un chart da
# un cluster che si sta smontando non ha effetto, e i suoi oggetti se ne vanno
# insieme a /var/lib/rancher/rke2.

echo "=== Stato finale ==="
echo -n "  servizi residui: "
systemctl list-units --all --type=service --no-legend 2>/dev/null \
    | grep -icE 'rke2|k3s|rancher-system-agent' || true
echo -n "  porte 9345/6443 occupate: "
ss -lntp 2>/dev/null | grep -cE ':(9345|6443)\b' || true

if [ "$RIAVVIA" = "1" ]; then
    echo "=== Riavvio ==="
    sync
    reboot
else
    echo "=== Fatto. Nessun riavvio: rilancia con RIAVVIA=1 se lo vuoi ==="
fi
