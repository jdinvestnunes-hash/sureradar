"""
telegram_setup.py — descobre o CHAT_ID do seu grupo do Telegram.

Como usar:
  1. Crie o bot no @BotFather e copie o TOKEN.
  2. Coloque o TOKEN no arquivo .env (TELEGRAM_BOT_TOKEN=...).
  3. Adicione o bot ao seu grupo do Telegram.
  4. Mande QUALQUER mensagem no grupo (ex.: "oi").
  5. Rode:  python telegram_setup.py
  6. Copie o ID que aparecer e cole no .env como TELEGRAM_CHAT_ID.
"""

import requests

import config


def main():
    token = config.TELEGRAM_BOT_TOKEN
    if not token:
        print("!! Preencha TELEGRAM_BOT_TOKEN no arquivo .env primeiro.")
        return

    url = f"https://api.telegram.org/bot{token}/getUpdates"
    r = requests.get(url, timeout=10)
    dados = r.json()

    if not dados.get("ok"):
        print("!! Erro:", dados)
        return

    encontrados = {}
    for upd in dados.get("result", []):
        msg = upd.get("message") or upd.get("channel_post") or {}
        chat = msg.get("chat")
        if chat:
            encontrados[chat["id"]] = chat.get("title") or chat.get("username") or chat.get("type")

    if not encontrados:
        print("Nenhuma mensagem encontrada. Mande uma mensagem no grupo COM o bot"
              " dentro e rode de novo. (O bot só enxerga mensagens após ser adicionado.)")
        return

    print("Chats encontrados (use o ID no .env como TELEGRAM_CHAT_ID):")
    for cid, nome in encontrados.items():
        print(f"   {cid}   ->   {nome}")


if __name__ == "__main__":
    main()
