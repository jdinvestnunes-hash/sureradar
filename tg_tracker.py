"""
tg_tracker.py — conta quantas pessoas ENTRARAM no canal por cada link de convite.

O bot (admin do canal) faz long-polling em getUpdates pedindo os eventos
`chat_member`. Quando alguém entra pelo link de uma campanha, o Telegram manda o
`invite_link` usado — e a gente soma +1 na campanha daquele link (auth.incrementar_membro).

Assim o /admin mostra MEMBROS REAIS por campanha (não só cliques do Facebook).
Precisa: bot admin do canal + TELEGRAM_CHAT_ID setado.
"""

import threading

import requests

import auth
import config

_API = "https://api.telegram.org/bot{}/{}"
_stop = threading.Event()
_thread = None
_offset = 0


def _rodar():
    global _offset
    while not _stop.is_set():
        try:
            if not config.TELEGRAM_BOT_TOKEN:
                _stop.wait(30)
                continue
            body = {"timeout": 25,
                    "allowed_updates": ["chat_member", "message", "my_chat_member"]}
            if _offset:
                body["offset"] = _offset + 1
            r = requests.post(_API.format(config.TELEGRAM_BOT_TOKEN, "getUpdates"),
                              json=body, timeout=35)
            data = r.json()
            for up in data.get("result", []):
                _offset = max(_offset, up["update_id"])
                cm = up.get("chat_member")
                if not cm:
                    continue
                old = (cm.get("old_chat_member") or {}).get("status")
                new = (cm.get("new_chat_member") or {}).get("status")
                link = (cm.get("invite_link") or {}).get("invite_link")
                entrou = new in ("member", "administrator", "creator") and \
                    old in ("left", "kicked", None)
                if entrou and link:
                    auth.incrementar_membro(link)
                    print(f">> +1 membro pelo link {link}")
        except Exception as e:
            print("!! tg_tracker:", e)
            _stop.wait(10)


def iniciar():
    global _thread
    if _thread and _thread.is_alive():
        return
    _stop.clear()
    _thread = threading.Thread(target=_rodar, name="tg-tracker", daemon=True)
    _thread.start()
    print(">> Telegram tracker iniciado (conta membros por link de campanha).")


def parar():
    _stop.set()
