#!/bin/bash
# Avvia la dashboard di fullstack su 127.0.0.1:8080.
#
# Ansible di sistema: non serve nessun virtualenv, e' il caso normale su questo
# repository (README: ansible core 2.18.8 su /usr/bin/python3).
# Ansible in un virtualenv: esportalo prima di lanciare,
#   ANSIBLE_VENV=~/ansible-env ./start.sh
#
# La porta NON va esposta sulla rete: la dashboard non ha autenticazione e
# lancia playbook. Da un'altra macchina ci si arriva con un tunnel SSH:
#   ssh -L 8080:localhost:8080 <utente>@<control node>
set -e

cd "$(dirname "$(realpath "$0")")"

if [ -n "${ANSIBLE_VENV:-}" ] && [ -d "$ANSIBLE_VENV" ]; then
  # shellcheck disable=SC1091
  source "$ANSIBLE_VENV/bin/activate"
  echo "  virtualenv:     $ANSIBLE_VENV"
fi

if ! python3 -c 'import flask' 2>/dev/null; then
  echo "  Flask non installato: pip install -r requirements.txt"
  exit 1
fi

if ! command -v ansible-playbook >/dev/null 2>&1; then
  echo "  ATTENZIONE: ansible-playbook non e' nel PATH."
  echo "  La dashboard parte lo stesso, ma non potra' lanciare niente"
  echo "  ne' leggere lo stato dei nodi."
fi

echo ""
echo "  Dashboard fullstack"
echo "  ─────────────────────────────────────────────"
echo "  Repository:     $(cd .. && pwd)"
echo "  URL locale:     http://localhost:8080/#/cruscotto"
echo ""
echo "  Da un'altra macchina, tunnel SSH:"
echo "  ssh -L 8080:localhost:8080 <utente>@<control node>"
echo "  ─────────────────────────────────────────────"
echo ""

exec python3 app.py
