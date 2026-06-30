"""Configuração dos chats a monitorar e parâmetros gerais."""

import os
from dotenv import load_dotenv

load_dotenv()

# Azure AD
AZURE_CLIENT_ID = os.getenv("AZURE_CLIENT_ID")
AZURE_CLIENT_SECRET = os.getenv("AZURE_CLIENT_SECRET")
AZURE_TENANT_ID = os.getenv("AZURE_TENANT_ID")

# LLM
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "anthropic")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-1.5-pro")

# Paths
DB_PATH = os.getenv("DB_PATH", "./storage/messages.db")
BRIEFINGS_PATH = os.getenv("BRIEFINGS_PATH", "./briefings")
OBSIDIAN_VAULT_PATH = os.getenv("OBSIDIAN_VAULT_PATH")

# Saída das páginas HTML de preservação das conversas (uma por chat + índice).
CHATS_HTML_PATH = os.getenv(
    "CHATS_HTML_PATH", r"C:\Vault\Projects\Chats do Trabalho\CHATS"
)

# Microsoft Graph scopes (delegated)
GRAPH_SCOPES = [
    "User.Read",
    "Chat.Read",
    "ChannelMessage.Read.All",
]

# Seleção de chats: dinâmica (não há mais lista fixa). O pipeline descobre os
# chats com atividade recente via list_chats() (ordenado pela última mensagem
# real, lastMessagePreview) e extrai cada um desde o cursor salvo.
#
# Válvula de segurança opcional: processa no máximo os N chats mais recentes por
# execução. None = sem limite (usado no backfill histórico).
MAX_CHATS_PER_RUN = None

# Categorias válidas para classificação
CATEGORIES = [
    "snippet_codigo",
    "lembrete",
    "alinhamento",
    "definicao",
    "link_util",
    "alerta",
]

# Peso de relevância por categoria (para ordenação de seções)
CATEGORY_WEIGHT = {
    "alerta": 5,
    "alinhamento": 4,
    "lembrete": 3,
    "definicao": 2,
    "snippet_codigo": 2,
    "link_util": 1,
}

# Horário de execução (cron local)
NIGHTLY_RUN_TIME = "23:00"

# Dia para resumo semanal
WEEKLY_SUMMARY_DAY = "friday"

# Rate limiting: pausa entre requisições à Graph API (segundos)
API_DELAY = 0.5

# Timeout de cada requisição à Graph API (segundos) — evita travar numa
# conexão pendurada.
REQUEST_TIMEOUT = 30

# Falhas de extração que disparam aborto (provável queda de rede).
MAX_CONSECUTIVE_ERRORS = 15

# Workers paralelos na extração (requisições à Graph são I/O-bound). Threads,
# não processos — o gargalo é rede, não CPU.
EXTRACTION_WORKERS = 5

# LLM: tamanho máximo de lote de mensagens por chamada
LLM_BATCH_SIZE = 50

# Resiliência a rate limit (429) do LLM.
LLM_MAX_RETRIES = int(os.getenv("LLM_MAX_RETRIES", "5"))   # tentativas extras ao 429
LLM_RETRY_DELAY = int(os.getenv("LLM_RETRY_DELAY", "30"))  # espera padrão (s) sem retry-after
# Espaçamento mínimo entre chamadas ao LLM (s). 0 = desligado. Para o free tier do
# Gemini (5 req/min no 3.5-flash) use ~13 para evitar os 429 proativamente.
LLM_MIN_INTERVAL = float(os.getenv("LLM_MIN_INTERVAL", "0"))
