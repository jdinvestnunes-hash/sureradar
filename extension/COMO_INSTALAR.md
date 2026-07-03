# SureRadar Bridge — extensão do Chrome

Essa extensão lê as surebets da sua conta **surebet.com** e alimenta o painel
**SureRadar** local automaticamente, **a cada 10 minutos** — sem download, sem
travar, e continua funcionando mesmo se você recarregar a página.

## Como instalar (1 minuto)

1. Deixe o painel rodando: na pasta do projeto, `python serve_preview.py`
   (ou `python app.py` quando for ao vivo).
2. No Chrome, abra: `chrome://extensions`
3. Ligue o **"Modo do desenvolvedor"** (canto superior direito).
4. Clique em **"Carregar sem compactação"** (Load unpacked).
5. Selecione a pasta **`extension`** (esta pasta).
6. Pronto! Abra e **fique logado** em `https://pt.surebet.com/surebets`.

A partir daí, a cada 10 min a extensão manda as surebets pro painel sozinha.
Você pode ver os logs em `chrome://extensions` → SureRadar Bridge → "service worker".

## Como funciona

- `content.js` roda na aba da surebet.com, lê as apostas da tela.
- `background.js` (service worker) envia pro painel em `http://localhost:8000/api/ingest`.
- O painel espelha automaticamente: **casas, esportes e faixa de lucro** que
  estiverem na sua conta.

## Ajustes

- Intervalo: mude `INTERVALO_MS` em `content.js` (padrão 10 min).
- Endereço do painel: mude `SAAS_URL` em `background.js` se não for localhost:8000.
