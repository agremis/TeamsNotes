"""Entry point do pipeline — extração, classificação e briefing.

Uso:
  python scheduler/run_nightly.py                  # processa o dia anterior
  python scheduler/run_nightly.py --since 2026-06-01  # retroativo desde a data
"""

import argparse
import logging
import sys
import os
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Nomes de chat/mensagem contêm acentos e emojis; sem isto o console cp1252 do
# Windows quebra o logging com UnicodeEncodeError.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import config
from storage.database import (
    create_tables, save_messages, get_unprocessed,
    update_cursor, get_cursor, save_classified_items,
    get_items_for_date, get_items_for_date_range,
    mark_messages_processed, reset_processed, delete_items_for_range,
    get_pending_dates,
)
from auth.token_manager import AuthRequired
from extractor.teams_client import get_messages, list_chats, parse_timestamp, to_utc
from processor.classifier import classify
from processor.briefing_builder import build_daily, build_weekly, build_monthly
from exporter.html_exporter import export as export_html

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)


def _extract_chat(
    chat: dict,
    floor_dt: datetime | None,
    until_dt: datetime | None,
    force: bool = False,
) -> int | None:
    """Extrai e salva as mensagens de um chat.

    Retorna o número de mensagens salvas (0 se nada novo) ou None em caso de
    erro de extração — o chamador usa isso para detectar quedas de rede.
    Thread-safe: cada chamada usa suas próprias conexões SQLite e o token em cache.

    force=True ignora o cursor e re-extrai a janela [floor, until] inteira (para
    recuperar raw_html/imagens de datas já extraídas antes). O cursor nunca
    regride: avança só se o novo valor for maior que o existente.

    Todas as comparações de instante são feitas em datetime (parse_timestamp), não
    em string: os timestamps da Graph têm precisão fracionária variável e a ordem
    lexicográfica não bate com a cronológica.
    """
    chat_id = chat["id"]
    chat_name = chat["topic"]
    # lastActivity (qualquer tipo de msg) é o limite superior seguro: se nem ele
    # passou do cursor, não há mensagem real nova para buscar.
    last_activity = parse_timestamp(chat.get("lastActivity"))

    cursor = to_utc(get_cursor(chat_id))
    # O cursor é EXCLUSIVO: a mensagem exatamente nele já foi extraída. O piso de
    # --since é inclusivo. Sem distinguir, a última mensagem de cada chat voltava
    # da API e era re-salva em toda execução.
    since_exclusive = False
    if force:
        since = floor_dt
    else:
        since = cursor
        since_exclusive = cursor is not None
        if floor_dt and (since is None or since < floor_dt):
            since = floor_dt
            since_exclusive = False

    # Sem atividade nova além da janela pedida — pula (sem chamada à Graph).
    if not last_activity or (since and last_activity <= since):
        return 0

    logger.info("Extraindo mensagens de: %s", chat_name)

    try:
        messages = get_messages(chat_id, since, until_dt, since_exclusive=since_exclusive)
    except Exception as e:
        logger.error("Erro ao extrair de %s: %s", chat_name, e)
        return None

    real_latest = None
    if messages:
        save_messages(messages, chat_id, chat_name)
        real_latest = max(
            (ts for ts in (parse_timestamp(m["created_at"]) for m in messages) if ts),
            default=None,
        )

    # Avança o cursor sem regredir. No modo diário (sem --until), leva até a última
    # atividade vista — inclusive eventos de sistema — mesmo sem mensagem real nova.
    # No backfill (--until), só com mensagem real, para não estourar a janela.
    candidates = [real_latest] if until_dt else [real_latest, last_activity]
    candidates.append(cursor)
    new_cursor = max((c for c in candidates if c), default=None)
    if new_cursor:
        update_cursor(chat_id, new_cursor.isoformat())

    if not messages:
        return 0

    logger.info("  -> %s: %d mensagens extraidas", chat_name, len(messages))
    return len(messages)


def run_extraction(
    since_floor: date | None = None, until: date | None = None, force: bool = False
) -> tuple[int, set[str]]:
    """Descobre dinamicamente os chats com atividade e extrai suas mensagens.

    A seleção vem de list_chats() (ordenado por última mensagem real). Por chat,
    extrai desde max(cursor, since_floor) até 'until' (passados a get_messages).
    Cursor e processamento ficam desacoplados: o cursor marca o que já foi
    extraído; o processamento por dia é guiado por intervalo + flag.

    Extrai os chats em paralelo (config.EXTRACTION_WORKERS) — requisições à Graph
    são I/O-bound. Aborta se acumular muitas falhas (provável queda de rede),
    cancelando o restante em vez de percorrer todos os chats inutilmente.

    Retorna (total de mensagens, conjunto de chat_ids que ganharam mensagens) —
    o segundo é usado para regenerar só as páginas HTML tocadas.
    """
    floor_dt = to_utc(datetime.combine(since_floor, datetime.min.time())) if since_floor else None
    until_dt = to_utc(datetime.combine(until, datetime.max.time())) if until else None

    chats = list_chats()
    if config.MAX_CHATS_PER_RUN:
        chats = chats[: config.MAX_CHATS_PER_RUN]

    n = len(chats)
    workers = max(1, config.EXTRACTION_WORKERS)
    logger.info("Chats descobertos: %d (extraindo com %d workers)", n, workers)

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(_extract_chat, chat, floor_dt, until_dt, force): chat["id"]
            for chat in chats
        }
        return _consume_extraction(futures, n)


def _future_result(future) -> int | None:
    """Resultado de um worker, tratando exceção inesperada como falha (None)."""
    try:
        return future.result()
    except Exception as e:  # defensivo: não deixar um worker derrubar o run
        logger.error("Erro inesperado em worker: %s", e)
        return None


def _consume_extraction(futures: dict, n: int) -> tuple[int, set[str]]:
    """Consome os futures da extração: soma mensagens, coleta chats tocados e
    aborta se acumular muitas falhas (cancelando o restante)."""
    total = 0
    errors = 0
    done = 0
    touched: set[str] = set()

    for future in as_completed(futures):
        done += 1
        result = _future_result(future)
        if result is None:
            errors += 1
            if errors >= config.MAX_CONSECUTIVE_ERRORS:
                logger.error("%d falhas — provavel queda de rede. Cancelando extracao.", errors)
                for f in futures:
                    f.cancel()
                break
            continue
        total += result
        if result > 0:
            touched.add(futures[future])
        if done % 100 == 0:
            logger.info("Progresso: %d/%d chats", done, n)

    return total, touched


def process_day(target_date: date) -> None:
    """Classifica mensagens de um dia e gera o briefing diário."""
    date_str = target_date.isoformat()

    pending = get_unprocessed(date_str)
    if not pending:
        return

    logger.info("Processando %s: %d mensagens pendentes", date_str, len(pending))

    # Classifica por chat para preservar a origem de cada item (seções por chat
    # no briefing). Misturar chats numa só chamada perderia o chat_name.
    by_chat = defaultdict(list)
    for m in pending:
        by_chat[m["chat_id"]].append(m)

    all_items = []
    for msgs in by_chat.values():
        chat_name = msgs[0].get("chat_name") or "Geral"
        items = classify(msgs)
        for item in items:
            item["chat_name"] = chat_name
        all_items.extend(items)

    if all_items:
        save_classified_items(all_items, date_str)

    mark_messages_processed([m["id"] for m in pending])

    db_items = get_items_for_date(date_str)
    if db_items:
        md = build_daily(target_date, db_items)
        if md:
            logger.info("Briefing diario gerado para %s", date_str)


def run_pipeline(
    since: date | None = None,
    until: date | None = None,
    no_export: bool = False,
    no_classify: bool = False,
    force: bool = False,
    reprocess: bool = False,
    backlog: int | None = None,
) -> bool:
    """Executa o pipeline. Retorna False se a extração falhou por completo
    (rede/Graph) — o chamador usa isso para sair com código != 0 e permitir que
    o Agendador reexecute. Os briefings do que já está no banco são gerados de
    qualquer forma."""
    logger.info("=" * 60)
    logger.info("Iniciando pipeline — %s", datetime.now().isoformat())
    logger.info("=" * 60)

    create_tables()

    touched: set[str] = set()
    extraction_failed = False
    if reprocess:
        # Reclassificar um período já processado com a engine atual: limpa os
        # itens antigos e remarca as mensagens como pendentes. Não re-extrai.
        end = until or (date.today() - timedelta(days=1))
        n_msg = reset_processed(since.isoformat(), end.isoformat())
        n_items = delete_items_for_range(since.isoformat(), end.isoformat())
        logger.info(
            "Reprocesso (%s a %s): %d mensagens remarcadas, %d itens removidos. "
            "Reclassificando com provider '%s'.",
            since.isoformat(), end.isoformat(), n_msg, n_items, config.LLM_PROVIDER,
        )
    else:
        # Extrair mensagens
        try:
            extracted, touched = run_extraction(since_floor=since, until=until, force=force)
            logger.info("Total extraido: %d mensagens em %d chats", extracted, len(touched))
        except AuthRequired as e:
            extraction_failed = True
            logger.error("AUTENTICACAO EXPIRADA — nada foi extraido. %s", e)
        except Exception as e:
            extraction_failed = True
            logger.error("Falha na extracao: %s", e)

    # Classificar/gerar briefings por dia (a menos que --no-classify).
    if not no_classify:
        yesterday = date.today() - timedelta(days=1)
        start = since or yesterday
        end = min(until, yesterday) if until else yesterday
        feitas: set[str] = set()
        current = start
        while current <= end:
            try:
                process_day(current)
            except Exception as e:
                logger.error("Falha ao processar %s: %s", current.isoformat(), e)
            feitas.add(current.isoformat())
            current += timedelta(days=1)

        _process_backlog(backlog if backlog is not None else config.BACKLOG_DAYS_PER_RUN, feitas)

        today = date.today()
        _maybe_build_weekly(today)
        _maybe_build_monthly(today)

    # Preservação HTML: regenera as páginas dos chats tocados (histórico completo).
    if not no_export and touched:
        try:
            export_html(chat_ids=sorted(touched))
        except Exception as e:
            logger.error("Falha no export HTML: %s", e)

    if extraction_failed:
        logger.error("Pipeline concluido COM FALHA na extracao — saindo com codigo 1.")
    else:
        logger.info("Pipeline concluido.")
    return not extraction_failed


def _process_backlog(limit: int, ja_feitas: set[str]) -> None:
    """Drena datas órfãs: passadas, com mensagens que nunca foram classificadas.

    process_day só cobre o intervalo do run (por padrão, ontem), então tudo que o
    backfill trouxe para datas antigas ficava pendente para sempre. Drena da data
    mais recente para a mais antiga — as lacunas recentes valem mais.

    Limitado por execução porque classificar CUSTA COTA DE LLM: são ~159 mil
    mensagens em ~1.300 datas. limit=0 desliga (padrão).
    """
    if limit <= 0:
        return

    # Só datas que o fluxo diário nunca mais vai visitar: as já cobertas por este
    # run (ja_feitas) e HOJE ficam de fora — hoje ainda está acumulando mensagens
    # e será classificado amanhã, como 'ontem'. Sem este corte, o dreno gastaria
    # cota reclassificando um dia pela metade.
    hoje = date.today().isoformat()
    pendentes = [
        d for d in get_pending_dates(newest_first=True)
        if d < hoje and d not in ja_feitas
    ]
    if not pendentes:
        logger.info("Backlog vazio — nada orfao a classificar.")
        return

    alvo = pendentes[:limit]
    logger.info(
        "Backlog: %d datas orfas pendentes. Processando %d nesta execucao (%s).",
        len(pendentes), len(alvo), ", ".join(alvo),
    )
    for d in alvo:
        try:
            process_day(date.fromisoformat(d))
        except Exception as e:
            logger.error("Falha ao processar backlog %s: %s", d, e)

    restantes = len(pendentes) - len(alvo)
    if restantes:
        logger.info("Backlog: %d datas ainda pendentes.", restantes)


def _maybe_build_weekly(today: date) -> None:
    """Gera o briefing semanal se hoje for o dia configurado."""
    if today.strftime("%A").lower() != config.WEEKLY_SUMMARY_DAY:
        return
    try:
        week_start = today - timedelta(days=6)
        week_items = get_items_for_date_range(week_start.isoformat(), today.isoformat())
        if week_items:
            build_weekly(week_start, today, week_items)
            logger.info("Briefing semanal gerado")
    except Exception as e:
        logger.error("Falha no briefing semanal: %s", e)


def _maybe_build_monthly(today: date) -> None:
    """Gera o briefing mensal se hoje for o último dia do mês."""
    if (today + timedelta(days=1)).month == today.month:
        return
    try:
        month_start = date(today.year, today.month, 1)
        month_items = get_items_for_date_range(month_start.isoformat(), today.isoformat())
        if month_items:
            build_monthly(today.year, today.month, month_items)
            logger.info("Briefing mensal gerado")
    except Exception as e:
        logger.error("Falha no briefing mensal: %s", e)


def main():
    parser = argparse.ArgumentParser(description="Pipeline de briefings do Teams")
    parser.add_argument(
        "--since",
        type=str,
        help="Data inicial para processamento retroativo (YYYY-MM-DD)",
    )
    parser.add_argument(
        "--until",
        type=str,
        help="Data final do backfill (YYYY-MM-DD). Limita extracao e processamento.",
    )
    parser.add_argument(
        "--no-export",
        action="store_true",
        help="Não gera as páginas HTML ao final (só extração + briefings).",
    )
    parser.add_argument(
        "--no-classify",
        action="store_true",
        help="Pula a classificação/briefings (LLM). Só extrai e gera HTML — não gasta cota.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Ignora os cursores e re-extrai a janela [--since, --until] inteira "
             "(recupera raw_html/imagens de datas já extraídas).",
    )
    parser.add_argument(
        "--backlog",
        type=int,
        metavar="N",
        help="Classifica N datas orfas (passadas, com mensagens nunca processadas), "
             "da mais recente para a mais antiga. GASTA COTA DE LLM. "
             "Padrao: config.BACKLOG_DAYS_PER_RUN (0 = desligado).",
    )
    parser.add_argument(
        "--reprocess",
        action="store_true",
        help="Reclassifica um período JÁ processado com a engine atual: limpa os "
             "briefings/itens do intervalo e remarca as mensagens (não re-extrai). "
             "Exige --since.",
    )
    args = parser.parse_args()

    if args.reprocess and not args.since:
        parser.error("--reprocess exige --since (intervalo a reprocessar).")

    since = None
    if args.since:
        since = date.fromisoformat(args.since)
        logger.info("Modo retroativo: processando desde %s", since.isoformat())

    until = None
    if args.until:
        until = date.fromisoformat(args.until)
        logger.info("Limite final do backfill: %s", until.isoformat())

    ok = run_pipeline(
        since=since, until=until, no_export=args.no_export,
        no_classify=args.no_classify, force=args.force, reprocess=args.reprocess,
        backlog=args.backlog,
    )
    # Exit code != 0 sinaliza a falha ao Agendador de Tarefas (que pode ser
    # configurado para reiniciar em caso de falha), em vez de mascará-la.
    if not ok:
        sys.exit(1)


if __name__ == "__main__":
    main()
