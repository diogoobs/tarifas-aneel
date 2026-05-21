#!/usr/bin/env python3
"""
Extrator de Tarifas ANEEL — Base de Dados Tarifas Homologadas
=============================================================
Fonte: https://portalrelatorios.aneel.gov.br/luznatarifa/basestarifas#!

COMO OBTER O ARQUIVO:
    1. Acesse https://portalrelatorios.aneel.gov.br/luznatarifa/basestarifas#!
    2. Aplique os filtros:
       - Tipo de Outorga: Concessionária
       - Base Tarifária: Tarifa de Aplicação
       - Ano, Mês: mais recente disponível
    3. Clique em "Baixar dados" → "Dados com layout atual" → xlsx → Exportar
    4. Salve o arquivo como data.xlsx na mesma pasta deste script

USO:
    python3 extrator_tarifas_aneel.py                        # processa data.xlsx
    python3 extrator_tarifas_aneel.py --input meu_arquivo.xlsx
    python3 extrator_tarifas_aneel.py --output tarifas.json --csv
    python3 extrator_tarifas_aneel.py --sigla "Enel SP"      # só uma distribuidora

FILTROS APLICADOS:
    • Remove linhas de metadados/filtros embutidas no Excel
    • Mantém apenas Base Tarifária = "Tarifa de Aplicação"
    • Seleciona a REH mais recente por distribuidora
"""

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    import pandas as pd
except ImportError:
    print("❌  Execute: pip install pandas openpyxl")
    sys.exit(1)

DEFAULT_INPUT  = "data.xlsx"
DEFAULT_OUTPUT = "data/tarifas_aneel.json"


# ─── HELPERS ──────────────────────────────────────────────────────────────────

def parse_float_br(s):
    if s is None or str(s).strip() in ("", "-", "nan", "0"):
        return None
    try:
        return float(str(s).replace(".", "").replace(",", "."))
    except ValueError:
        return None


def normaliza_reh(reh: str) -> tuple:
    """Extrai (ano, numero) de 'REH Nº 3.477, DE 21 DE MARÇO DE 2025' para ordenação."""
    m = re.search(r"(\d[\d\.]+).*?(\d{4})\s*$", str(reh).strip())
    if not m:
        return (0, 0)
    num = int(m.group(1).replace(".", ""))
    ano = int(m.group(2))
    return (ano, num)


def eh_linha_valida(sigla: str) -> bool:
    """Descarta linhas de metadados embutidas no Excel do Power BI."""
    if not sigla or not str(sigla).strip():
        return False
    s = str(sigla).strip()
    # Linhas de metadados contêm frases dos filtros aplicados
    for termo in ["Filtros", "Base Tarifária", "Tipo de Outorga",
                  "Ano é", "Flag é", "Em branco"]:
        if termo in s:
            return False
    return True


# ─── LEITURA E PROCESSAMENTO ──────────────────────────────────────────────────

def carregar_excel(path: Path, debug=False) -> pd.DataFrame:
    print(f"📂  Lendo arquivo: {path}")
    try:
        df = pd.read_excel(str(path), dtype=str)
    except Exception as e:
        print(f"❌  Erro ao ler Excel: {e}")
        sys.exit(1)

    print(f"  ✅ {len(df):,} linhas × {len(df.columns)} colunas")

    if debug:
        print(f"  🔍 Colunas: {list(df.columns)}")
        print(f"  🔍 Amostra:\n{df.head(3).to_string()}\n")

    return df


def processar(df: pd.DataFrame, filtro_sigla=None, debug=False) -> dict:
    df = df.copy()

    # Normaliza nomes de colunas (remove espaços extras)
    df.columns = [str(c).strip() for c in df.columns]

    col_sigla = "Sigla"
    col_reh   = "Resolução ANEEL"
    col_base  = "Base Tarifária"
    col_ini   = "Início Vigência"
    col_fim   = "Fim Vigência"
    col_sub   = "Subgrupo"
    col_mod   = "Modalidade"
    col_cls   = "Classe"
    col_scls  = "Subclasse"
    col_det   = "Detalhe"
    col_ace   = "Acessante"
    col_post  = "Posto"
    col_uni   = "Unidade"
    col_tusd  = "TUSD"
    col_te    = "TE"

    # ── 1. Remove linhas de metadados ────────────────────────────────────────
    df[col_sigla] = df[col_sigla].fillna("").astype(str)
    df = df[df[col_sigla].apply(eh_linha_valida)].copy()
    print(f"  🔽 Após remover metadados: {len(df):,} linhas")

    # ── 2. Filtra base tarifária ──────────────────────────────────────────────
    if col_base in df.columns:
        df = df[df[col_base].fillna("").str.strip() == "Tarifa de Aplicação"]
        print(f"  🔽 Após filtro Base Tarifária: {len(df):,} linhas")

    # ── 3. Filtro opcional por distribuidora ─────────────────────────────────
    if filtro_sigla:
        df = df[df[col_sigla].str.lower() == filtro_sigla.lower()]
        print(f"  🔽 Após filtro sigla '{filtro_sigla}': {len(df):,} linhas")

    if df.empty:
        print("  ⚠  Nenhum dado após filtros.")
        return {}

    # ── 4. REH mais recente por distribuidora ─────────────────────────────────
    df["_reh_sort"] = df[col_reh].apply(normaliza_reh)
    idx_max = df.groupby(col_sigla)["_reh_sort"].transform("max") == df["_reh_sort"]
    df = df[idx_max].copy()
    df.drop(columns=["_reh_sort"], inplace=True)
    print(f"  🔽 Após seleção da REH mais recente: {len(df):,} linhas")

    # ── 5. Monta resultado ────────────────────────────────────────────────────
    resultado = {}

    for sigla, grupo in df.groupby(col_sigla):
        reh       = grupo[col_reh].iloc[0] if col_reh in grupo else None
        dt_inicio = grupo[col_ini].iloc[0] if col_ini in grupo else None
        dt_fim    = grupo[col_fim].iloc[0] if col_fim in grupo else None

        # Normaliza datas
        for dt in [dt_inicio, dt_fim]:
            if dt and "00:00:00" in str(dt):
                dt = str(dt).split(" ")[0]

        tarifas = []
        for _, row in grupo.iterrows():
            tusd_raw = row.get(col_tusd)
            te_raw   = row.get(col_te)
            tusd = parse_float_br(tusd_raw)
            te   = parse_float_br(te_raw)

            # Converte strings de data
            ini_str = str(row.get(col_ini, "")).split(" ")[0] if row.get(col_ini) else None
            fim_str = str(row.get(col_fim, "")).split(" ")[0] if row.get(col_fim) else None

            tarifas.append({
                "vigencia_inicio": ini_str,
                "vigencia_fim":    fim_str,
                "subgrupo":        str(row.get(col_sub,  "") or "").strip(),
                "modalidade":      str(row.get(col_mod,  "") or "").strip(),
                "classe":          str(row.get(col_cls,  "") or "").strip(),
                "subclasse":       str(row.get(col_scls, "") or "").strip(),
                "detalhe":         str(row.get(col_det,  "") or "").strip(),
                "acessante":       str(row.get(col_ace,  "") or "").strip(),
                "posto":           str(row.get(col_post, "") or "").strip(),
                "unidade":         str(row.get(col_uni,  "") or "").strip(),
                "vlr_tusd":        tusd,
                "vlr_te":          te,
                "vlr_total":       round(tusd + te, 6) if tusd is not None and te is not None else None,
            })

        resultado[sigla] = {
            "sigla":           sigla,
            "reh":             reh,
            "vigencia_inicio": str(dt_inicio).split(" ")[0] if dt_inicio else None,
            "vigencia_fim":    str(dt_fim).split(" ")[0]    if dt_fim    else None,
            "base_tarifaria":  "Tarifa de Aplicação",
            "tarifas":         tarifas,
            "total_registros": len(tarifas),
        }

    return resultado


# ─── RESUMO ───────────────────────────────────────────────────────────────────

def imprimir_resumo(resultado: dict):
    print(f"\n{'─'*70}")
    print(f"  {'DISTRIBUIDORA':<30} {'REH':>6}  {'VIGÊNCIA':>10}  {'REGS':>6}")
    print(f"{'─'*70}")
    for sigla, d in sorted(resultado.items()):
        reh_num = re.search(r"(\d[\d\.]+)", str(d.get("reh", ""))).group(1)[:8] \
                  if d.get("reh") else "?"
        inicio  = str(d.get("vigencia_inicio", "?"))[:10]
        regs    = d.get("total_registros", 0)
        print(f"  {sigla:<30} {reh_num:>8}  {inicio:>10}  {regs:>6}")
    print(f"{'─'*70}")
    print(f"  Total: {len(resultado)} distribuidoras\n")


def exportar_csv(resultado: dict, path_csv: Path):
    linhas = []
    for sigla, d in resultado.items():
        for t in d.get("tarifas", []):
            linhas.append({"sigla": sigla, "reh": d.get("reh"), **t})
    pd.DataFrame(linhas).to_csv(path_csv, index=False, encoding="utf-8-sig", sep=";")
    print(f"  📄 CSV: {path_csv}  ({len(linhas):,} linhas)")


# ─── MAIN ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Processa o Excel de tarifas ANEEL exportado do portal"
    )
    parser.add_argument("--input",  default=DEFAULT_INPUT,
                        help=f"Arquivo Excel de entrada (default: {DEFAULT_INPUT})")
    parser.add_argument("--output", default=DEFAULT_OUTPUT,
                        help=f"JSON de saída (default: {DEFAULT_OUTPUT})")
    parser.add_argument("--csv",    action="store_true",
                        help="Também exporta CSV flat")
    parser.add_argument("--sigla",  default=None,
                        help="Filtra uma distribuidora (ex: --sigla 'Enel SP')")
    parser.add_argument("--debug",  action="store_true")
    args = parser.parse_args()

    in_path  = Path(args.input)
    out_path = Path(args.output)

    if not in_path.exists():
        print(f"❌  Arquivo não encontrado: {in_path}")
        print(f"   Baixe o Excel em: https://portalrelatorios.aneel.gov.br/luznatarifa/basestarifas#!")
        sys.exit(1)

    out_path.parent.mkdir(parents=True, exist_ok=True)

    print("""
╔══════════════════════════════════════════════════════════════╗
║  Extrator de Tarifas ANEEL — Portal Luz na Tarifa            ║
╚══════════════════════════════════════════════════════════════╝
""")

    df       = carregar_excel(in_path, debug=args.debug)
    print("\n🔄  Processando...")
    resultado = processar(df, filtro_sigla=args.sigla, debug=args.debug)

    imprimir_resumo(resultado)

    envelope = {
        "gerado_em":            datetime.now(timezone.utc).isoformat(),
        "fonte":                "Portal Luz na Tarifa — ANEEL",
        "url_fonte":            "https://portalrelatorios.aneel.gov.br/luznatarifa/basestarifas#!",
        "arquivo_origem":       in_path.name,
        "filtros": {
            "base_tarifaria":   "Tarifa de Aplicação",
            "reh":              "Mais recente por distribuidora",
        },
        "total_distribuidoras": len(resultado),
        "distribuidoras":       resultado,
    }

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(envelope, f, ensure_ascii=False, indent=2, default=str)

    print(f"✅  JSON: {out_path}  ({out_path.stat().st_size / 1024:.1f} KB)")

    if args.csv:
        exportar_csv(resultado, out_path.with_suffix(".csv"))

    print(f"""
╔══════════════════════════════════════════════════════════════╗
║  Concluído! {len(resultado)} distribuidoras extraídas
╚══════════════════════════════════════════════════════════════╝
""")


if __name__ == "__main__":
    main()
