"""Despeja em Markdown o que foi escrito num chat do Teams, agora.

Para usar como contexto na hora de resumir uma reunião (ex.: a gravação da
daily): o chat da reunião costuma ter links, bloqueios e decisões que não foram
ditos em voz alta. Lê direto da Graph — mais fresco que o último run do
pipeline — e escreve no stdout ou num arquivo. Não grava no banco, não mexe em
cursor e não chama a LLM: custo zero de tokens aqui.

O chat é achado pelo nome (trecho, sem diferenciar maiúsculas). Vários chats
com o mesmo nome (ex.: uma série de reuniões recriada) -> usa o de atividade
mais recente e lista os outros; --all inclui todos.

Uso:
  python scheduler/chat_context.py "[AOP Core] Daily"                # hoje
  python scheduler/chat_context.py "AOP Core" "Delivery Team"         # vários
  python scheduler/chat_context.py "Delivery Team" --since 2026-11-24
  python scheduler/chat_context.py "[AOP Core] Daily" --hours 3 --out ctx.md
"""

import argparse
import html
import os
import sys
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from auth.token_manager import AuthRequired
from extractor.teams_client import get_messages, list_chats, parse_timestamp, to_utc
from storage.database import find_chats


def _local(ts: datetime) -> datetime:
    """UTC -> fuso da máquina (a Graph devolve UTC; a reunião é no horário local)."""
    return ts.astimezone()


def resolve(name: str, include_all: bool = False) -> list[dict]:
    """[{chat_id, chat_name}] que casam com 'name'.

    Procura primeiro no banco (rápido, e acha o chat mesmo se foi renomeado);
    só se nada casar varre /me/chats — caso de um chat novo que o pipeline
    ainda não extraiu."""
    found = find_chats(name)
    if not found:
        needle = name.lower()
        found = [
            {"chat_id": c["id"], "chat_name": c["topic"]}
            for c in list_chats()
            if needle in (c.get("topic") or "").lower()
        ]
    if len(found) > 1 and not include_all:
        others = ", ".join(f"{c['chat_name']!r}" for c in found[1:4])
        print(
            f"'{name}' casou com {len(found)} chats; usando o mais recente "
            f"({found[0]['chat_name']!r}). Outros: {others}. Use --all para todos.",
            file=sys.stderr,
        )
        found = found[:1]
    return found


def render(chat: dict, since: datetime, messages: list[dict]) -> str:
    """Markdown de um chat: mensagens desde 'since', da mais antiga para a mais nova."""
    timed = [(ts, m) for m in messages if (ts := parse_timestamp(m["created_at"]))]
    timed.sort(key=lambda pair: pair[0])

    lines = [f"# {chat['chat_name']}", "", f"_Desde {_local(since):%d/%m/%Y %H:%M}_", ""]
    for ts, m in timed:
        text = html.unescape(m["body"]).replace("\xa0", " ").replace("\n", "\n  ")
        lines.append(f"- {_local(ts):%H:%M} **{m['author']}**: {text}")
    if not timed:
        lines.append("_(nada escrito no período)_")
    lines.append("")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Contexto escrito num chat do Teams (Markdown)")
    parser.add_argument("names", nargs="+", help="Nome do chat (ou um trecho dele).")
    window = parser.add_mutually_exclusive_group()
    window.add_argument("--since", help="Data inicial (AAAA-MM-DD, 00:00 local). Padrão: hoje.")
    window.add_argument("--hours", type=float, help="Janela das últimas N horas.")
    parser.add_argument("--all", action="store_true", help="Inclui todos os chats que casarem.")
    parser.add_argument("--out", help="Grava neste arquivo em vez do stdout.")
    args = parser.parse_args()

    if args.hours:
        since = datetime.now().astimezone() - timedelta(hours=args.hours)
    else:
        day = date.fromisoformat(args.since) if args.since else date.today()
        since = datetime.combine(day, datetime.min.time()).astimezone()
    since = to_utc(since)

    parts = []
    try:
        for name in args.names:
            chats = resolve(name, args.all)
            if not chats:
                raise SystemExit(f"Nenhum chat com '{name}' no nome.")
            for chat in chats:
                parts.append(render(chat, since, get_messages(chat["chat_id"], since)))
    except AuthRequired as e:
        raise SystemExit(f"Autenticacao expirada: {e}")

    md = "\n".join(parts)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(md)
        print(f"Gravado em {args.out}")
    else:
        print(md)


if __name__ == "__main__":
    main()
