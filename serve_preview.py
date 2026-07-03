"""
serve_preview.py — sobe o painel com dados reais para VISUALIZAÇÃO/screenshot,
SEM ligar o agendador nem enviar Telegram. Uso só de preview/desenvolvimento.

    python serve_preview.py
"""
import os
import sys
try:
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
except (AttributeError, ValueError):
    pass

os.environ["ALLOW_DEV_PRO"] = "1"   # libera a ativação-teste do Pro só no dev

import config
config.AGENDADOR_ATIVO = False   # não roda scheduler nem envia Telegram

import feed
import pipeline

sbs = pipeline.coletar()
feed.set_surebets(sbs, quando="preview")
print(f">> Feed populado com {len(sbs)} surebets (preview, sem Telegram).")

import uvicorn
uvicorn.run("app:app", host="127.0.0.1", port=8000, reload=False)
