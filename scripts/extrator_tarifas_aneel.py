#!/usr/bin/env python3
"""
Extrator de Tarifas ANEEL — via Cloudflare Worker Proxy
=========================================================
Baixa o CSV de tarifas via proxy na Cloudflare (contorna bloqueio de IP).

INSTALAÇÃO:
    pip install requests pandas openpyxl

USO:
    python3 extrator_tarifas_aneel.py
    python3 extrator_tarifas_aneel.py --sigla "Enel SP"
    python3 extrator_tarifas_aneel.py --debug

VARIÁVEIS DE AMBIENTE (obrigatórias no GitHub Actions):
    PROXY_URL    URL do Cloudflare Worker (ex: https://aneel-proxy.SEU_USER.workers.dev)
    PROXY_TOKEN  Token de segurança configurado no Worker
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path

try:
    import requests
except ImportError:
    print("❌  Execute: pip install requests"); sys.exit(1)

try:
    import pandas as pd
except ImportError:
    print("❌  Execute: pip install pandas"); sys.exit(1)

DEFAULT_OUTPUT = "data/tarifas_aneel.json"


# ─── HELPERS ──────────────────────────────────────────────────────────────────

def parse_float_br(s):
    if s is None or str(s).strip() in ("", "-", "nan"):
        return None
    try:
        return float(str(s).replace(".", "").replace(",", "."))
    except ValueError:
        return None

def normaliza_reh(reh: str) -> tuple:
    m = re.search(r"(\d[\d\.]+).*?(\d{4})\s*$", str(reh).strip())
    if not m: return (0, 0)
    return (int(m.group(2)), int(m.group(1).replace(".", "")))

def eh_linha_valida(sigla: str) -> bool:
    if not sigla or not str(sigla).strip(): return False
    for termo in ["Filtros", "Base Tarifária", "Tipo de Outorga", "Ano é", "Flag é"]:
        if termo in str(sigla): return False
    return True


# ─── DOWNLOAD VIA PROXY ───────────────────────────────────────────────────────

def baixar_csv(proxy_url: str, proxy_token: str, debug=False) -> str:
    """Baixa o CSV via Cloudflare Worker proxy."""
    url = f"{proxy_url.rstrip('/')}/csv"
    headers = {"X-Proxy-Token": proxy_token}

    print(f"📡  Baixando via proxy: {proxy_url}")
    try:
        resp = requests.get(url, headers=headers, timeout=60)
        if resp.status_code == 401:
            print("❌  Token inválido — verifique PROXY_TOKEN"); sys.exit(1)
        resp.raise_for_status()
        print(f"  ✅ CSV baixado: {len(resp.content)/1024:.0f} KB")
        return resp.text
    except requests.RequestException as e:
        print(f"❌  Erro no proxy: {e}"); sys.exit(1)


# ─── PROCESSAMENTO ────────────────────────────────────────────────────────────

def processar(csv_text: str, filtro_sigla=None) -> dict:
    df = pd.read_csv(StringIO(csv_text), sep=";", dtype=str)
    print(f"  ✅ {len(df):,} linhas × {len(df.columns)} colunas")

    # Normaliza colunas
    df.columns = [c.strip() for c in df.columns]

    # Mapeamento de colunas (CSV usa nomes em português)
    col_map = {
        "sigla":   next((c for c in df.columns if "sigla" in c.lower() or "agente" in c.lower()), None),
        "reh":     next((c for c in df.columns if "reh" in c.lower() or "resolucao" in c.lower() or "resolução" in c.lower()), None),
        "base":    next((c for c in df.columns if "base" in c.lower()), None),
        "inicio":  next((c for c in df.columns if "inicio" in c.lower() or "início" in c.lower()), None),
        "fim":     next((c for c in df.columns if "fim" in c.lower()), None),
        "subgrupo": next((c for c in df.columns if "subgrupo" in c.lower()), None),
        "modal":   next((c for c in df.columns if "modalidade" in c.lower()), None),
        "classe":  next((c for c in df.columns if "classe" in c.lower() and "sub" not in c.lower()), None),
        "subclasse": next((c for c in df.columns if "subclasse" in c.lower()), None),
        "detalhe": next((c for c in df.columns if "detalhe" in c.lower()), None),
        "acesante": next((c for c in df.columns if "acess" in c.lower()), None),
        "posto":   next((c for c in df.columns if "posto" in c.lower()), None),
        "unidade": next((c for c in df.columns if "unidade" in c.lower()), None),
        "tusd":    next((c for c in df.columns if "tusd" in c.lower()), None),
        "te":      next((c for c in df.columns if c.lower() == "te" or "vlrte" in c.lower()), None),
    }

    col_sigla = col_map["sigla"] or "SigAgente"
    df[col_sigla] = df[col_sigla].fillna("").astype(str)
    df = df[df[col_sigla].apply(eh_linha_valida)].copy()

    # Filtra base tarifária
    if col_map["base"]:
        df = df[df[col_map["base"]].fillna("").str.strip() == "Tarifa de Aplicação"]

    if filtro_sigla:
        df = df[df[col_sigla].str.lower() == filtro_sigla.lower()]

    print(f"  🔽 Após filtros: {len(df):,} linhas")

    # REH mais recente
    col_reh = col_map["reh"] or "DscREH"
    if col_reh in df.columns:
        df["_sort"] = df[col_reh].apply(normaliza_reh)
        df = df[df.groupby(col_sigla)["_sort"].transform("max") == df["_sort"]].copy()
        df.drop(columns=["_sort"], inplace=True)

    print(f"  🔽 Após REH mais recente: {len(df):,} linhas")

    resultado = {}
    for sigla, grupo in df.groupby(col_sigla):
        reh = grupo[col_reh].iloc[0] if col_reh in grupo else None
        ini = grupo[col_map["inicio"]].iloc[0] if col_map["inicio"] else None
        fim = grupo[col_map["fim"]].iloc[0] if col_map["fim"] else None

        tarifas = []
        for _, row in grupo.iterrows():
            tusd = parse_float_br(row.get(col_map["tusd"] or "VlrTUSD"))
            te   = parse_float_br(row.get(col_map["te"]   or "VlrTE"))
            tarifas.append({
                "subgrupo":   str(row.get(col_map["subgrupo"] or "", "") or "").strip(),
                "modalidade": str(row.get(col_map["modal"]    or "", "") or "").strip(),
                "classe":     str(row.get(col_map["classe"]   or "", "") or "").strip(),
                "subclasse":  str(row.get(col_map["subclasse"]or "", "") or "").strip(),
                "detalhe":    str(row.get(col_map["detalhe"]  or "", "") or "").strip(),
                "acessante":  str(row.get(col_map["acesante"] or "", "") or "").strip(),
                "posto":      str(row.get(col_map["posto"]    or "", "") or "").strip(),
                "unidade":    str(row.get(col_map["unidade"]  or "", "") or "").strip(),
                "vlr_tusd":   tusd,
                "vlr_te":     te,
                "vlr_total":  round(tusd + te, 6) if tusd is not None and te is not None else None,
            })

        resultado[sigla] = {
            "sigla":           sigla,
            "reh":             str(reh) if reh else None,
            "vigencia_inicio": str(ini).split(" ")[0] if ini else None,
            "vigencia_fim":    str(fim).split(" ")[0] if fim else None,
            "base_tarifaria":  "Tarifa de Aplicação",
            "tarifas":         tarifas,
            "total_registros": len(tarifas),
        }

    return resultado


# ─── MAIN ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    parser.add_argument("--sigla",  default=None)
    parser.add_argument("--debug",  action="store_true")
    args = parser.parse_args()

    proxy_url   = os.environ.get("PROXY_URL",   "").strip()
    proxy_token = os.environ.get("PROXY_TOKEN", "").strip()

    if not proxy_url or not proxy_token:
        print("❌  Defina PROXY_URL e PROXY_TOKEN nas variáveis de ambiente")
        print("   No GitHub Actions: Settings → Secrets → Actions")
        sys.exit(1)

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    print("""
╔══════════════════════════════════════════════════════════════╗
║  Extrator de Tarifas ANEEL — via Cloudflare Proxy            ║
╚══════════════════════════════════════════════════════════════╝
""")

    csv_text  = baixar_csv(proxy_url, proxy_token, debug=args.debug)
    resultado = processar(csv_text, filtro_sigla=args.sigla)

    # Resumo
    print(f"\n{'─'*65}")
    for sigla, d in sorted(resultado.items()):
        print(f"  {sigla:<28} {str(d.get('reh','?'))[:20]}  {d.get('total_registros',0):>6}")
    print(f"{'─'*65}")
    print(f"  Total: {len(resultado)} distribuidoras\n")

    envelope = {
        "gerado_em":            datetime.now(timezone.utc).isoformat(),
        "fonte":                "Dados Abertos ANEEL via Cloudflare Proxy",
        "url_fonte":            "https://dadosabertos.aneel.gov.br/dataset/tarifas-distribuidoras-energia-eletrica",
        "filtros": {
            "base_tarifaria":   "Tarifa de Aplicação",
            "reh":              "Mais recente por distribuidora",
        },
        "total_distribuidoras": len(resultado),
        "distribuidoras":       resultado,
    }

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(envelope, f, ensure_ascii=False, indent=2, default=str)

    print(f"✅  {len(resultado)} distribuidoras → {out_path}  ({out_path.stat().st_size/1024:.0f} KB)")

if __name__ == "__main__":
    main()
