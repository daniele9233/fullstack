"""Verifica della composizione dei comandi.

Si esegue con:  python -m pytest tests/ -q
oppure senza pytest:  python tests/test_commands.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from launcher.commands import (  # noqa: E402
    PLAYBOOK_PREDEFINITI,
    Opzioni,
    Playbook,
    anteprima_comando,
    comando_remoto,
    costruisci_argomenti,
)


def test_la_verifica_e_il_primo_playbook_dell_elenco():
    """La verifica va lanciata prima dell'installazione, e l'elenco lo dice.

    E' il playbook che guarda le macchine senza toccarle: se comparisse in
    fondo, dopo "Deploy completo", l'ordine dell'interfaccia suggerirebbe di
    installare per primo.
    """
    primo = PLAYBOOK_PREDEFINITI[0]
    assert primo.percorso == "playbooks/00-verifica.yml"
    assert "Deploy completo" == PLAYBOOK_PREDEFINITI[1].nome


def test_comando_minimo():
    pb = Playbook("Database", "playbooks/30-database.yml", "inventory.ini")
    assert costruisci_argomenti(pb, Opzioni()) == [
        "ansible-playbook",
        "-i",
        "inventory.ini",
        "playbooks/30-database.yml",
    ]


def test_ogni_playbook_usa_il_proprio_inventario():
    pb = Playbook("Storage", "playbooks/40-storage.yml", "inventari/storage.ini")
    assert "inventari/storage.ini" in anteprima_comando(pb, Opzioni())


def test_simulazione():
    pb = Playbook("Deploy", "site.yml")
    riga = anteprima_comando(pb, Opzioni(check=True, diff=True))
    assert "--check" in riga and "--diff" in riga


def test_limit_tags_ed_extra_vars():
    pb = Playbook("Deploy", "site.yml")
    opz = Opzioni(
        limit="db_servers",
        tags="certs",
        skip_tags="lento",
        extra_vars=["postgres_force_reinit=true", "etcd_client_port=2479"],
    )
    riga = anteprima_comando(pb, opz)
    assert "--limit db_servers" in riga
    assert "--tags certs" in riga
    assert "--skip-tags lento" in riga
    assert "-e postgres_force_reinit=true" in riga
    assert "-e etcd_client_port=2479" in riga


def test_verbosita():
    pb = Playbook("Deploy", "site.yml")
    assert anteprima_comando(pb, Opzioni(verbosita=0)).count(" -v") == 0
    assert anteprima_comando(pb, Opzioni(verbosita=3)).endswith("-vvv")
    # Ansible non va oltre -vvvv: la verbosita' viene tagliata li'.
    assert anteprima_comando(pb, Opzioni(verbosita=9)).endswith("-vvvv")


def test_chiave_dei_nodi():
    pb = Playbook("Deploy", "site.yml")
    riga = anteprima_comando(pb, Opzioni(chiave_nodi="/root/.ssh/DeliveryAutomation.pem"))
    assert "--private-key /root/.ssh/DeliveryAutomation.pem" in riga


def test_input_pericoloso_viene_neutralizzato():
    """I campi liberi non devono poter aggiungere comandi.

    Senza quoting, un --limit come 'db; rm -rf /' diventerebbe un secondo
    comando eseguito sul controller.
    """
    pb = Playbook("Deploy", "site.yml")
    riga = anteprima_comando(pb, Opzioni(limit="db_servers; rm -rf /"))
    assert "; rm -rf /" not in riga.replace("'db_servers; rm -rf /'", "")
    assert "'db_servers; rm -rf /'" in riga


def test_percorsi_con_spazi():
    pb = Playbook("Deploy", "playbooks/deploy prod.yml", "inventari/prod cluster.ini")
    riga = anteprima_comando(pb, Opzioni())
    assert "'playbooks/deploy prod.yml'" in riga
    assert "'inventari/prod cluster.ini'" in riga


def test_comando_remoto_senza_venv():
    pb = Playbook("Database", "playbooks/30-database.yml")
    cmd = comando_remoto(pb, Opzioni(), "/root/kikkoBetterThanAI/fullstack")
    assert cmd.startswith("cd /root/kikkoBetterThanAI/fullstack &&")
    assert "export ANSIBLE_FORCE_COLOR=1" in cmd
    assert "export PYTHONUNBUFFERED=1" in cmd
    assert "source" not in cmd
    assert cmd.endswith("exec ansible-playbook -i inventory.ini playbooks/30-database.yml")


def test_comando_remoto_con_venv():
    pb = Playbook("Database", "playbooks/30-database.yml")
    cmd = comando_remoto(pb, Opzioni(), "/root/fullstack", venv="~/ansible-env")
    assert "source " in cmd and "ansible-env/bin/activate" in cmd


def test_cartella_con_spazi_viene_quotata():
    pb = Playbook("Deploy", "site.yml")
    cmd = comando_remoto(pb, Opzioni(), "/root/mia cartella/fullstack")
    assert "cd '/root/mia cartella/fullstack'" in cmd


def test_venv_con_tilde_si_espande():
    """La tilde deve restare fuori dagli apici, altrimenti bash non la espande.

    shlex.quote produrrebbe '~/ansible-env/bin/activate' e la shell cercherebbe
    una cartella chiamata proprio "~": il virtualenv non verrebbe attivato.
    """
    pb = Playbook("Deploy", "site.yml")
    cmd = comando_remoto(pb, Opzioni(), "/root/fullstack", venv="~/ansible-env")
    assert 'source "$HOME"/ansible-env/bin/activate' in cmd
    assert "'~/" not in cmd


def test_venv_con_percorso_assoluto():
    pb = Playbook("Deploy", "site.yml")
    cmd = comando_remoto(pb, Opzioni(), "/root/fullstack", venv="/opt/ansible-env")
    assert "source /opt/ansible-env/bin/activate" in cmd


def test_venv_con_tilde_e_spazi():
    """Tilde espansa E resto quotato: le due cose devono convivere."""
    pb = Playbook("Deploy", "site.yml")
    cmd = comando_remoto(pb, Opzioni(), "/root/fullstack", venv="~/mio env")
    assert 'source "$HOME"/\'mio env/bin/activate\'' in cmd


def _esegui():
    globali = dict(globals())
    falliti = 0
    eseguiti = 0
    for nome, funzione in sorted(globali.items()):
        if nome.startswith("test_") and callable(funzione):
            eseguiti += 1
            try:
                funzione()
                print(f"  ok   {nome}")
            except AssertionError as errore:
                falliti += 1
                print(f"  FALLITO {nome}: {errore}")
    print(f"\n{eseguiti - falliti}/{eseguiti} verifiche superate")
    return 1 if falliti else 0


if __name__ == "__main__":
    raise SystemExit(_esegui())
