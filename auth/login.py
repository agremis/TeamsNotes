"""Reautenticação interativa (device code flow).

Uso:
  python -m auth.login

O pipeline (run_nightly) NUNCA abre device flow: ele roda pelo Agendador, sem
ninguém para digitar o código. Quando o cache MSAL não consegue mais renovar
sozinho, ele falha com AuthRequired e pede para você rodar isto aqui.
"""

import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from auth.token_manager import login

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")


def main() -> None:
    try:
        login()
    except Exception as e:
        print(f"\nFalha na autenticacao: {e}", flush=True)
        sys.exit(1)
    print("Token renovado. O pipeline volta a rodar sem interacao.", flush=True)


if __name__ == "__main__":
    main()
