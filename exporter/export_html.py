"""Entry point — gera o arquivo HTML das conversas a partir do SQLite.

Cada página é o histórico completo do chat (acumula entre execuções). Normalmente
o export já roda junto com o pipeline; use este comando para regenerar avulso.

Uso:
  python exporter/export_html.py                     # regenera todos os chats
  python exporter/export_html.py --chat 19:xxxx@thread.v2
"""

import argparse
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from exporter.html_exporter import export

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser(description="Exporta as conversas para HTML navegável")
    parser.add_argument("--chat", help="Regenera apenas este chat_id (default: todos)")
    args = parser.parse_args()

    n = export(chat_ids=[args.chat] if args.chat else None)
    logger.info("Concluido: %d chats regenerados.", n)


if __name__ == "__main__":
    main()
