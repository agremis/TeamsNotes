"""Geração de briefings em Markdown — diário, semanal e mensal.

Cada briefing agrupa itens por chat (H2), com resumo narrativo e
takeaways organizados por categoria. Seções sem conteúdo são omitidas.
Arquivos são salvos no OBSIDIAN_VAULT_PATH (ou BRIEFINGS_PATH como fallback).
"""

import os
from collections import defaultdict
from datetime import date, timedelta

import config

CATEGORY_LABELS = {
    "alinhamento": "Alinhamentos & Decisões",
    "lembrete": "Lembretes & Prazos",
    "snippet_codigo": "Snippets de Código",
    "link_util": "Links Relevantes",
    "alerta": "Alertas & Problemas",
    "definicao": "Definições Técnicas",
}

# Ordem de exibição dentro de cada seção de chat
CATEGORY_DISPLAY_ORDER = [
    "alinhamento",
    "alerta",
    "lembrete",
    "definicao",
    "snippet_codigo",
    "link_util",
]


def _output_base() -> str:
    return config.OBSIDIAN_VAULT_PATH or config.BRIEFINGS_PATH


def _save(path: str, content: str) -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return path


def _chat_relevance_score(items: list[dict]) -> int:
    return sum(config.CATEGORY_WEIGHT.get(it["category"], 0) for it in items)


def _render_chat_section(chat_name: str, items: list[dict]) -> str:
    """Renderiza uma seção H2 para um chat com takeaways por categoria."""
    grouped = defaultdict(list)
    for item in items:
        grouped[item["category"]].append(item)

    lines = [f"## {chat_name}\n"]

    for cat in CATEGORY_DISPLAY_ORDER:
        cat_items = grouped.get(cat, [])
        if not cat_items:
            continue
        label = CATEGORY_LABELS.get(cat, cat)
        lines.append(f"### {label}\n")
        for it in cat_items:
            author = it.get("author", "")
            content = it.get("content", "")
            prefix = f"**{author}**: " if author else ""
            lines.append(f"- {prefix}{content}")
        lines.append("")

    return "\n".join(lines)


def _render_briefing(title: str, subtitle: str | None, items_by_chat: dict[str, list[dict]]) -> str | None:
    """Renderiza o briefing completo agrupado por chat, ordenado por relevância."""
    # Filtrar chats sem conteúdo
    chats_with_content = {
        name: items for name, items in items_by_chat.items() if items
    }
    if not chats_with_content:
        return None

    # Ordenar por relevância (alertas e alinhamentos pesam mais)
    sorted_chats = sorted(
        chats_with_content.items(),
        key=lambda pair: _chat_relevance_score(pair[1]),
        reverse=True,
    )

    lines = [f"# {title}\n"]
    if subtitle:
        lines.append(f"_{subtitle}_\n")

    for chat_name, items in sorted_chats:
        lines.append(_render_chat_section(chat_name, items))

    return "\n".join(lines)


def _group_by_chat(items: list[dict]) -> dict[str, list[dict]]:
    grouped = defaultdict(list)
    for item in items:
        # Suporta tanto itens do classifier (chat_name) quanto do DB (chat_name)
        chat_name = item.get("chat_name", "Geral")
        # Normaliza: items do DB usam content_clean, do classifier usam content
        if "content_clean" in item and "content" not in item:
            item["content"] = item["content_clean"]
        grouped[chat_name].append(item)
    return dict(grouped)


def build_daily(target_date: date, items: list[dict]) -> str | None:
    """Gera briefing diario e salva em daily/YYYY-MM-DD.md.

    Retorna o conteudo Markdown ou None se não houver conteúdo.
    """
    date_str = target_date.isoformat()
    items_by_chat = _group_by_chat(items)

    md = _render_briefing(
        f"Briefing Diário -- {date_str}",
        None,
        items_by_chat,
    )
    if not md:
        return None

    path = os.path.join(_output_base(), "daily", f"{date_str}.md")
    _save(path, md)
    return md


def build_weekly(start_date: date, end_date: date, items: list[dict]) -> str | None:
    """Gera briefing semanal e salva em weekly/YYYY-Wnn.md."""
    year, week, _ = end_date.isocalendar()
    week_label = f"{year}-W{week:02d}"
    items_by_chat = _group_by_chat(items)

    md = _render_briefing(
        f"Briefing Semanal -- {week_label}",
        f"Periodo: {start_date.isoformat()} a {end_date.isoformat()}",
        items_by_chat,
    )
    if not md:
        return None

    path = os.path.join(_output_base(), "weekly", f"{week_label}.md")
    _save(path, md)
    return md


def build_monthly(year: int, month: int, items: list[dict]) -> str | None:
    """Gera briefing mensal e salva em monthly/YYYY-MM.md."""
    start = date(year, month, 1)
    if month == 12:
        end = date(year + 1, 1, 1) - timedelta(days=1)
    else:
        end = date(year, month + 1, 1) - timedelta(days=1)

    month_label = f"{year}-{month:02d}"
    items_by_chat = _group_by_chat(items)

    md = _render_briefing(
        f"Briefing Mensal -- {month_label}",
        f"Periodo: {start.isoformat()} a {end.isoformat()}",
        items_by_chat,
    )
    if not md:
        return None

    path = os.path.join(_output_base(), "monthly", f"{month_label}.md")
    _save(path, md)
    return md
