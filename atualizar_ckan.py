#!/usr/bin/env python3
"""
atualizar_ckan.py — Extrator direto da ANEEL Dados Abertos (CKAN)
==================================================================
Substitui o caminho antigo (proxy Cloudflare + xlsx manual + Playwright).
Baixa o CSV oficial de "Tarifas de aplicação das distribuidoras de energia
elétrica" direto do portal de Dados Abertos e regenera data/tarifas_aneel.json.

IMPORTANTE — re-chaveamento:
O resource do CKAN identifica a distribuidora por CÓDIGO (SigAgente, ex.:
ELETROPAULO, COELBA). Mas o app Tarifa Justa casa pela CHAVE = nome comercial
(ex.: "Enel SP", "Neoenergia Coelba"). Publicar com os códigos crus QUEBRA a
busca de distribuidora no app. Por isso este script re-chaveia via RENOMEAR,
descarta linhas inválidas (Não Informado / Base Econômica) e mescla duplicatas,
reproduzindo o conjunto de nomes comerciais que o app espera.

Roda 100% no seu Mac (IP residencial não é bloqueado pela ANEEL).

INSTALAÇÃO:  pip install requests pandas
USO:         python3 atualizar_ckan.py

Fonte: https://dadosabertos.aneel.gov.br/dataset/tarifas-distribuidoras-energia-eletrica
Resource id: fcf2906c-7c32-4b9b-a637-054e7a5234f4
"""

import argparse
import json
import sys
import tempfile
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

try:
    import requests
except ImportError:
    print("❌  Execute: pip install requests"); sys.exit(1)
try:
    import pandas as pd
except ImportError:
    print("❌  Execute: pip install pandas"); sys.exit(1)

# Reutiliza a lógica já validada que gerou o JSON atual
sys.path.insert(0, str(Path(__file__).resolve().parent / "scripts"))
from extrator_tarifas_aneel import processar, normaliza_reh  # noqa: E402

RESOURCE_ID = "fcf2906c-7c32-4b9b-a637-054e7a5234f4"
DATASET_SLUG = "tarifas-distribuidoras-energia-eletrica"
FILENAME = "tarifas-homologadas-distribuidoras-energia-eletrica.csv"
DEFAULT_OUTPUT = "data/tarifas_aneel.json"

# O app consome os valores como `vlr ÷ 100000 = R$/kWh`. O CKAN entrega em
# R$/MWh (ou R$/kW) puro. Para R$/kWh correto: R$MWh/1000 = (R$MWh×100)/100000.
# Logo, guardamos os valores multiplicados por 100 (escala que o app espera).
ESCALA_APP = 100

URLS = [
    f"https://dadosabertos.aneel.gov.br/dataset/{DATASET_SLUG}/resource/{RESOURCE_ID}/download/{FILENAME}",
    f"https://dadosabertos.aneel.gov.br/datastore/dump/{RESOURCE_ID}?bom=True",
]
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "text/csv,application/octet-stream,*/*",
}


def norm(s: str) -> str:
    s = unicodedata.normalize("NFD", str(s or "")).encode("ascii", "ignore").decode()
    return " ".join(s.lower().split())


# ─── Mapa CÓDIGO CKAN (normalizado) → NOME COMERCIAL esperado pelo app ─────────
# Só as distribuidoras aqui entram no JSON final (whitelist = conjunto que o app
# conhece). Quem não estiver aqui é descartado (cooperativas minúsculas etc.).
# A única que PRECISA estar certa hoje é a Enel SP (= ELETROPAULO), única
# distribuidora na whitelist do app. As demais são para o futuro / lista "Disponíveis".
RENOMEAR = {
    "ambar amazonas": "Amazonas Energia",
    "ambar energia rr": "Roraima Energia",
    "cpfl-paulista": "CPFL Paulista",
    "cpfl-piratining": "CPFL Piratininga",
    "cpfl santa cruz": "CPFL Santa Cruz",
    "celesc": "Celesc-DIS",
    "cemig-d": "Cemig-D",
    "chesp": "Chesp",
    "cocel": "Cocel",
    "cooperalianca": "Cooperaliança",
    "copel-dis": "Copel-DIS",
    "dmed": "DMED",
    "dcelt": "Dcelt",
    "demei": "Demei",
    "edp es": "EDP ES",
    "edp sp": "EDP SP",
    "efljc": "EFLJC",
    "elfsm": "ELFSM",
    "eflul": "Eflul",
    "eletrocar": "Eletrocar",
    "enel ce": "Enel CE",
    "enel rj": "Enel RJ",
    "eletropaulo": "Enel SP",
    "eac": "Energisa AC",
    "ems": "Energisa MS",
    "emt": "Energisa MT",
    "emr": "Energisa Minas Rio",
    "epb": "Energisa PB",
    "ero": "Energisa RO",
    "ese": "Energisa SE",
    "ess": "Energisa Sul Sudeste",
    "eto": "Energisa TO",
    "cea": "Equatorial CEA",
    "ceee-d": "Equatorial CEEE",
    "equatorial go": "Equatorial GO",
    "equatorial ma": "Equatorial MA",
    "equatorial pa": "Equatorial PA",
    "equatorial pi": "Equatorial PI",
    "hidropan": "Hidropan",
    "light sesa": "Light",
    "muxenergia": "MuxEnergia",
    "neoenergia brasilia": "Neoenergia Brasília",
    "coelba": "Neoenergia Coelba",
    "cosern": "Neoenergia Cosern",
    "elektro": "Neoenergia Elektro",
    "neoenergia pe": "Neoenergia Pernambuco",
    "uhenpal": "Nova Palma",
    "pacto energia pr": "Pacto Energia",
    "rge": "RGE",
    "rge sul": "RGE",
    "sulgipe": "Sulgipe",
}


def baixar_csv() -> str:
    ultimo_erro = None
    for url in URLS:
        print(f"📡  Baixando de: {url}")
        try:
            with requests.get(url, headers=HEADERS, timeout=180, stream=True) as resp:
                if resp.status_code != 200:
                    print(f"   ↳ HTTP {resp.status_code}, tento a próxima…")
                    ultimo_erro = f"HTTP {resp.status_code} em {url}"
                    continue
                tmp = Path(tempfile.gettempdir()) / "aneel_ckan.csv"
                total = 0
                with open(tmp, "wb") as f:
                    for chunk in resp.iter_content(chunk_size=1 << 16):
                        if chunk:
                            f.write(chunk); total += len(chunk)
                print(f"   ✅ {total/1024:.0f} KB")
                if total < 10_000:
                    ultimo_erro = f"resposta muito curta ({total} bytes)"
                    print("   ↳ poucos bytes, tento a próxima…"); continue
                for sep in (";", ",", None):
                    try:
                        df = pd.read_csv(tmp, sep=sep, dtype=str,
                                         engine="python" if sep is None else "c",
                                         encoding="utf-8", on_bad_lines="skip")
                        if df.shape[1] > 3:
                            print(f"   ✅ {len(df):,} linhas × {df.shape[1]} colunas (sep={sep!r})")
                            return df.to_csv(sep=";", index=False)
                    except Exception:
                        continue
                ultimo_erro = f"não consegui parsear o CSV de {url}"
        except requests.RequestException as e:
            ultimo_erro = f"{type(e).__name__}: {e}"
            print(f"   ↳ falhou: {ultimo_erro}"); continue
    print(f"❌  Nenhuma fonte respondeu. Último erro: {ultimo_erro}"); sys.exit(1)


def rechavear(resultado: dict) -> tuple[dict, list]:
    """Aplica a whitelist RENOMEAR, descarta inválidos, mescla duplicatas
    (mantém a de REH mais recente). Devolve (novo_dict, descartados)."""
    novo, descartados = {}, []
    for sigla, d in resultado.items():
        nome = RENOMEAR.get(norm(sigla))
        if not nome or norm(sigla) in ("nao informado", ""):
            descartados.append(sigla); continue
        d = dict(d); d["sigla"] = nome
        # Reescala para a convenção que o app consome (vlr ÷ 100000 = R$/kWh)
        novas_tarifas = []
        for t in d.get("tarifas", []):
            t = dict(t)
            for campo in ("vlr_tusd", "vlr_te", "vlr_total"):
                if t.get(campo) is not None:
                    t[campo] = round(t[campo] * ESCALA_APP, 4)
            novas_tarifas.append(t)
        d["tarifas"] = novas_tarifas
        if nome in novo:
            # colisão (ex.: dois casings de CPFL Santa Cruz): fica a REH mais nova
            atual = normaliza_reh(novo[nome].get("reh") or "")
            novo_reh = normaliza_reh(d.get("reh") or "")
            if novo_reh >= atual:
                novo[nome] = d
        else:
            novo[nome] = d
    return novo, descartados


def sanidade_enel(dist: dict):
    """Imprime as tarifas da Enel SP (Branca B, fora ponta, MWh) já na escala
    do app (÷100000 = R$/kWh). Referência jul/2025: fora ponta ≈ 0.6256."""
    if not dist:
        print("   ⚠️  Enel SP não saiu no resultado — NÃO publique."); return
    print(f"   Enel SP · vigência {dist.get('vigencia_inicio')} · {dist.get('reh','')[:40]}")
    cands = [t for t in dist.get("tarifas", [])
             if str(t.get("subgrupo", "")).startswith("B")
             and "MWh" in str(t.get("unidade", ""))
             and "branca" in str(t.get("modalidade", "")).lower()
             and str(t.get("posto", "")).lower().strip() == "fora ponta"]
    if not cands:
        print("   ⚠️  Enel SP sem tarifa Branca B fora-ponta MWh — confira."); return
    vistos = set()
    for t in cands:
        tot = t.get("vlr_total")
        chave = (t.get("subgrupo"), t.get("subclasse"), tot)
        if chave in vistos:
            continue
        vistos.add(chave)
        rkwh = round(tot/100000, 5) if tot is not None else None
        print(f"     {t.get('subgrupo')}/{t.get('subclasse') or '—'} fora-ponta: "
              f"R$/kWh = {rkwh}")
    print("     (referência jul/2025 ≈ 0.6256 — espere algo entre ~0.60 e ~0.75)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--output", default=DEFAULT_OUTPUT)
    args = ap.parse_args()

    print("""
╔══════════════════════════════════════════════════════════════╗
║  Tarifas ANEEL — extração direta do Dados Abertos (CKAN)      ║
╚══════════════════════════════════════════════════════════════╝
""")

    csv_text = baixar_csv()
    bruto = processar(csv_text)          # keyed por SigAgente (código)
    resultado, descartados = rechavear(bruto)

    if "Enel SP" not in resultado:
        print("❌  Enel SP (ELETROPAULO) não apareceu — algo mudou na fonte. "
              "NÃO publique; me mande a saída."); sys.exit(2)

    print(f"\n{'─'*65}")
    for nome, d in sorted(resultado.items()):
        print(f"  {nome:<26} {str(d.get('reh','?'))[:30]:<32} {d.get('total_registros',0):>5}")
    print(f"{'─'*65}")
    print(f"  {len(resultado)} distribuidoras (nomes comerciais) | "
          f"{len(descartados)} códigos CKAN descartados (fora da whitelist)\n")

    print("🔎  Teste de sanidade (confira antes de publicar):")
    sanidade_enel(resultado.get("Enel SP"))
    print()

    envelope = {
        "gerado_em": datetime.now(timezone.utc).isoformat(),
        "fonte": "Dados Abertos ANEEL (CKAN) — download direto, re-chaveado p/ nome comercial",
        "url_fonte": f"https://dadosabertos.aneel.gov.br/dataset/{DATASET_SLUG}/resource/{RESOURCE_ID}",
        "filtros": {
            "base_tarifaria": "Tarifa de Aplicação",
            "reh": "Mais recente por distribuidora",
            "chave": "nome comercial (RENOMEAR)",
        },
        "total_distribuidoras": len(resultado),
        "distribuidoras": resultado,
    }
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(envelope, f, ensure_ascii=False, indent=2, default=str)
    print(f"✅  {len(resultado)} distribuidoras → {out}  ({out.stat().st_size/1024:.0f} KB)")
    if len(resultado) < 40:
        print(f"⚠️   Só {len(resultado)} distribuidoras (esperado ~50). Confira a whitelist.")


if __name__ == "__main__":
    main()
