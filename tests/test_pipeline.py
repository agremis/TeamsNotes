"""Teste do pipeline completo: classifier -> briefing_builder com dados mockados.

Simula dois chats distintos para validar o agrupamento por chat e ordenacao
por relevancia no briefing final.
"""

import sys
import os
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")

from processor.classifier import classify
from processor.briefing_builder import build_daily, build_weekly, build_monthly

# Chat 1: Equipe Backend — tem alertas, alinhamentos, snippets (alta relevancia)
CHAT_BACKEND = [
    {"author": "Carlos Silva", "body": "Bom dia pessoal!", "created_at": "2026-06-24T08:01:00Z"},
    {"author": "Ana Costa", "body": "Bom dia! :)", "created_at": "2026-06-24T08:02:00Z"},
    {
        "author": "Carlos Silva",
        "body": "Pessoal, ficou decidido na reuniao de ontem: vamos migrar do PostgreSQL 14 para o 16 no proximo sprint. O Ricardo vai liderar a migracao.",
        "created_at": "2026-06-24T09:15:00Z",
    },
    {
        "author": "Ana Costa",
        "body": "Para quem precisar, a query pra verificar as conexoes ativas no PG:\nSELECT pid, usename, application_name, state FROM pg_stat_activity WHERE state = 'active';",
        "created_at": "2026-06-24T09:30:00Z",
    },
    {"author": "Bruno Oliveira", "body": "kkkkk viram o meme do estagiario que fez DROP TABLE em prod?", "created_at": "2026-06-24T09:45:00Z"},
    {
        "author": "Carlos Silva",
        "body": "ALERTA: o servico de notificacoes esta retornando timeout intermitente desde as 9h. Ja abri um ticket no Jira (INFRA-2847). Monitorando.",
        "created_at": "2026-06-24T10:00:00Z",
    },
    {
        "author": "Ana Costa",
        "body": "Nao esquecer: prazo final pra submeter o RFC do novo sistema de cache e sexta-feira (27/06). Quem ainda nao revisou, por favor olhar ate amanha.",
        "created_at": "2026-06-24T10:30:00Z",
    },
    {
        "author": "Ricardo Mendes",
        "body": "Pra quem nao conhece, Circuit Breaker eh um padrao de resiliencia que impede chamadas repetidas a um servico que esta falhando. Ele abre o circuito apos N falhas e so tenta novamente apos um tempo configurado.",
        "created_at": "2026-06-24T11:00:00Z",
    },
    {
        "author": "Ricardo Mendes",
        "body": "Atualizacao: o timeout do servico de notificacoes foi causado por um memory leak no handler de webhooks. Ja fiz o fix e deploy no staging. Vou monitorar antes de mandar pra prod.",
        "created_at": "2026-06-24T14:00:00Z",
    },
    {
        "author": "Carlos Silva",
        "body": "Config do novo endpoint no nginx:\nlocation /api/v2/notifications {\n    proxy_pass http://notifications-service:8080;\n    proxy_read_timeout 30s;\n    proxy_connect_timeout 5s;\n}",
        "created_at": "2026-06-24T15:30:00Z",
    },
]

# Chat 2: Projeto Mobile — so tem links e lembretes (baixa relevancia)
CHAT_MOBILE = [
    {"author": "Julia Santos", "body": "Bom dia time!", "created_at": "2026-06-24T08:05:00Z"},
    {"author": "Bruno Oliveira", "body": "Alguem vai almocar no japones hoje?", "created_at": "2026-06-24T11:30:00Z"},
    {
        "author": "Julia Santos",
        "body": "Link util: documentacao do novo SDK de autenticacao https://docs.microsoft.com/graph/auth-v2 -- vale a pena ler a secao de refresh tokens",
        "created_at": "2026-06-24T13:00:00Z",
    },
    {"author": "Carlos Silva", "body": "Esse GIF eh muito bom hahaha", "created_at": "2026-06-24T13:15:00Z"},
    {
        "author": "Julia Santos",
        "body": "Lembrete: reuniao de retrospectiva amanha as 10h. Tragam os pontos de melhoria do sprint.",
        "created_at": "2026-06-24T15:00:00Z",
    },
    {"author": "Bruno Oliveira", "body": "Boa tarde, galera! Bom feriado pra quem vai emendar!", "created_at": "2026-06-24T17:00:00Z"},
]


def main():
    today = date(2026, 6, 24)

    print("=" * 60)
    print("TESTE DO PIPELINE: classifier -> briefing_builder")
    print("=" * 60)

    # 1. Classificar cada chat separadamente
    print(f"\n[1/3] Classificando chat 'Equipe Backend' ({len(CHAT_BACKEND)} msgs)...")
    items_backend = classify(CHAT_BACKEND)
    for it in items_backend:
        it["chat_name"] = "Equipe Backend"
    print(f"      -> {len(items_backend)} itens")

    print(f"[2/3] Classificando chat 'Projeto Mobile' ({len(CHAT_MOBILE)} msgs)...")
    items_mobile = classify(CHAT_MOBILE)
    for it in items_mobile:
        it["chat_name"] = "Projeto Mobile"
    print(f"      -> {len(items_mobile)} itens")

    all_items = items_backend + items_mobile

    if not all_items:
        print("Nenhum item retornado pela LLM. Verifique a API key.")
        return

    print(f"\nTotal: {len(all_items)} itens classificados:")
    for it in all_items:
        print(f"  [{it['chat_name']}] [{it['category']}] {it['author']}: {it['content'][:70]}")

    # 2. Gerar briefings
    print("\n[3/3] Gerando briefings...\n")

    daily_md = build_daily(today, all_items)
    print("--- BRIEFING DIARIO ---")
    print(daily_md or "(sem conteudo)")

    start_week = date(2026, 6, 18)
    weekly_md = build_weekly(start_week, today, all_items)
    print("\n--- BRIEFING SEMANAL ---")
    print(weekly_md or "(sem conteudo)")

    monthly_md = build_monthly(2026, 6, all_items)
    print("\n--- BRIEFING MENSAL ---")
    print(monthly_md or "(sem conteudo)")

    print("=" * 60)
    print("Pipeline concluido com sucesso!")
    base = os.getenv("OBSIDIAN_VAULT_PATH") or os.getenv("BRIEFINGS_PATH", "./briefings")
    print(f"Arquivos salvos em {base}/daily/, weekly/ e monthly/")
    print("=" * 60)


if __name__ == "__main__":
    main()
