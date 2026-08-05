"""Orquestra a sincronização de um briefing .md com o Open Brain.

Etapa adicional ao pipeline principal: nunca levanta exceção para o chamador
— qualquer falha (config ausente, arquivo ilegível, rede/HTTP) é logada e
contabilizada, sem interromper a geração normal do briefing.
"""

import logging
import os
from datetime import datetime

import config
from openbrain.client import OpenBrainError, capture_thought
from openbrain.parser import parse_briefing

logger = logging.getLogger(__name__)

_LOG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs", "open_brain_sync.log"
)


def _log_run(file_path: str, ok: int, fail: int) -> None:
    os.makedirs(os.path.dirname(_LOG_PATH), exist_ok=True)
    line = f"{datetime.now().isoformat(timespec='seconds')} {file_path} ok={ok} fail={fail}\n"
    with open(_LOG_PATH, "a", encoding="utf-8") as f:
        f.write(line)


def sync_file(file_path: str) -> tuple[int, int]:
    """Sincroniza um arquivo .md de briefing com o Open Brain: uma chamada
    capture_thought por seção (`##`) com conteúdo.

    Retorna (secoes_ok, secoes_falha). Nunca levanta — falhas de config,
    leitura ou rede/HTTP são logadas e refletidas na contagem de falhas.
    """
    if not config.OPEN_BRAIN_MCP_URL or not config.OPEN_BRAIN_ACCESS_KEY:
        logger.warning(
            "Open Brain não configurado (OPEN_BRAIN_MCP_URL/OPEN_BRAIN_ACCESS_KEY) — sync pulado."
        )
        return 0, 0

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            md_text = f.read()
    except OSError as e:
        logger.error("Open Brain sync: não foi possível ler %s: %s", file_path, e)
        return 0, 0

    sections = parse_briefing(md_text, file_path)
    if not sections:
        logger.info("Open Brain sync: nenhuma seção com conteúdo em %s.", file_path)
        return 0, 0

    ok = 0
    fail = 0
    for item in sections:
        try:
            capture_thought(item["paragraph"])
            ok += 1
        except OpenBrainError as e:
            fail += 1
            logger.error("Open Brain sync: falha ao capturar seção '%s': %s", item["section"], e)

    logger.info(
        "Open Brain sync (%s): %d seção(ões) capturada(s), %d falha(s).", file_path, ok, fail
    )
    _log_run(file_path, ok, fail)
    return ok, fail
