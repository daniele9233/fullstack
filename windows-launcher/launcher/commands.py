"""Composizione delle righe di comando ansible-playbook.

Logica pura, senza SSH e senza interfaccia: e' la parte che decide COSA verra'
eseguito sul controller, ed e' separata apposta per poterla verificare da sola.

Ogni valore che arriva dall'utente passa da shlex.quote prima di finire nella
riga di comando: --limit, --tags e le extra vars sono campi di testo liberi, e
senza quoting un apice o un punto e virgola cambierebbero il comando eseguito.
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass, field


@dataclass
class Playbook:
    """Una voce dell'elenco: un playbook con il SUO inventario."""

    nome: str
    percorso: str
    inventario: str = "inventory.ini"
    descrizione: str = ""

    def to_dict(self) -> dict:
        return {
            "nome": self.nome,
            "percorso": self.percorso,
            "inventario": self.inventario,
            "descrizione": self.descrizione,
        }

    @staticmethod
    def from_dict(d: dict) -> "Playbook":
        return Playbook(
            nome=d.get("nome", ""),
            percorso=d.get("percorso", ""),
            inventario=d.get("inventario", "inventory.ini"),
            descrizione=d.get("descrizione", ""),
        )


@dataclass
class Opzioni:
    """Interruttori dell'esecuzione, quelli che in Semaphore sono i "task params"."""

    check: bool = False          # --check, simulazione senza modifiche
    diff: bool = False           # --diff, mostra le differenze
    limit: str = ""              # --limit, restringe agli host indicati
    tags: str = ""               # --tags
    skip_tags: str = ""          # --skip-tags
    extra_vars: list[str] = field(default_factory=list)   # -e chiave=valore
    verbosita: int = 0           # 0..4  ->  -v -vv -vvv -vvvv
    forks: int = 0               # --forks, 0 = default di Ansible
    chiave_nodi: str = ""        # --private-key, percorso SUL CONTROLLER
    diventa_root: bool = False   # --become


# I playbook del repository, con il rispettivo inventario. E' l'elenco che
# compare al primo avvio: dall'interfaccia si aggiunge, si modifica e si toglie.
PLAYBOOK_PREDEFINITI: list[Playbook] = [
    Playbook(
        nome="Deploy completo",
        percorso="site.yml",
        inventario="inventory.ini",
        descrizione="Tutte e quattro le aree nell'ordine corretto: bilanciatori, "
                    "Rancher, database, storage.",
    ),
    Playbook(
        nome="Bilanciatori",
        percorso="playbooks/10-bilanciatori.yml",
        inventario="inventory.ini",
        descrizione="keepalived, haproxy e nginx sul gruppo [nginx_servers]. "
                    "Gira un nodo alla volta.",
    ),
    Playbook(
        nome="Cluster Rancher e downstream",
        percorso="playbooks/20-rancher.yml",
        inventario="inventory.ini",
        descrizione="RKE2 sui master, Traefik, Rancher via Helm e creazione del "
                    "cluster downstream.",
    ),
    Playbook(
        nome="Database PostgreSQL",
        percorso="playbooks/30-database.yml",
        inventario="inventory.ini",
        descrizione="PostgreSQL con Patroni ed etcd sul gruppo [db_servers].",
    ),
    Playbook(
        nome="Storage GlusterFS",
        percorso="playbooks/40-storage.yml",
        inventario="inventory.ini",
        descrizione="GlusterFS, Ganesha NFS e Pacemaker sul gruppo "
                    "[gluster_servers].",
    ),
]


def _percorso_shell(percorso: str) -> str:
    """Quota un percorso lasciando espandere la tilde iniziale.

    shlex.quote produrrebbe '~/ansible-env/bin/activate', e la shell non espande
    la tilde dentro agli apici: il virtualenv non verrebbe attivato e nessuno
    capirebbe perche'. Si sostituisce con $HOME, che invece si espande anche
    quando il resto del percorso e' quotato.
    """
    if percorso.startswith("~/"):
        return '"$HOME"/' + shlex.quote(percorso[2:])
    return shlex.quote(percorso)


def costruisci_argomenti(playbook: Playbook, opzioni: Opzioni) -> list[str]:
    """Restituisce ansible-playbook e i suoi argomenti, gia' separati."""
    argomenti = ["ansible-playbook", "-i", playbook.inventario, playbook.percorso]

    if opzioni.check:
        argomenti.append("--check")
    if opzioni.diff:
        argomenti.append("--diff")
    if opzioni.diventa_root:
        argomenti.append("--become")

    if opzioni.limit.strip():
        argomenti += ["--limit", opzioni.limit.strip()]
    if opzioni.tags.strip():
        argomenti += ["--tags", opzioni.tags.strip()]
    if opzioni.skip_tags.strip():
        argomenti += ["--skip-tags", opzioni.skip_tags.strip()]

    for coppia in opzioni.extra_vars:
        if coppia.strip():
            argomenti += ["-e", coppia.strip()]

    if opzioni.chiave_nodi.strip():
        argomenti += ["--private-key", opzioni.chiave_nodi.strip()]

    if opzioni.forks and opzioni.forks > 0:
        argomenti += ["--forks", str(opzioni.forks)]

    if opzioni.verbosita > 0:
        argomenti.append("-" + "v" * min(opzioni.verbosita, 4))

    return argomenti


def anteprima_comando(playbook: Playbook, opzioni: Opzioni) -> str:
    """La riga di comando come la vedresti scritta a mano, per l'anteprima."""
    return " ".join(shlex.quote(a) for a in costruisci_argomenti(playbook, opzioni))


def comando_remoto(
    playbook: Playbook,
    opzioni: Opzioni,
    cartella_ansible: str,
    venv: str = "",
) -> str:
    """Il comando completo da mandare in esecuzione sul controller via SSH.

    Comprende lo spostamento nella cartella del repository e, se indicato,
    l'attivazione del virtualenv. ANSIBLE_FORCE_COLOR tiene i colori anche
    quando l'output non finisce su un terminale, PYTHONUNBUFFERED evita che
    l'output arrivi a blocchi invece che riga per riga.
    """
    pezzi = [
        f"cd {shlex.quote(cartella_ansible)}",
        "export ANSIBLE_FORCE_COLOR=1",
        "export PYTHONUNBUFFERED=1",
    ]

    if venv.strip():
        pezzi.append(f"source {_percorso_shell(venv.strip().rstrip('/') + '/bin/activate')}")

    pezzi.append("exec " + anteprima_comando(playbook, opzioni))
    return " && ".join(pezzi)
