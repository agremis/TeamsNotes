"""Script de teste — lista os chats do Teams do usuário autenticado."""

import sys
sys.stdout.reconfigure(encoding="utf-8")

from auth.token_manager import get_access_token
from extractor.teams_client import list_chats


def main():
    print("Autenticando...")
    get_access_token()
    print("OK\n")

    print("Buscando chats...")
    chats = list_chats()
    print(f"Encontrados: {len(chats)} chats\n")

    print(f"{'#':<4} {'Tipo':<11} {'Ult.Msg':<21} {'Ult.Atividade':<21} {'Ult.Update':<21} {'Nome/Membros':<62} {'ID'}")
    print("-" * 198)

    for i, chat in enumerate(chats, 1):
        topic = chat["topic"]
        if len(topic) > 60:
            topic = topic[:57] + "..."
        last_msg = (chat.get("lastMessage") or "")[:19].replace("T", " ") or "(sem msg)"
        last_act = (chat.get("lastActivity") or "")[:19].replace("T", " ") or "-"
        last_upd = (chat.get("lastUpdated") or "")[:19].replace("T", " ")
        print(f"{i:<4} {chat['chatType']:<11} {last_msg:<21} {last_act:<21} {last_upd:<21} {topic:<62} {chat['id']}")


if __name__ == "__main__":
    main()
