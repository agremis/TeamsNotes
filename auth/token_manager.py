"""Gerenciamento de tokens MSAL com device code flow e cache persistente."""

import json
import logging
import os
import threading
import time

import msal

import config

logger = logging.getLogger(__name__)

CACHE_FILE = os.path.join(os.path.dirname(__file__), "..", ".msal_cache.json")
AUTHORITY = f"https://login.microsoftonline.com/{config.AZURE_TENANT_ID}"

# Cache em memória do access token, válido durante a execução. Evita reconstruir
# o app MSAL e reautenticar a cada chat (gargalo na extração de centenas de chats).
# Lock serializa o refresh entre workers paralelos.
_TOKEN: dict = {"value": None, "expires_at": 0.0}
_TOKEN_MARGIN = 300  # renova 5 min antes de expirar
_TOKEN_LOCK = threading.Lock()


def _load_cache() -> msal.SerializableTokenCache:
    cache = msal.SerializableTokenCache()
    if os.path.exists(CACHE_FILE):
        with open(CACHE_FILE, "r") as f:
            cache.deserialize(f.read())
    return cache


def _save_cache(cache: msal.SerializableTokenCache) -> None:
    if cache.has_state_changed:
        with open(CACHE_FILE, "w") as f:
            f.write(cache.serialize())


def _build_app(cache: msal.SerializableTokenCache) -> msal.PublicClientApplication:
    return msal.PublicClientApplication(
        client_id=config.AZURE_CLIENT_ID,
        authority=AUTHORITY,
        token_cache=cache,
    )


def _store_token(result: dict) -> str:
    """Guarda o token no cache em memória e o retorna."""
    expires_in = result.get("expires_in", 3600)
    _TOKEN["value"] = result["access_token"]
    _TOKEN["expires_at"] = time.time() + expires_in - _TOKEN_MARGIN
    return result["access_token"]


class AuthRequired(RuntimeError):
    """Não há token válido no cache e ninguém pode digitar o código.

    O pipeline roda pelo Agendador, sem ninguém na frente do console: abrir um
    device flow ali só faria o run travar ~15 min no polling e morrer depois
    (AADSTS70016 'authorization pending'). Melhor falhar rápido e dizer o que fazer.
    """


def get_access_token() -> str:
    """Obtém um access token válido a partir do cache MSAL (nunca interativo).

    Se o cache não puder renovar sozinho, levanta AuthRequired — cabe ao humano
    rodar `python -m auth.login`. Ver login().
    """
    # Cache em memória: reusa o token enquanto válido, sem reconstruir o MSAL.
    if _TOKEN["value"] and time.time() < _TOKEN["expires_at"]:
        return _TOKEN["value"]

    with _TOKEN_LOCK:
        # Re-checa após o lock: outro worker pode ter renovado enquanto esperávamos.
        if _TOKEN["value"] and time.time() < _TOKEN["expires_at"]:
            return _TOKEN["value"]
        return _acquire_silent()


def _acquire_silent() -> str:
    """Renova pelo refresh token do cache. Levanta AuthRequired se não der."""
    cache = _load_cache()
    app = _build_app(cache)

    accounts = app.get_accounts()
    if accounts:
        logger.info("Tentando token silencioso para %s", accounts[0]["username"])
        result = app.acquire_token_silent(
            scopes=config.GRAPH_SCOPES,
            account=accounts[0],
        )
        if result and "access_token" in result:
            _save_cache(cache)
            return _store_token(result)

    raise AuthRequired(
        "Sem token válido no cache MSAL (expirado, revogado ou primeira execução). "
        "O pipeline não abre device flow sozinho — rode, no console: "
        "python -m auth.login"
    )


def login() -> str:
    """Device code flow INTERATIVO. Requer alguém para digitar o código.

    Ponto de entrada explícito (`python -m auth.login`), separado do caminho do
    pipeline justamente para que um run desatendido nunca caia aqui.
    """
    cache = _load_cache()
    app = _build_app(cache)

    logger.info("Iniciando device code flow...")
    flow = app.initiate_device_flow(scopes=config.GRAPH_SCOPES)
    if "user_code" not in flow:
        raise RuntimeError(f"Falha ao iniciar device flow: {json.dumps(flow, indent=2)}")

    print(f"\n{'='*60}", flush=True)
    print(f"Para autenticar, acesse: {flow['verification_uri']}", flush=True)
    print(f"E insira o código: {flow['user_code']}", flush=True)
    print(f"{'='*60}\n", flush=True)

    result = app.acquire_token_by_device_flow(flow)
    if "access_token" not in result:
        raise RuntimeError(f"Falha na autenticação: {result.get('error_description', result)}")

    _save_cache(cache)
    logger.info("Autenticação bem-sucedida.")
    return _store_token(result)
