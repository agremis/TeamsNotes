"""Classificação de mensagens via LLM (provider configurável)."""

import json
import logging

import config
from processor.llm_client import classify as llm_classify

logger = logging.getLogger(__name__)

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

Retorne APENAS um array JSON válido. Sem texto adicional, sem markdown.
Se não houver nada útil, retorne: []"""


def _format_messages_for_llm(messages: list[dict]) -> str:
    lines = []
    for msg in messages:
        lines.append(f"[{msg['created_at']}] {msg['author']}: {msg['body']}")
    return "\n".join(lines)


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
        logger.error("Falha ao parsear JSON da LLM: %s", raw[:500])
        return []


def classify(messages: list[dict]) -> list[dict]:
    """Classifica uma lista de mensagens via LLM.

    Recebe lista de dicts com keys: author, body, created_at.
    Retorna lista de dicts classificados com: category, content, author, timestamp.
    Processa em lotes conforme LLM_BATCH_SIZE.
    """
    if not messages:
        return []

    all_items = []

    for i in range(0, len(messages), config.LLM_BATCH_SIZE):
        batch = messages[i : i + config.LLM_BATCH_SIZE]
        batch_text = _format_messages_for_llm(batch)

        logger.info("Classificando lote de %d mensagens...", len(batch))
        raw = llm_classify(SYSTEM_PROMPT, batch_text)
        items = _parse_response(raw)

        for item in items:
            if item.get("category") not in config.CATEGORIES:
                logger.warning("Categoria inválida ignorada: %s", item.get("category"))
                continue
            all_items.append(item)

    logger.info("Total de itens classificados: %d", len(all_items))
    return all_items
