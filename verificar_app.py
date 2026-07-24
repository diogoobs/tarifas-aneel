#!/usr/bin/env python3
"""
verificar_app.py — Simula a leitura que o index.html faz do JSON ao vivo.
Replica fielmente tentarCarregarTarifasAoVivo()/pegaValor() para conferir quais
valores o APP realmente exibiria (não só quais existem no JSON).

USO:  python3 verificar_app.py            # confere Enel SP
      python3 verificar_app.py "Light"    # confere outra distribuidora
"""
import json, sys
from datetime import date

OUT = "data/tarifas_aneel.json"
alvo = sys.argv[1] if len(sys.argv) > 1 else "Enel SP"
hoje = date.today().isoformat()
PRIO = ["B3", "B2", "B1", "B4"]

d = json.load(open(OUT, encoding="utf-8"))
dist = d.get("distribuidoras", {}).get(alvo)
if not dist:
    print(f"❌  '{alvo}' não está no JSON. Chaves: {', '.join(sorted(d['distribuidoras']))}")
    sys.exit(1)

lista = dist.get("tarifas", [])
listaB = [t for t in lista
          if str(t.get("subgrupo", "")).startswith("B")
          and "MWh" in str(t.get("unidade", ""))
          and (not t.get("vigencia_fim") or t.get("vigencia_fim") >= hoje)
          and str(t.get("detalhe", "")).strip() not in ("APE", "SCEE", "GD")]

def pegaValor(sub, modal, posto):
    cands = [t for t in listaB
             if t.get("subgrupo") == sub
             and modal.lower() in str(t.get("modalidade", "")).lower()
             and str(t.get("posto", "")).lower().strip() == posto.lower()]
    cands.sort(key=lambda t: t.get("vigencia_inicio", "") or "", reverse=True)
    if not cands:
        return None
    e = cands[0]
    raw = e.get("vlr_total")
    if raw is None:
        raw = (e.get("vlr_tusd") or 0) + (e.get("vlr_te") or 0)
    return round(raw/100000, 6) if raw is not None else None

pt = fp = inter = conv = None
for sub in PRIO:
    p = pegaValor(sub, "branca", "ponta")
    f = pegaValor(sub, "branca", "fora ponta")
    if p is None or f is None:
        continue
    if p >= f - 0.001:
        pt, fp = p, f
        intr = pegaValor(sub, "branca", "intermediário") or pegaValor(sub, "branca", "intermediario")
        if intr is not None and intr >= f - 0.001:
            inter = intr
        else:
            for s2 in PRIO:
                if s2 == sub:
                    continue
                i2 = pegaValor(s2, "branca", "intermediário") or pegaValor(s2, "branca", "intermediario")
                if i2 is not None and i2 >= f - 0.001:
                    inter = i2; break
            if inter is None:
                inter = intr
        print(f"   (branca escolhida no subgrupo {sub})")
        break

for sub in PRIO:
    v = (pegaValor(sub, "convencional", "não se aplica")
         or pegaValor(sub, "convencional", "nao se aplica")
         or pegaValor(sub, "convencional", "fora ponta"))
    if v is not None:
        conv = v; break

print(f"\n═══ O que o app exibiria para {alvo} ═══")
print(f"  Convencional B:            {conv}")
print(f"  Branca ponta:              {pt}")
print(f"  Branca intermediário:      {inter}")
print(f"  Branca fora-ponta:         {fp}")
print("\n  Referência Enel SP (snapshot jul/2025):")
print("    conv 0.72518 | ponta 1.4573 | inter 0.95866 | fora 0.6256")
print("  (esperado: mesma ordem de grandeza, um pouco acima por reajuste 2026)")
