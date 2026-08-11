"""Ansible Launcher - avvio del software.

Uso:  python main.py
"""

import sys


def main() -> int:
    try:
        from launcher.ui import avvia
    except ModuleNotFoundError as errore:
        mancante = getattr(errore, "name", "") or str(errore)
        print(f"Manca una libreria: {mancante}\n")
        print("Installa le dipendenze con:")
        print("    pip install -r requirements.txt")
        return 1
    return avvia()


if __name__ == "__main__":
    sys.exit(main())
