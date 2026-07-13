"""Cliente para extração de mensagens do Microsoft Teams via Graph API."""

import re
import time
from datetime import datetime, timezone

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

import config
from auth.token_manager import get_access_token

GRAPH_BASE = "https://graph.microsoft.com/v1.0"


def _build_session() -> requests.Session:
    """Session compartilhada com retry/backoff para sobreviver a quedas
    transitórias de rede.

    Sem isto, uma única falha de conexão (rede subindo no boot, soluço no meio
    da paginação) aborta a extração inteira. Retenta erros de conexão/leitura e
    429/5xx com backoff exponencial e respeita Retry-After. GET é idempotente, o
    replay é seguro.
    """
    retry = Retry(
        total=config.HTTP_MAX_RETRIES,
        backoff_factor=config.HTTP_BACKOFF_FACTOR,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["GET"]),
        respect_retry_after_header=True,
        raise_on_status=False,  # deixa o resp.raise_for_status() decidir no fim
    )
    session = requests.Session()
    session.mount("https://", HTTPAdapter(max_retries=retry))
    return session


# Uma sessão por processo: o pool de conexões e a política de retry são
# thread-safe, então os workers paralelos da extração a compartilham.
_SESSION = _build_session()


def get_session() -> requests.Session:
    """Sessão HTTP compartilhada (retry/backoff) para chamadas à Graph.

    Exposta para outros módulos (ex.: download de imagens no exporter) reusarem
    a mesma resiliência a quedas transitórias, em vez de um requests.get() cru.
    """
    return _SESSION

# Tipos de mensagem que NÃO são conversa real (eventos de sistema do Teams).
SYSTEM_MESSAGE_TYPES = {"systemEventMessage", "chatEvent", "unknownFutureValue"}


def to_utc(dt: datetime | None) -> datetime | None:
    """Normaliza para UTC-aware. O cursor vem aware; o piso de --since, naive."""
    if dt is None:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def parse_timestamp(value: str | None) -> datetime | None:
    """createdDateTime/lastActivity da Graph -> datetime UTC-aware.

    A precisão fracionária varia entre mensagens ('.11Z', '.061Z', '.7Z'), então
    comparar as strings ISO diretamente é furado: '...25.11Z' > '...25.110000+00:00'
    na ordem lexicográfica. Toda comparação de instante passa por aqui.
    """
    if not value:
        return None
    try:
        return to_utc(datetime.fromisoformat(value))
    except ValueError:
        return None


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
        resp = _SESSION.get(url, headers=headers, params=params, timeout=config.REQUEST_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        results.extend(data.get("value", []))
        url = data.get("@odata.nextLink")
        params = None
        if url:  # só pausa se há mais páginas a buscar
            time.sleep(config.API_DELAY)

    return results


# Piso para chats sem timestamp — afundam para o fim da ordenação.
_NO_ACTIVITY = datetime.min.replace(tzinfo=timezone.utc)


def _chat_sort_key(chat: dict) -> tuple[datetime, datetime]:
    """Ordena pela última mensagem real; desempata pela atividade bruta.

    Chats sem mensagem real (só eventos de sistema, ou sem preview) afundam para
    o fim — sem fallback no lastUpdated, que sobe metadados em lote. Compara
    datetime, não string: ver parse_timestamp.
    """
    return (
        parse_timestamp(chat.get("lastMessage")) or _NO_ACTIVITY,
        parse_timestamp(chat.get("lastActivity")) or _NO_ACTIVITY,
    )


def list_chats() -> list[dict]:
    """Lista todos os chats do usuário autenticado, sem duplicatas.

    Retorna lista com id, chatType e topic de cada chat, ordenada pela última
    mensagem real (mais recente primeiro).
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

    # A paginação da Graph repete chats entre páginas (o $skiptoken não é estável
    # se a coleção muda durante a varredura). Sem deduplicar, o mesmo chat entra
    # várias vezes no pool e é extraído em paralelo consigo mesmo — chamadas
    # desperdiçadas e contagem inflada. Mantém a entrada de atividade mais recente.
    unique: dict[str, dict] = {}
    for c in chats:
        seen = unique.get(c["id"])
        if seen is None or _chat_sort_key(c) > _chat_sort_key(seen):
            unique[c["id"]] = c

    return sorted(unique.values(), key=_chat_sort_key, reverse=True)


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
    since: datetime | None,
    until: datetime | None,
    out: list[dict],
    since_exclusive: bool = False,
) -> bool:
    """Filtra uma página de mensagens para 'out'. Retorna True se cruzou o piso.

    Mensagens vêm em ordem decrescente: ao encontrar uma anterior a 'since', o
    restante é ainda mais antigo e a paginação pode parar.

    since_exclusive=True quando 'since' é o cursor: a mensagem exatamente nesse
    instante já foi extraída, então ela também é piso e para a paginação. Sem
    isso, a última mensagem de cada chat era rebaixada em toda execução.
    Com o piso de --since (uma data) o limite é inclusivo — daí o parâmetro.
    """
    for msg in values:
        created = parse_timestamp(msg.get("createdDateTime"))
        if since and created:
            if created < since or (since_exclusive and created == since):
                return True
        if until and created and created > until:
            continue
        parsed = _parse_message(msg)
        if parsed:
            out.append(parsed)
    return False


def get_messages(
    chat_id: str,
    since: datetime | None = None,
    until: datetime | None = None,
    since_exclusive: bool = False,
) -> list[dict]:
    """Busca mensagens de um chat no intervalo [since, until].

    O endpoint /chats/{id}/messages NÃO suporta $filter nem $orderby (retorna
    HTTP 400). As mensagens vêm em ordem decrescente de criação, então filtramos
    client-side e interrompemos a paginação assim que passamos do piso 'since'.

    since_exclusive=True trata 'since' como já extraído (é o cursor), excluindo a
    mensagem exatamente nesse instante. Ver _collect_page.

    Retorna lista com id, author, body, created_at (mensagens de sistema e sem
    conteúdo são descartadas).
    """
    url = f"{GRAPH_BASE}/me/chats/{chat_id}/messages"
    params = {"$top": "50"}
    headers = _headers()

    # Comparação por datetime, não por string: os createdDateTime da Graph têm
    # precisão fracionária variável (ver parse_timestamp).
    since = to_utc(since)
    until = to_utc(until)

    messages = []
    reached_floor = False

    while url and not reached_floor:
        resp = _SESSION.get(url, headers=headers, params=params, timeout=config.REQUEST_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()

        reached_floor = _collect_page(
            data.get("value", []), since, until, messages, since_exclusive
        )

        url = data.get("@odata.nextLink")
        params = None
        if url and not reached_floor:  # só pausa se vai buscar mais páginas
            time.sleep(config.API_DELAY)

    return messages
