"""Converte um briefing diário em Markdown em parágrafos autocontidos por seção.

Cada seção `##` (um chat/tópico distinto) vira UM parágrafo corrido, prosa pura
(sem `**negrito**`, `-` de bullet ou `###`), pronto para ser capturado como uma
"thought" no Open Brain. É uma reformatação determinística — não resume, não
inventa e não infere nada além do que já está escrito nos bullets originais.
"""

import os
import re
from datetime import date

_DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")
_AUTHOR_RE = re.compile(r"^\*\*(?P<author>[^*]+)\*\*:\s*(?P<text>.*)$", re.DOTALL)


def extract_date(md_text: str, file_path: str | None = None) -> date | None:
    """Data do briefing: primeiro tenta o nome do arquivo (YYYY-MM-DD.md),
    depois o H1 ('# Briefing Diário -- YYYY-MM-DD'). None se não achar nenhum.
    """
    if file_path:
        m = _DATE_RE.search(os.path.basename(file_path))
        if m:
            return date.fromisoformat(m.group(1))
    for line in md_text.splitlines():
        if line.startswith("# "):
            m = _DATE_RE.search(line)
            if m:
                return date.fromisoformat(m.group(1))
    return None


def _split_sections(md_text: str) -> list[tuple[str, list[str]]]:
    """[(chat_name, linhas_da_secao)] para cada H2, ignorando o H1 do título."""
    sections: list[tuple[str, list[str]]] = []
    title: str | None = None
    lines: list[str] = []
    for raw in md_text.splitlines():
        if raw.startswith("## "):
            if title is not None:
                sections.append((title, lines))
            title = raw[3:].strip()
            lines = []
        elif raw.startswith("# "):
            continue
        elif title is not None:
            lines.append(raw)
    if title is not None:
        sections.append((title, lines))
    return sections


def _split_subsections(section_lines: list[str]) -> list[tuple[str, list[str]]]:
    """[(assunto, [bullet_texto, ...])] para cada H3 dentro da seção.

    Bullets multi-linha (ex.: snippet de código com quebras) são unidos numa só
    linha de texto — continuam o bullet anterior até o próximo `- `, `###` ou
    fim da seção.
    """
    subs: list[tuple[str, list[list[str]]]] = []
    label: str | None = None
    bullets: list[list[str]] = []
    for raw in section_lines:
        stripped = raw.strip()
        if stripped.startswith("### "):
            if label is not None:
                subs.append((label, bullets))
            header = stripped[4:].strip()
            label = header.split()[0] if header else header
            bullets = []
        elif stripped.startswith("- "):
            bullets.append([stripped[2:].strip()])
        elif stripped and bullets:
            bullets[-1].append(stripped)
    if label is not None:
        subs.append((label, bullets))
    return [(lbl, [" ".join(b).strip() for b in bts]) for lbl, bts in subs]


def _bullet_to_prose(bullet_text: str) -> str:
    """'**Nome**: texto' -> 'Nome: texto'; sem autor, retorna o texto como está."""
    m = _AUTHOR_RE.match(bullet_text)
    if not m:
        return bullet_text.strip()
    author = m.group("author").strip()
    text = m.group("text").strip()
    return f"{author}: {text}" if text else author


def _participants_phrase(chat_name: str) -> str | None:
    """Se chat_name parece uma lista de nomes de participantes (fallback do
    Teams quando o chat não tem topic próprio — ver extractor/teams_client.py),
    retorna 'conversa entre A, B e C'. Caso contrário (nome de tópico de
    verdade), retorna None.

    Sinal usado: a vírgula. Tópicos escolhidos por humanos neste dataset não
    usam vírgula (ex.: '[FAST] Daily', 'Monitoring Rule Event Details'); a
    lista de participantes do Teams é sempre separada por vírgula. Evitamos de
    propósito inspecionar o "formato" de cada nome (maiúscula por palavra etc.)
    — isso é frágil e quebra com qualquer variação (conectores como "de/da",
    hífen, apóstrofo, nome de um termo só...). O único resguardo é um limite
    de tamanho por segmento, pra não confundir uma frase-tópico com vírgula
    no meio com uma lista de nomes.
    """
    if "," not in chat_name:
        return None
    parts = [p.strip() for p in chat_name.split(",") if p.strip()]
    if len(parts) < 2:
        return None
    if any(len(p.split()) > 6 for p in parts):
        return None
    return f"conversa entre {', '.join(parts[:-1])} e {parts[-1]}"


def _build_intro(chat_name: str, dt: date | None) -> str:
    date_str = dt.strftime("%d/%m/%Y") if dt else "data desconhecida"
    contexto = _participants_phrase(chat_name) or f"na thread '{chat_name}'"
    return f"Em {date_str}, {contexto}:"


def _section_to_paragraph(chat_name: str, section_lines: list[str], dt: date | None) -> str | None:
    grouped = []
    for label, bullets in _split_subsections(section_lines):
        # Tira só um "." final de cada bullet antes de juntar com "; " — evita
        # ".;" no meio do parágrafo (conteúdo preservado, é puramente cosmético).
        prose = [_bullet_to_prose(b).rstrip(".") for b in bullets if b.strip()]
        prose = [p for p in prose if p]
        if prose:
            grouped.append(f"{label}: " + "; ".join(prose))
    if not grouped:
        return None
    sentences = [g if g[-1] in "!?:" else f"{g}." for g in grouped]
    return _build_intro(chat_name, dt) + " " + " ".join(sentences)


def parse_briefing(md_text: str, file_path: str | None = None) -> list[dict]:
    """[{'section': chat_name, 'paragraph': str}] — um item por seção H2 com
    conteúdo. Seções sem nenhum bullet são omitidas.
    """
    dt = extract_date(md_text, file_path)
    result = []
    for chat_name, lines in _split_sections(md_text):
        paragraph = _section_to_paragraph(chat_name, lines, dt)
        if paragraph:
            result.append({"section": chat_name, "paragraph": paragraph})
    return result
