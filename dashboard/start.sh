#!/bin/bash
# Avvia la dashboard di fullstack su 127.0.0.1:8080.
#
# Ansible di sistema: non serve nessun virtualenv, e' il caso normale su questo
# repository (README: ansible core 2.18.8 su /usr/bin/python3).
# Ansible in un virtualenv: esportalo prima di lanciare,
#   ANSIBLE_VENV=~/ansible-env ./start.sh
#
# Porta gia' occupata? Cambiala:
#   DASHBOARD_PORT=8081 ./start.sh
#
# La porta NON va esposta sulla rete: la dashboard non ha autenticazione e
# lancia playbook. Da un'altra macchina ci si arriva con un tunnel SSH:
#   ssh -L 8080:localhost:8080 <utente>@<control node>
set -e

cd "$(dirname "$(realpath "$0")")"

PORTA="${DASHBOARD_PORT:-8080}"

if [ -n "${ANSIBLE_VENV:-}" ] && [ -d "$ANSIBLE_VENV" ]; then
  # shellcheck disable=SC1091
  source "$ANSIBLE_VENV/bin/activate"
  echo "  virtualenv:     $ANSIBLE_VENV"
fi

if ! python3 -c 'import flask' 2>/dev/null; then
  echo "  Flask non installato. Su Ubuntu 24.04:"
  echo "      sudo apt install -y python3-flask"
  echo "  altrove:"
  echo "      pip install -r requirements.txt"
  exit 1
fi

if ! command -v ansible-playbook >/dev/null 2>&1; then
  echo "  ATTENZIONE: ansible-playbook non e' nel PATH."
  echo "  La dashboard parte lo stesso, ma non potra' lanciare niente"
  echo "  ne' leggere lo stato dei nodi."
fi

# Chi tiene la porta, prima di sbatterci contro. Senza questo controllo Flask
# dice solo "Address already in use", che non basta a capire se il colpevole
# e' un'altra copia di questa dashboard o qualcos'altro.
#
# La prova si fa con python3 e non con ss: python3 c'e' per forza (la dashboard
# e' scritta in python), ss e lsof no. Quelli servono solo, se ci sono, a dire
# CHI tiene la porta.
if ! python3 -c "
import socket, sys
s = socket.socket()
s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
try:
    s.bind(('127.0.0.1', $PORTA))
except OSError:
    sys.exit(1)
finally:
    s.close()
" 2>/dev/null; then
  echo ""
  echo "  La porta $PORTA e' gia' occupata. Chi la tiene:"
  if command -v ss >/dev/null 2>&1; then
    ss -lntp "sport = :$PORTA" 2>/dev/null | tail -n +2 | sed 's/^/      /'
  elif command -v lsof >/dev/null 2>&1; then
    lsof -nP -iTCP:"$PORTA" -sTCP:LISTEN 2>/dev/null | tail -n +2 | sed 's/^/      /'
  else
    echo "      (ne' ss ne' lsof disponibili per dirlo)"
  fi
  echo ""
  echo "  Se e' un'altra copia di questa dashboard, fermala:"
  echo "      pkill -f 'python3 app.py'"
  echo "      # oppure, se gira come demone:  sudo systemctl stop fullstack-dashboard"
  echo "  Altrimenti scegli un'altra porta:"
  echo "      DASHBOARD_PORT=8081 ./start.sh"
  echo ""
  exit 1
fi

echo ""
echo "  Dashboard fullstack"
echo "  ─────────────────────────────────────────────"
echo "  Repository:     $(cd .. && pwd)"
echo "  URL locale:     http://localhost:$PORTA/#/cruscotto"
echo ""
echo "  Da un'altra macchina, tunnel SSH:"
echo "  ssh -L $PORTA:localhost:$PORTA <utente>@<control node>"
echo "  ─────────────────────────────────────────────"
echo ""

exec python3 app.py
