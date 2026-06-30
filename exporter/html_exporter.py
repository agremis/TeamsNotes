"""Gera o arquivo HTML navegável das conversas (uma página por chat + índice).

Lê as mensagens preservadas (raw_html) do SQLite, baixa as imagens inline
(hostedContents da Graph, autenticado) para assets locais e renderiza páginas
estilo Teams com divisória por dia. Não chama a LLM — é só preservação.
"""

import hashlib
import html
import logging
import os
import re
import shutil
from html import unescape

import requests

import config
from auth.token_manager import get_access_token
from storage.database import get_connection

logger = logging.getLogger(__name__)

TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), "templates")
IMG_EXTS = ("png", "jpg", "jpeg", "gif", "webp")
_CONTENT_TYPE_EXT = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/jpg": "jpg",
    "image/gif": "gif",
    "image/webp": "webp",
}
_MONTHS = ["", "jan", "fev", "mar", "abr", "mai", "jun",
           "jul", "ago", "set", "out", "nov", "dez"]

# Aplica o tema salvo (ou o do sistema) antes da pintura, evitando flash.
_THEME_HEAD = (
    '<script>(function(){var t=localStorage.getItem("theme")||'
    '(matchMedia("(prefers-color-scheme: dark)").matches?"dark":"light");'
    'document.documentElement.setAttribute("data-theme",t);})();</script>'
)
_THEME_BTN = '<button id="theme-toggle" class="theme-btn" title="Alternar tema">\U0001F319</button>'


# --------------------------------------------------------------------------- #
# Helpers de formatação
# --------------------------------------------------------------------------- #

def _slug(chat_id: str, chat_name: str) -> str:
    """Slug filesystem-safe: prefixo legível + hash curto do id."""
    h = hashlib.sha1(chat_id.encode("utf-8")).hexdigest()[:8]
    prefix = re.sub(r"[^A-Za-z0-9]+", "-", chat_name or "chat").strip("-")[:40]
    return f"{prefix or 'chat'}-{h}"


def _fmt_day(date_str: str) -> str:
    """'2026-06-25' -> '25 jun 2026'."""
    parts = (date_str or "")[:10].split("-")
    if len(parts) != 3:
        return date_str or ""
    y, m, d = parts
    month = _MONTHS[int(m)] if m.isdigit() and 1 <= int(m) <= 12 else m
    return f"{d} {month} {y}"


def _fmt_time(created_at: str) -> str:
    """'2026-06-25T18:47:58Z' -> '18:47'."""
    return created_at[11:16] if created_at and len(created_at) >= 16 else ""


def _author_class(author: str) -> str:
    n = int(hashlib.md5((author or "").encode("utf-8")).hexdigest(), 16) % 8
    return f"a{n}"


def _sanitize(raw: str) -> str:
    raw = re.sub(r"<script\b[^>]*>.*?</script>", "", raw, flags=re.I | re.S)
    raw = re.sub(r"<style\b[^>]*>.*?</style>", "", raw, flags=re.I | re.S)
    return raw


# --------------------------------------------------------------------------- #
# Imagens
# --------------------------------------------------------------------------- #

def _ext_from_content_type(ctype: str) -> str:
    return _CONTENT_TYPE_EXT.get((ctype or "").split(";")[0].strip().lower(), "png")


def _download_image(url: str, dest_dir: str, basename: str) -> str | None:
    """Baixa um hostedContent autenticado. Retorna o nome do arquivo ou None."""
    basename = re.sub(r"[^A-Za-z0-9_-]", "", basename) or "img"
    for ext in IMG_EXTS:  # cache: já baixado antes
        if os.path.exists(os.path.join(dest_dir, f"{basename}.{ext}")):
            return f"{basename}.{ext}"
    try:
        token = get_access_token()
        resp = requests.get(
            url, headers={"Authorization": f"Bearer {token}"}, timeout=config.REQUEST_TIMEOUT
        )
        resp.raise_for_status()
    except Exception as e:
        logger.warning("Falha ao baixar imagem: %s", e)
        return None

    os.makedirs(dest_dir, exist_ok=True)
    fname = f"{basename}.{_ext_from_content_type(resp.headers.get('Content-Type', ''))}"
    with open(os.path.join(dest_dir, fname), "wb") as f:
        f.write(resp.content)
    return fname


def _localize_images(raw_html: str, slug: str, msg_id: str, img_dir: str) -> str:
    """Baixa as imagens inline da Graph e reescreve os <img src> para local."""
    counter = {"n": 0}

    def repl(match: re.Match) -> str:
        tag = match.group(0)
        src_match = re.search(r'src="([^"]*)"', tag)
        if not src_match:
            return tag
        src = unescape(src_match.group(1))
        if "graph.microsoft.com" not in src or "hostedContents" not in src:
            return tag
        counter["n"] += 1
        fname = _download_image(src, img_dir, f"{msg_id}_{counter['n']}")
        if not fname:
            return tag
        rel = f"../assets/img/{slug}/{fname}"
        return tag[: src_match.start(1)] + rel + tag[src_match.end(1):]

    return re.sub(r"<img\b[^>]*>", repl, raw_html or "")


# --------------------------------------------------------------------------- #
# Renderização
# --------------------------------------------------------------------------- #

def _render_messages(rows: list[dict], slug: str, img_dir: str) -> str:
    parts: list[str] = []
    current_day = None
    for r in rows:
        created = r.get("created_at") or ""
        day = created[:10]
        if day != current_day:
            current_day = day
            parts.append(f'<div class="day-sep"><span class="day-label">{_fmt_day(day)}</span></div>')

        raw = r.get("raw_html") or html.escape(r.get("body") or "")
        content = _localize_images(_sanitize(raw), slug, r.get("id") or "", img_dir)
        parts.append(
            f'<div class="msg {_author_class(r.get("author"))}">'
            f'<div class="meta"><span class="author">{html.escape(r.get("author") or "Desconhecido")}</span>'
            f'<span class="time">{_fmt_time(created)}</span></div>'
            f'<div class="content">{content}</div></div>'
        )
    return "\n".join(parts)


def _chat_page(title: str, body: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="pt-br"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<link rel="stylesheet" href="../assets/style.css">
{_THEME_HEAD}
</head><body>
<header class="topbar"><a class="back" href="../index.html">&#8592; Índice</a>\
<h1>{html.escape(title)}</h1>{_THEME_BTN}</header>
<main class="conversation">
{body}
</main>
<div id="lightbox" class="lightbox"><img alt=""></div>
<script src="../assets/app.js"></script>
</body></html>"""


def _index_page(chats: list[dict]) -> str:
    rows = []
    for c in chats:
        rng = f"{_fmt_day(c['first'])} – {_fmt_day(c['last'])}"
        rows.append(
            f'<a class="chat-row" href="chats/{c["slug"]}.html">'
            f'<span class="name">{html.escape(c["name"])}</span>'
            f'<span class="count">{c["n"]} msgs</span>'
            f'<span class="range">{rng}</span></a>'
        )
    return f"""<!DOCTYPE html>
<html lang="pt-br"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Conversas do Teams</title>
<link rel="stylesheet" href="assets/style.css">
{_THEME_HEAD}
</head><body>
<header class="topbar"><h1>Conversas do Teams</h1>{_THEME_BTN}</header>
<div class="index-wrap">
<input id="search" class="search" type="search" placeholder="Filtrar conversas...">
{os.linesep.join(rows)}
</div>
<script src="assets/app.js"></script>
</body></html>"""


# --------------------------------------------------------------------------- #
# Consultas
# --------------------------------------------------------------------------- #

def _query_chats() -> list[dict]:
    """Metadados de todos os chats com mensagens (para o índice)."""
    sql = """SELECT chat_id, MAX(chat_name) AS chat_name, COUNT(*) AS n,
                    MIN(created_at) AS first, MAX(created_at) AS last
             FROM messages
             GROUP BY chat_id ORDER BY last DESC"""
    with get_connection() as conn:
        return [dict(r) for r in conn.execute(sql)]


def _query_messages(chat_id: str) -> list[dict]:
    """Histórico COMPLETO de um chat (a página é sempre regenerada inteira, o
    que faz a conversa acumular conforme o banco cresce entre execuções)."""
    sql = """SELECT id, author, body, raw_html, created_at FROM messages
             WHERE chat_id = ? ORDER BY created_at"""
    with get_connection() as conn:
        return [dict(r) for r in conn.execute(sql, (chat_id,))]


# --------------------------------------------------------------------------- #
# Orquestração
# --------------------------------------------------------------------------- #

def _copy_templates(assets_dir: str) -> None:
    os.makedirs(assets_dir, exist_ok=True)
    for name in ("style.css", "app.js"):
        shutil.copyfile(os.path.join(TEMPLATES_DIR, name), os.path.join(assets_dir, name))


def _write(path: str, content: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def export(chat_ids: list[str] | None = None) -> int:
    """Gera/atualiza o arquivo HTML em config.CHATS_HTML_PATH.

    Cada página de chat é regenerada a partir do histórico COMPLETO no banco —
    como o banco acumula entre execuções, as conversas vão crescendo sem criar
    páginas novas. Se chat_ids for dado, só esses chats são regenerados (os
    tocados na execução); o índice é sempre reescrito com todos os chats.

    Retorna o número de páginas de chat regeneradas.
    """
    out = config.CHATS_HTML_PATH
    chats_dir = os.path.join(out, "chats")
    assets_dir = os.path.join(out, "assets")
    img_root = os.path.join(assets_dir, "img")
    _copy_templates(assets_dir)

    all_chats = _query_chats()
    wanted = set(chat_ids) if chat_ids is not None else None
    targets = [c for c in all_chats if wanted is None or c["chat_id"] in wanted]
    logger.info("Chats no arquivo: %d | regenerando: %d", len(all_chats), len(targets))

    for c in targets:
        slug = _slug(c["chat_id"], c["chat_name"] or "")
        rows = _query_messages(c["chat_id"])
        body = _render_messages(rows, slug, os.path.join(img_root, slug))
        name = c["chat_name"] or "(sem nome)"
        _write(os.path.join(chats_dir, f"{slug}.html"), _chat_page(name, body))
        logger.info("  -> %s (%d msgs)", name, c["n"])

    meta = [
        {
            "slug": _slug(c["chat_id"], c["chat_name"] or ""),
            "name": c["chat_name"] or "(sem nome)",
            "n": c["n"], "first": c["first"], "last": c["last"],
        }
        for c in all_chats
    ]
    _write(os.path.join(out, "index.html"), _index_page(meta))
    logger.info("Indice gerado em %s", os.path.join(out, "index.html"))
    return len(targets)
