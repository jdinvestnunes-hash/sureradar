"""
main.py — checagem única pelo terminal (CLI).

Uso:
    python main.py

Roda UMA coleta (todos os mercados, todas as casas configuradas) e imprime as
surebets encontradas. Para o dashboard web, use `python app.py`.
"""

import sys

# Console do Windows usa cp1252 e quebra com emojis/acentos. Força UTF-8.
try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass

import config
from pipeline import coletar


def _classificar(casa_key):
    return config.classificar_casa(casa_key)


def imprimir(sb):
    print("=" * 66)
    print(f"🎯 SUREBET {sb['profit_pct']:.2f}%  |  {sb['event']}")
    print(f"   {sb['sport_label']}  ·  {sb['market_label']}")
    print("-" * 66)
    for p in sb["legs"]:
        print(
            f"   • {p['outcome']:<20} @ {p['odd']:<6} "
            f"[{p['bookmaker']} · {p['bookmaker_type']}]  ->  apostar {p['stake_pct']:.1f}%"
        )
    print("=" * 66 + "\n")


def main():
    print("\n🔎 Detector de Surebets — checagem única...\n")
    print(f"   Ligas: {', '.join(config.ESPORTES)}")
    print(f"   Mercados: {', '.join(config.MERCADOS)}   |   Regiões: {config.REGIOES}")
    print(f"   Lucro mínimo: {config.LUCRO_MINIMO_PCT}%\n")

    surebets = coletar()
    for sb in sorted(surebets, key=lambda s: s["profit_pct"], reverse=True):
        imprimir(sb)

    print(f"Concluído. Surebets encontradas: {len(surebets)}")
    if not surebets:
        print("Nenhuma agora — normal, elas são raras e efêmeras.")


if __name__ == "__main__":
    main()
