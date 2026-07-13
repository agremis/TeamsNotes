"""Camada de acesso ao SQLite — mensagens, cursores e itens classificados."""

import sqlite3
from datetime import datetime
from contextlib import contextmanager

import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY,
    chat_id TEXT NOT NULL,
    chat_name TEXT,
    author TEXT,
    body TEXT,
    raw_html TEXT,
    created_at DATETIME,
    extracted_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    processed INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS read_cursors (
    chat_id TEXT PRIMARY KEY,
    last_read_at DATETIME
);

CREATE TABLE IF NOT EXISTS classified_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id TEXT,
    chat_name TEXT,
    category TEXT,
    content_clean TEXT,
    author TEXT,
    occurred_at DATETIME,
    briefing_date DATE,
    engine TEXT
);
"""

# Colunas adicionadas após a criação inicial do schema. Migração idempotente
# para bancos antigos reaproveitados (banco novo já nasce com elas).
_MIGRATIONS = {
    "messages": [("raw_html", "TEXT")],
    "classified_items": [("engine", "TEXT")],
}


def _active_engine() -> str:
    """Identificador provider/model da engine LLM ativa (para teste comparativo)."""
    if config.LLM_PROVIDER == "gemini":
        return f"gemini/{config.GEMINI_MODEL}"
    return f"anthropic/{config.ANTHROPIC_MODEL}"


@contextmanager
def get_connection():
    # timeout/busy_timeout: com workers paralelos, escritas concorrentes esperam
    # o lock em vez de falharem com "database is locked". WAL permite leituras
    # simultâneas a uma escrita. Cada thread cria/fecha sua própria conexão.
    conn = sqlite3.connect(config.DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def create_tables() -> None:
    with get_connection() as conn:
        conn.executescript(SCHEMA)
        _apply_migrations(conn)


def _apply_migrations(conn) -> None:
    """Adiciona colunas faltantes em bancos pré-existentes (idempotente)."""
    for table, columns in _MIGRATIONS.items():
        existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        for name, coltype in columns:
            if name not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {coltype}")


# --- Messages ---

def save_messages(messages: list[dict], chat_id: str, chat_name: str) -> None:
    with get_connection() as conn:
        for msg in messages:
            conn.execute(
                """INSERT INTO messages
                       (id, chat_id, chat_name, author, body, raw_html, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET
                       body = excluded.body,
                       raw_html = excluded.raw_html,
                       chat_name = excluded.chat_name""",
                (
                    msg["id"], chat_id, chat_name, msg["author"],
                    msg["body"], msg.get("raw_html", ""), msg["created_at"],
                ),
            )


def get_unprocessed(target_date: str | None = None) -> list[dict]:
    with get_connection() as conn:
        if target_date:
            rows = conn.execute(
                """SELECT * FROM messages
                   WHERE processed = 0 AND DATE(created_at) = ?
                   ORDER BY created_at""",
                (target_date,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM messages WHERE processed = 0 ORDER BY created_at"
            ).fetchall()
        return [dict(r) for r in rows]


def get_pending_dates(limit: int | None = None, newest_first: bool = True) -> list[str]:
    """Datas (YYYY-MM-DD) que ainda têm mensagens não classificadas.

    O pipeline diário só olha para 'ontem', então mensagens extraídas depois (por
    backfill) para uma data já passada ficam pendentes para sempre. Isto expõe
    essas datas órfãs para que possam ser drenadas aos poucos.
    """
    order = "DESC" if newest_first else "ASC"
    sql = (
        "SELECT DATE(created_at) AS d FROM messages WHERE processed = 0 "
        f"GROUP BY d ORDER BY d {order}"
    )
    params: tuple = ()
    if limit is not None:
        sql += " LIMIT ?"
        params = (int(limit),)
    with get_connection() as conn:
        return [r["d"] for r in conn.execute(sql, params) if r["d"]]


def mark_messages_processed(message_ids: list[str]) -> None:
    if not message_ids:
        return
    with get_connection() as conn:
        placeholders = ",".join("?" for _ in message_ids)
        conn.execute(
            f"UPDATE messages SET processed = 1 WHERE id IN ({placeholders})",
            message_ids,
        )


def reset_processed(start: str, end: str) -> int:
    """Remarca como pendentes (processed=0) as mensagens do intervalo de datas.

    Usado para reprocessar/reclassificar um período com outra engine. Retorna
    quantas linhas foram afetadas.
    """
    with get_connection() as conn:
        cur = conn.execute(
            "UPDATE messages SET processed = 0 WHERE DATE(created_at) BETWEEN ? AND ?",
            (start, end),
        )
        return cur.rowcount


# --- Read Cursors ---

def get_cursor(chat_id: str) -> datetime | None:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT last_read_at FROM read_cursors WHERE chat_id = ?", (chat_id,)
        ).fetchone()
        if row and row["last_read_at"]:
            return datetime.fromisoformat(row["last_read_at"])
        return None


def update_cursor(chat_id: str, last_read_at: str) -> None:
    with get_connection() as conn:
        conn.execute(
            """INSERT INTO read_cursors (chat_id, last_read_at)
               VALUES (?, ?)
               ON CONFLICT(chat_id) DO UPDATE SET last_read_at = excluded.last_read_at""",
            (chat_id, last_read_at),
        )


# --- Classified Items ---

def save_classified_items(items: list[dict], briefing_date: str) -> None:
    engine = _active_engine()
    with get_connection() as conn:
        for item in items:
            conn.execute(
                """INSERT INTO classified_items
                   (message_id, chat_name, category, content_clean, author,
                    occurred_at, briefing_date, engine)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    "",
                    item.get("chat_name", "Geral"),
                    item["category"],
                    item.get("content", ""),
                    item.get("author", ""),
                    item.get("timestamp", ""),
                    briefing_date,
                    engine,
                ),
            )


def delete_items_for_range(start: str, end: str) -> int:
    """Remove os itens classificados de um intervalo (para reprocessar com outra
    engine sem misturar resultados). Retorna quantas linhas foram removidas."""
    with get_connection() as conn:
        cur = conn.execute(
            "DELETE FROM classified_items WHERE briefing_date BETWEEN ? AND ?",
            (start, end),
        )
        return cur.rowcount


def get_items_for_date(date: str) -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM classified_items WHERE briefing_date = ? ORDER BY occurred_at",
            (date,),
        ).fetchall()
        return [dict(r) for r in rows]


def get_items_for_date_range(start: str, end: str) -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            """SELECT * FROM classified_items
               WHERE briefing_date BETWEEN ? AND ?
               ORDER BY occurred_at""",
            (start, end),
        ).fetchall()
        return [dict(r) for r in rows]
