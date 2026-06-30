"""Cliente para extração de mensagens do Microsoft Teams via Graph API."""

import re
import time
from datetime import datetime

import requests

import config
from auth.token_manager import get_access_token

GRAPH_BASE = "https://graph.microsoft.com/v1.0"

# Tipos de mensagem que NÃO são conversa real (eventos de sistema do Teams).
SYSTEM_MESSAGE_TYPES = {"systemEventMessage", "chatEvent", "unknownFutureValue"}


def _is_system_preview(preview: dict) -> bool:
    """Detecta se o lastMessagePreview é um evento de sistema (não conversa).

    Conservador: só retorna True diante de sinal POSITIVO de sistema
    (eventDetail presente ou messageType de sistema). Se os campos vierem
    ausentes, trata como mensagem real — evita rebaixar conversa por engano.
    """
    if preview.get("eventDetail"):
        return True
    return preview.get("messageType") in SYSTEM_MESSAGE_TYPES


def _headers() -> dict:
    token = get_access_token()
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }


def _get_paginated(url: str, params: dict | None = None) -> list[dict]:
    """Faz GET paginado seguindo @odata.nextLink."""
    results = []
    headers = _headers()

    while url:
        resp = requests.get(url, headers=headers, params=params, timeout=config.REQUEST_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        results.extend(data.get("value", []))
        url = data.get("@odata.nextLink")
        params = None
        if url:  # só pausa se há mais páginas a buscar
            time.sleep(config.API_DELAY)

    return results


def list_chats() -> list[dict]:
    """Lista todos os chats do usuário autenticado.

    Retorna lista com id, chatType e topic de cada chat.
    """
    raw = _get_paginated(
        f"{GRAPH_BASE}/me/chats",
        params={
            "$top": "50",
            "$expand": "members,lastMessagePreview",
        },
    )

    chats = []
    for c in raw:
        members = c.get("members", [])
        member_names = [
            m.get("displayName", "?")
            for m in members
            if m.get("displayName")
        ]

        # lastUpdatedDateTime só muda em rename/alteração de membros — NÃO
        # reflete novas mensagens. Usamos o createdDateTime da última mensagem
        # (lastMessagePreview). O preview, porém, inclui mensagens de sistema
        # (reunião iniciada/encerrada, alguém entrou, gravação disponível), que
        # fariam webinars/reuniões subirem sem conversa real.
        #
        #   lastActivity = horário do preview (qualquer tipo) — limite superior
        #                  seguro para decidir extração (nunca pula conversa real).
        #   lastMessage  = horário só quando o preview é mensagem real — usado
        #                  para ordenar/exibir (eventos de sistema afundam).
        preview = c.get("lastMessagePreview") or {}
        last_activity = preview.get("createdDateTime", "")
        last_message = "" if _is_system_preview(preview) else last_activity

        chats.append({
            "id": c["id"],
            "chatType": c.get("chatType", ""),
            "topic": c.get("topic") or ", ".join(member_names) or "(sem nome)",
            "lastUpdated": c.get("lastUpdatedDateTime", ""),
            "lastMessage": last_message,
            "lastActivity": last_activity,
        })

    # Ordena pela última mensagem real; desempata pela atividade bruta. Chats
    # sem mensagem real (só eventos de sistema, ou sem preview) afundam para o
    # fim — sem fallback no lastUpdated, que sobe metadados em lote.
    chats.sort(
        key=lambda c: (c.get("lastMessage") or "", c.get("lastActivity") or ""),
        reverse=True,
    )
    return chats


def _parse_message(msg: dict) -> dict | None:
    """Converte uma mensagem da Graph no formato interno, ou None se descartável.

    Descarta mensagens de sistema (messageType != 'message') e sem conteúdo.
    Devolve 'body' (texto limpo, para a LLM) e 'raw_html' (original preservado,
    para o arquivo HTML — imagens são localizadas depois, no export).
    """
    if msg.get("messageType") != "message":
        return None

    body = msg.get("body", {})
    raw_html = body.get("content", "")
    content = raw_html.strip()
    if not content:
        return None

    if body.get("contentType") == "html":
        content = re.sub(r"<[^>]+>", "", content).strip()

    if not content:
        return None

    from_data = msg.get("from") or {}
    user_data = from_data.get("user") or {}
    author = user_data.get("displayName", "Desconhecido")

    return {
        "id": msg["id"],
        "author": author,
        "body": content,
        "raw_html": raw_html,
        "created_at": msg.get("createdDateTime", ""),
    }


def _collect_page(
    values: list[dict],
    since_iso: str | None,
    until_iso: str | None,
    out: list[dict],
) -> bool:
    """Filtra uma página de mensagens para 'out'. Retorna True se cruzou o piso.

    Mensagens vêm em ordem decrescente: ao encontrar uma anterior a 'since', o
    restante é ainda mais antigo e a paginação pode parar.
    """
    for msg in values:
        created = msg.get("createdDateTime", "")
        if since_iso and created and created < since_iso:
            return True
        if until_iso and created and created > until_iso:
            continue
        parsed = _parse_message(msg)
        if parsed:
            out.append(parsed)
    return False


def get_messages(
    chat_id: str,
    since: datetime | None = None,
    until: datetime | None = None,
) -> list[dict]:
    """Busca mensagens de um chat no intervalo [since, until].

    O endpoint /chats/{id}/messages NÃO suporta $filter nem $orderby (retorna
    HTTP 400). As mensagens vêm em ordem decrescente de criação, então filtramos
    client-side e interrompemos a paginação assim que passamos do piso 'since'.

    Retorna lista com id, author, body, created_at (mensagens de sistema e sem
    conteúdo são descartadas).
    """
    url = f"{GRAPH_BASE}/me/chats/{chat_id}/messages"
    params = {"$top": "50"}
    headers = _headers()

    since_iso = since.strftime("%Y-%m-%dT%H:%M:%S.000Z") if since else None
    until_iso = until.strftime("%Y-%m-%dT%H:%M:%S.999Z") if until else None

    messages = []
    reached_floor = False

    while url and not reached_floor:
        resp = requests.get(url, headers=headers, params=params, timeout=config.REQUEST_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()

        reached_floor = _collect_page(data.get("value", []), since_iso, until_iso, messages)

        url = data.get("@odata.nextLink")
        params = None
        if url and not reached_floor:  # só pausa se vai buscar mais páginas
            time.sleep(config.API_DELAY)

    return messages
