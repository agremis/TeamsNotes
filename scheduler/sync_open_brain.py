"""Sincroniza manualmente um briefing .md específico com o Open Brain.

Uso:
  python scheduler/sync_open_brain.py --file briefings/daily/2026-08-03.md

Para rodar retroativamente sobre um dia antigo, aponte --file para o arquivo
correspondente em daily/. Não varre diretórios sozinho — sempre um arquivo
por vez, para não reprocessar o histórico inteiro sem querer.
"""

import argparse
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from openbrain.sync import sync_file

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)


def main():
    parser = argparse.ArgumentParser(description="Sincroniza um briefing .md com o Open Brain")
    parser.add_argument("--file", required=True, help="Caminho do arquivo .md a sincronizar")
    args = parser.parse_args()

    if not os.path.isfile(args.file):
        parser.error(f"Arquivo não encontrado: {args.file}")

    ok, fail = sync_file(args.file)
    print(f"Open Brain sync: {ok} seção(ões) capturada(s), {fail} falha(s).")
    if fail and not ok:
        sys.exit(1)


if __name__ == "__main__":
    main()
