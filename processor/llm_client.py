"""Cliente LLM unificado — suporta Anthropic e Google Gemini via config."""

import logging
import re
import sys
import time

import config

logger = logging.getLogger(__name__)

_VALID_PROVIDERS = ("anthropic", "gemini")
_last_call = [0.0]  # timestamp da última chamada (para espaçamento)


def _check_provider() -> str:
    provider = config.LLM_PROVIDER
    if provider not in _VALID_PROVIDERS:
        print(
            f"ERRO: LLM_PROVIDER='{provider}' invalido. "
            f"Valores aceitos: {', '.join(_VALID_PROVIDERS)}",
            file=sys.stderr,
        )
        sys.exit(1)
    return provider


def _is_rate_limit(err: Exception) -> bool:
    s = str(err).lower()
    return "429" in s or "quota" in s or "resourceexhausted" in s or "rate limit" in s


def _is_daily_quota(err: Exception) -> bool:
    """Cota DIÁRIA esgotada — não adianta esperar/retentar (não reseta em minutos)."""
    s = str(err).lower()
    return "perday" in s or "requestsperday" in s or "per_day" in s


def _retry_after(err: Exception, default: int) -> int:
    """Extrai o retry-after sugerido pelo erro (ex.: 'seconds: 51'), com teto."""
    m = re.search(r"seconds:\s*(\d+)", str(err))
    return min((int(m.group(1)) + 2) if m else default, 120)


def _pace() -> None:
    """Garante o espaçamento mínimo configurado entre chamadas ao LLM."""
    if config.LLM_MIN_INTERVAL > 0:
        wait = config.LLM_MIN_INTERVAL - (time.monotonic() - _last_call[0])
        if wait > 0:
            time.sleep(wait)
    _last_call[0] = time.monotonic()


def classify(system_prompt: str, user_prompt: str) -> str:
    """Envia prompt ao provider configurado e retorna o texto da resposta.

    Resiliente a rate limit (429): espera o retry-after sugerido e tenta de novo,
    até config.LLM_MAX_RETRIES vezes.
    """
    provider = _check_provider()
    call = _call_anthropic if provider == "anthropic" else _call_gemini

    for attempt in range(config.LLM_MAX_RETRIES + 1):
        _pace()
        try:
            return call(system_prompt, user_prompt)
        except Exception as e:
            # Cota diária esgotada: retentar é inútil (só reseta em horas) — falha já.
            if _is_daily_quota(e):
                logger.error(
                    "Cota DIÁRIA do LLM esgotada (%s). Troque de modelo/plano e tente "
                    "amanhã ou em outro modelo.", config.GEMINI_MODEL,
                )
                raise
            if _is_rate_limit(e) and attempt < config.LLM_MAX_RETRIES:
                wait = _retry_after(e, config.LLM_RETRY_DELAY)
                logger.warning(
                    "Rate limit do LLM (tentativa %d/%d) — aguardando %ds...",
                    attempt + 1, config.LLM_MAX_RETRIES, wait,
                )
                time.sleep(wait)
                continue
            raise


def _call_anthropic(system_prompt: str, user_prompt: str) -> str:
    import anthropic

    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    response = client.messages.create(
        model=config.ANTHROPIC_MODEL,
        max_tokens=4096,
        system=system_prompt,
        messages=[{"role": "user", "content": user_prompt}],
    )
    return response.content[0].text.strip()


def _call_gemini(system_prompt: str, user_prompt: str) -> str:
    import google.generativeai as genai

    genai.configure(api_key=config.GEMINI_API_KEY)
    model = genai.GenerativeModel(
        model_name=config.GEMINI_MODEL,
        system_instruction=system_prompt,
    )
    response = model.generate_content(user_prompt)
    return response.text.strip()
