"""
Integração com a Marketing API do Facebook (Meta) — puxa o GASTO dos anúncios
por campanha/conjunto pra cruzar com os membros e mostrar o custo por membro.

Config (Railway): META_ACCESS_TOKEN (ads_read), META_AD_ACCOUNT_ID, META_API_VER.
Nunca lançamos exceção "crua" pra fora: devolvemos mensagem amigável em pt-BR.
"""
import requests

import config

_BASE = "https://graph.facebook.com"

# períodos aceitos (mapeiam pro date_preset da API do Facebook)
PRESETS = {
    "hoje": "today",
    "ontem": "yesterday",
    "7dias": "last_7d",
    "30dias": "last_30d",
    "mes": "this_month",
    "tudo": "maximum",
}


def configurado():
    return bool(config.META_ACCESS_TOKEN and config.META_AD_ACCOUNT_ID)


def _ad_account():
    aid = (config.META_AD_ACCOUNT_ID or "").strip()
    if not aid:
        return ""
    return aid if aid.startswith("act_") else "act_" + aid


def gastos(preset="hoje", level="adset"):
    """Devolve [{id, nome, gasto}] com o gasto por campanha/conjunto no período.

    level: 'adset' (conjunto — casa com 1 criativo por campanha interna) ou
           'campaign' (campanha inteira). preset: chave de PRESETS.
    Levanta RuntimeError com mensagem pt-BR se não configurado / erro da API."""
    if not configurado():
        raise RuntimeError("Configure META_ACCESS_TOKEN e META_AD_ACCOUNT_ID no Railway.")
    date_preset = PRESETS.get(preset, "today")
    level = "campaign" if level == "campaign" else "adset"
    campo_id = "campaign_id" if level == "campaign" else "adset_id"
    campo_nome = "campaign_name" if level == "campaign" else "adset_name"
    url = f"{_BASE}/{config.META_API_VER}/{_ad_account()}/insights"
    params = {
        "level": level,
        "fields": f"{campo_id},{campo_nome},spend,impressions,clicks,actions",
        "date_preset": date_preset,
        "limit": 300,
        "access_token": config.META_ACCESS_TOKEN,
    }
    try:
        r = requests.get(url, params=params, timeout=15)
        data = r.json() if r.content else {}
    except requests.RequestException as e:
        raise RuntimeError(f"Falha ao falar com o Facebook: {e}")
    except ValueError:
        raise RuntimeError("Resposta inválida do Facebook.")
    if r.status_code != 200 or "error" in data:
        msg = ((data.get("error") or {}).get("message")) or f"HTTP {r.status_code}"
        raise RuntimeError(f"Facebook recusou: {msg}")
    out = []
    for row in data.get("data", []):
        def _num(v):
            try:
                return float(v or 0)
            except (TypeError, ValueError):
                return 0.0
        # "leads" do Facebook: o mesmo lead vem em VÁRIOS action_type (lead,
        # offsite_conversion.fb_pixel_lead, lead_grouped...). Somar duplicaria.
        # Pegamos UM só, na ordem de prioridade (não somamos).
        acts = {(a.get("action_type") or ""): _num(a.get("value"))
                for a in (row.get("actions") or [])}
        leads = 0
        for tipo in ("offsite_conversion.fb_pixel_lead", "onsite_conversion.lead_grouped",
                     "lead", "leadgen_grouped", "leadgen.other"):
            if tipo in acts:
                leads = int(acts[tipo])
                break
        else:
            cand = [v for t, v in acts.items() if "lead" in t]
            leads = int(max(cand)) if cand else 0
        out.append({
            "id": row.get(campo_id, ""),
            "nome": row.get(campo_nome, ""),
            "gasto": round(_num(row.get("spend")), 2),
            "cliques": int(_num(row.get("clicks"))),
            "impressoes": int(_num(row.get("impressions"))),
            "leads_fb": leads,
        })
    out.sort(key=lambda x: x["gasto"], reverse=True)
    return out


def status_campanhas():
    """{id_campanha: {"status": effective_status, "objetivo": objective}} pra
    mostrar a coluna 'Veiculação' (Ativa/Pausada/Em análise) igual o gerenciador.
    Best-effort: se der erro, devolve {} e o painel só não mostra o status."""
    if not configurado():
        return {}
    url = f"{_BASE}/{config.META_API_VER}/{_ad_account()}/campaigns"
    params = {
        "fields": "id,name,effective_status,objective",
        "limit": 300,
        "access_token": config.META_ACCESS_TOKEN,
    }
    try:
        r = requests.get(url, params=params, timeout=15)
        data = r.json() if r.content else {}
    except (requests.RequestException, ValueError):
        return {}
    if r.status_code != 200 or "error" in data:
        return {}
    out = {}
    for row in data.get("data", []):
        out[row.get("id", "")] = {
            "status": row.get("effective_status", ""),
            "objetivo": row.get("objective", ""),
        }
    return out


def testar():
    """Diagnóstico rápido pra tela de admin."""
    if not configurado():
        return {"ok": False, "erro": "Falta META_ACCESS_TOKEN e/ou META_AD_ACCOUNT_ID."}
    try:
        linhas = gastos(preset="hoje", level="adset")
        return {"ok": True, "conjuntos": len(linhas),
                "total_hoje": round(sum(x["gasto"] for x in linhas), 2)}
    except Exception as e:
        return {"ok": False, "erro": str(e)}
