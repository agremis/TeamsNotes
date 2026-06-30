"""Reinicia o experimento: faz backup do banco atual e deixa o caminho livre.

O banco NÃO é apagado — é renomeado para messages_backup_<timestamp>.db,
preservando o trabalho anterior para comparação. (Nome neutro de propósito: o
provider configurado no momento do reset não é necessariamente o que gerou os
dados guardados.) A próxima execução do pipeline recria um banco limpo via
create_tables().

Uso:
  python scheduler/reset_db.py
"""

import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    db = config.DB_PATH

    if not os.path.exists(db):
        print(f"Nenhum banco em {db} — nada a fazer (será criado limpo na próxima execução).")
        return

    base, ext = os.path.splitext(db)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = f"{base}_backup_{stamp}{ext}"

    # Inclui os arquivos auxiliares do WAL, se existirem.
    for suffix in ("", "-wal", "-shm"):
        src = db + suffix
        if os.path.exists(src):
            os.rename(src, backup + suffix)

    print(f"Backup criado: {backup}")
    print("Banco limpo será recriado na próxima execução do pipeline.")


if __name__ == "__main__":
    main()
