"""Cliente HTTP para o MCP server pessoal Open Brain (tool `capture_thought`).

Streamable HTTP MCP, JSON-RPC 2.0. A chave de acesso vai como query param
`key` (mesmo mecanismo aceito pelo server: header `x-brain-key` OU `?key=`).

Server de referência (deploy como Supabase Edge Function): projeto OB1
https://github.com/NateBJones-Projects/OB1
"""

import itertools
import json
import logging

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

import config

logger = logging.getLogger(__name__)

_ids = itertools.count(1)


class OpenBrainError(Exception):
    """Falha ao chamar o Open Brain MCP: rede, HTTP ou erro JSON-RPC."""


def _build_session() -> requests.Session:
    retry = Retry(
        total=config.HTTP_MAX_RETRIES,
        backoff_factor=config.HTTP_BACKOFF_FACTOR,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["POST"]),
        respect_retry_after_header=True,
        raise_on_status=False,
    )
    session = requests.Session()
    session.mount("https://", HTTPAdapter(max_retries=retry))
    session.mount("http://", HTTPAdapter(max_retries=retry))
    return session


_SESSION = _build_session()


def _parse_sse(text: str) -> dict:
    """Extrai o JSON da linha 'data:' de uma resposta SSE ('event: message')."""
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("data:"):
            payload = line[len("data:"):].strip()
            if payload:
                return json.loads(payload)
    raise OpenBrainError(f"Resposta SSE sem linha 'data:' utilizável: {text[:200]!r}")


def capture_thought(content: str) -> dict:
    """Chama tools/call capture_thought({content}). Retorna o campo 'result'
    da resposta JSON-RPC. Levanta OpenBrainError em qualquer falha (rede,
    HTTP, payload ilegível ou campo 'error' no JSON-RPC) — nunca retorna
    silenciosamente um sucesso falso.
    """
    if not config.OPEN_BRAIN_MCP_URL or not config.OPEN_BRAIN_ACCESS_KEY:
        raise OpenBrainError("OPEN_BRAIN_MCP_URL/OPEN_BRAIN_ACCESS_KEY não configurados.")

    body = {
        "jsonrpc": "2.0",
        "id": next(_ids),
        "method": "tools/call",
        "params": {"name": "capture_thought", "arguments": {"content": content}},
    }
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }

    try:
        resp = _SESSION.post(
            config.OPEN_BRAIN_MCP_URL,
            params={"key": config.OPEN_BRAIN_ACCESS_KEY},
            headers=headers,
            json=body,
            timeout=config.REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
    except requests.RequestException as e:
        raise OpenBrainError(f"Falha de rede/HTTP ao chamar Open Brain: {e}") from e

    content_type = resp.headers.get("content-type", "")
    try:
        payload = _parse_sse(resp.text) if "text/event-stream" in content_type else resp.json()
    except (json.JSONDecodeError, OpenBrainError) as e:
        raise OpenBrainError(f"Resposta ilegível do Open Brain: {e}") from e

    if "error" in payload:
        raise OpenBrainError(f"Open Brain retornou erro JSON-RPC: {payload['error']}")

    return payload.get("result", {})
