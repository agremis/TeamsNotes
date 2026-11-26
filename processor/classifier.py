"""Classificação de mensagens via LLM (provider configurável)."""

import html
import json
import logging
import re

import config
from processor.llm_client import classify as llm_classify

logger = logging.getLogger(__name__)

# Teams manda link colado como <a href="URL real">título visível</a> — o texto
# puro (body) só tem o título, a URL fica só no HTML. Sem isto, PRs, pipelines
# e outros links relevantes viram itens sem link nenhum.
_HREF_RE = re.compile(r'<a\s[^>]*\bhref="([^"]+)"', re.IGNORECASE)

SYSTEM_PROMPT = """Você receberá mensagens de chats corporativos do Microsoft Teams de um dia.

Sua tarefa é:
1. DESCARTAR silenciosamente: piadas, memes, "bom dia", "boa tarde",
   gifs, reações, conversas sociais sem valor profissional.

2. CLASSIFICAR o que for útil em uma destas categorias:
   - snippet_codigo: trechos de código, queries, comandos, configs
   - lembrete: tarefas, prazos, "não esquecer de..."
   - alinhamento: decisões tomadas, acordos, definições de rumo
   - definicao: explicações técnicas, conceitos, padrões
   - link_util: URLs relevantes com contexto
   - alerta: problemas, bugs, bloqueios, riscos mencionados

3. Para cada item útil, retorne:
   {
     "category": "...",
     "content": "conteúdo limpo, conciso, em português",
     "author": "nome do autor",
     "timestamp": "ISO 8601"
   }

Às vezes a entrada começa com um bloco CONTEXTO: mensagens anteriores da mesma
conversa, já processadas antes. Use-o só para entender as NOVAS MENSAGENS (a que
discussão um "fechado", "pode seguir" ou "resolvido" se refere). Extraia itens
APENAS das NOVAS MENSAGENS — nada que esteja só no CONTEXTO.

Retorne APENAS um array JSON válido. Sem texto adicional, sem markdown.
Se não houver nada útil, retorne: []"""

_CONTEXT_HEADER = "=== CONTEXTO (já processado — não extrair itens daqui) ==="
_NEW_HEADER = "=== NOVAS MENSAGENS (classificar) ==="


def _hidden_links(msg: dict) -> list[str]:
    """URLs presentes só no HTML (texto visível é apenas o título do link)."""
    raw_html = msg.get("raw_html") or ""
    body = msg.get("body") or ""
    links = []
    for href in _HREF_RE.findall(raw_html):
        href = html.unescape(href)
        if href not in body and href not in links:
            links.append(href)
    return links


def _format_messages_for_llm(messages: list[dict]) -> str:
    lines = []
    for msg in messages:
        text = msg["body"]
        for href in _hidden_links(msg):
            text = f"{text} ({href})"
        lines.append(f"[{msg['created_at']}] {msg['author']}: {text}")
    return "\n".join(lines)


def _build_prompt(context: list[dict], batch: list[dict]) -> str:
    """Lote a classificar, precedido (se houver) das mensagens de contexto.

    Sem contexto o prompt fica idêntico ao de antes — classificar um dia
    inteiro de uma vez não paga nada a mais."""
    if not context:
        return _format_messages_for_llm(batch)
    return "\n".join([
        _CONTEXT_HEADER,
        _format_messages_for_llm(context),
        "",
        _NEW_HEADER,
        _format_messages_for_llm(batch),
    ])


def _parse_response(raw: str) -> list[dict]:
    # LLM às vezes envolve em ```json ... ```
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
        if raw.endswith("```"):
            raw = raw[:-3]
        raw = raw.strip()

    try:
        items = json.loads(raw)
        if not isinstance(items, list):
            logger.warning("LLM retornou JSON não-array, descartando: %s", raw[:200])
            return []
        return items
    except json.JSONDecodeError:
        if not raw.rstrip().endswith("]"):
            logger.error(
                "Falha ao parsear JSON da LLM — resposta parece truncada (não "
                "termina em ']'), o lote inteiro foi perdido. Considere aumentar "
                "max_tokens ou reduzir LLM_BATCH_SIZE. Fim da resposta: %s",
                raw[-300:],
            )
        else:
            logger.error("Falha ao parsear JSON da LLM: %s", raw[:500])
        return []


def classify(messages: list[dict], context: list[dict] | None = None) -> list[dict]:
    """Classifica uma lista de mensagens (de um mesmo chat) via LLM.

    Recebe lista de dicts com keys: author, body, created_at, em ordem
    cronológica. 'context' são mensagens anteriores da mesma conversa, já
    classificadas, que vão junto só para leitura.
    Retorna lista de dicts classificados com: category, content, author, timestamp.
    Processa em lotes conforme LLM_BATCH_SIZE; cada lote leva como contexto as
    LLM_CONTEXT_MESSAGES mensagens que o precedem (do 'context' ou do lote
    anterior), para que um assunto cortado na fronteira não perca o começo.
    """
    if not messages:
        return []

    all_items = []
    # Um assunto que atravessa a fronteira de dois lotes é visto (e reextraído)
    # por ambas as chamadas à LLM, que não têm memória uma da outra.
    seen = set()
    history = list(context or [])
    n_ctx = max(0, config.LLM_CONTEXT_MESSAGES)

    for i in range(0, len(messages), config.LLM_BATCH_SIZE):
        batch = messages[i : i + config.LLM_BATCH_SIZE]
        ctx = history[-n_ctx:] if n_ctx else []
        batch_text = _build_prompt(ctx, batch)
        history.extend(batch)

        logger.info(
            "Classificando lote de %d mensagens%s...",
            len(batch), f" (+{len(ctx)} de contexto)" if ctx else "",
        )
        raw = llm_classify(SYSTEM_PROMPT, batch_text)
        items = _parse_response(raw)

        for item in items:
            if item.get("category") not in config.CATEGORIES:
                logger.warning("Categoria inválida ignorada: %s", item.get("category"))
                continue
            key = (item["category"], item.get("content", "").strip().lower())
            if key in seen:
                continue
            seen.add(key)
            all_items.append(item)

    logger.info("Total de itens classificados: %d", len(all_items))
    return all_items
