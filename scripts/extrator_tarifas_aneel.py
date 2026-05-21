#!/usr/bin/env python3
"""
Extrator de Tarifas ANEEL — Base de Dados Tarifas Homologadas
=============================================================
Fonte: https://dadosabertos.aneel.gov.br/dataset/tarifas-distribuidoras-energia-eletrica
API:   CKAN Datastore (sem scraping de Power BI)

INSTALAÇÃO:
    pip install requests pandas

USO:
    python3 extrator_tarifas_aneel.py                    # gera JSON + CSV
    python3 extrator_tarifas_aneel.py --output meu.json  # nome customizado
    python3 extrator_tarifas_aneel.py --csv              # também exporta CSV
    python3 extrator_tarifas_aneel.py --sigla ENEL-SP    # só uma distribuidora
    python3 extrator_tarifas_aneel.py --debug            # mostra amostra dos dados brutos

FILTROS APLICADOS (conforme especificação):
    • Tipo de outorga: apenas concessionárias
    • REH: resolução homologatória mais recente por distribuidora
    • Base tarifária: apenas "Tarifa de Aplicação"
"""

import argparse
import json
import ssl
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    import requests
    from requests.adapters import HTTPAdapter
    from urllib3.util.ssl_ import create_urllib3_context
except ImportError:
    print("❌  Execute: pip install requests")
    sys.exit(1)

try:
    import pandas as pd
except ImportError:
    print("❌  Execute: pip install pandas")
    sys.exit(1)


# ─── SSL ADAPTER ──────────────────────────────────────────────────────────────
# O servidor dadosabertos.aneel.gov.br usa TLS legado que causa SSLEOFError
# com as configurações padrão do requests. Este adapter força configurações
# mais permissivas compatíveis com o servidor da ANEEL.

class LegacySSLAdapter(HTTPAdapter):
    def init_poolmanager(self, *args, **kwargs):
        ctx = create_urllib3_context()
        ctx.set_ciphers("DEFAULT@SECLEVEL=1")
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        kwargs["ssl_context"] = ctx
        super().init_poolmanager(*args, **kwargs)


def make_session() -> requests.Session:
    session = requests.Session()
    session.mount("https://dadosabertos.aneel.gov.br", LegacySSLAdapter())
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (compatible; tarifas-aneel-extractor/1.0)",
        "Accept": "application/json, text/plain, */*",
    })
    return session


SESSION = make_session()

# ─── CONFIGURAÇÃO ─────────────────────────────────────────────────────────────

RESOURCE_ID   = "fcf2906c-7c32-4b9b-a637-054e7a5234f4"
API_BASE      = "https://dadosabertos.aneel.gov.br/api/3/action/datastore_search"
API_SQL       = "https://dadosabertos.aneel.gov.br/api/3/action/datastore_search_sql"
CSV_DOWNLOAD  = (
    "https://dadosabertos.aneel.gov.br/dataset/5a583f3e-1646-4f67-bf0f-69db4203e89e"
    "/resource/fcf2906c-7c32-4b9b-a637-054e7a5234f4"
    "/download/tarifas-homologadas-distribuidoras-energia-eletrica.csv"
)
DEFAULT_OUTPUT = "tarifas_aneel_homologadas.json"
PAGE_SIZE      = 10_000   # registros por chamada à API

# Concessionárias conhecidas (usadas para filtragem e fallback)
# Fonte: ANEEL — lista de distribuidoras com outorga de concessão
SIGLAS_CONCESSIONARIAS = {
    "ENEL-SP", "ENEL-CE", "ENEL-GO", "ENEL-RJ",
    "ELEKTRO", "EDP-SP", "EDP-ES", "CPFL-PAULISTA",
    "CPFL-PIRATININGA", "CPFL-SUL", "CPFL-JAGUARI",
    "COPEL-DIS", "CEMIG-D", "LIGHT", "CELESC-DIS",
    "EQUATORIAL-MA", "EQUATORIAL-PA", "EQUATORIAL-AL",
    "EQUATORIAL-PI", "EQUATORIAL-GO", "EQUATORIAL-MS",
    "ENERGISA-MT", "ENERGISA-MS", "ENERGISA-PB",
    "ENERGISA-TO", "ENERGISA-MG", "ENERGISA-SE",
    "COELBA", "CELPE", "COSERN", "COELCE",
    "AMAZONAS-ENERGIA", "CERON", "BOA-VISTA-ENERGIA",
    "AES-SUL", "RGE-SUL", "RGE", "CELG-D",
    "CHESP", "CEB-D", "CELB", "CEMAR", "CEAL",
    "CEPISA", "SULGIPE", "DMED",
}


# ─── HELPERS ──────────────────────────────────────────────────────────────────

def parse_float_br(s):
    """Converte string pt-BR (vírgula decimal) para float."""
    if s is None or str(s).strip() in ("", "-", "nan"):
        return None
    try:
        return float(str(s).replace(".", "").replace(",", "."))
    except ValueError:
        return None


def normaliza_reh(reh: str) -> tuple:
    """
    Extrai (numero, ano) de strings como '3477/2025' ou 'REH nº 3.477/2025'
    para ordenação correta da resolução mais recente.
    """
    import re
    m = re.search(r"(\d[\d\.]+)\s*/\s*(\d{4})", str(reh))
    if not m:
        return (0, 0)
    num = int(m.group(1).replace(".", ""))
    ano = int(m.group(2))
    return (ano, num)


def eh_concessionaria(sigla: str) -> bool:
    """
    Heurística para identificar concessionárias vs cooperativas/permissionárias.
    Cooperativas geralmente contêm 'COOP', 'CERPA', 'CERAES', 'COOPERTRADIÇÃO'
    Permissionárias: 'PERM' ou sufixo '-PERM'
    """
    s = sigla.upper()
    # Exclui explicitamente cooperativas e permissionárias
    if any(p in s for p in ["COOP", "CERPA", "CERAL", "CERES",
                              "CERTAJA", "HIDROPAN", "UHENPAL",
                              "-PERM", "PERM-"]):
        return False
    return True


# ─── DOWNLOAD DOS DADOS ───────────────────────────────────────────────────────

def baixar_via_api_paginada(filtro_sigla=None, debug=False) -> pd.DataFrame:
    """
    Baixa todos os registros via API CKAN com paginação.
    Aplica filtro de base tarifária = 'Tarifa de Aplicação' na query.
    """
    print("📡  Conectando à API CKAN (Dados Abertos ANEEL)...")

    todos = []
    offset = 0

    # Filtros diretos suportados pela API CKAN datastore
    filtros = {"DscBaseTarifaria": "Tarifa de Aplicação"}
    if filtro_sigla:
        filtros["SigAgente"] = filtro_sigla.upper()

    while True:
        params = {
            "resource_id": RESOURCE_ID,
            "limit":       PAGE_SIZE,
            "offset":      offset,
            "filters":     json.dumps(filtros),
        }
        try:
            resp = SESSION.get(API_BASE, params=params, timeout=60, verify=False)
            resp.raise_for_status()
        except requests.RequestException as e:
            print(f"  ⚠  Erro na API: {e}")
            print("  🔄  Tentando fallback via download CSV direto...")
            return baixar_via_csv(filtro_sigla, debug)

        data = resp.json()
        if not data.get("success"):
            print(f"  ⚠  API retornou erro: {data.get('error')}")
            return baixar_via_csv(filtro_sigla, debug)

        registros = data["result"]["records"]
        total     = data["result"]["total"]

        if offset == 0:
            print(f"  ✅ Total de registros (Tarifa de Aplicação): {total:,}")

        todos.extend(registros)
        offset += PAGE_SIZE

        pct = min(100, len(todos) / max(total, 1) * 100)
        print(f"  ⏳ Baixados: {len(todos):,}/{total:,} ({pct:.0f}%)", end="\r")

        if len(todos) >= total or not registros:
            break

    print()  # nova linha após o \r

    if not todos:
        print("  ⚠  Nenhum registro retornado — tentando CSV direto...")
        return baixar_via_csv(filtro_sigla, debug)

    df = pd.DataFrame(todos)
    if debug:
        print(f"\n  🔍 Colunas disponíveis: {list(df.columns)}")
        print(f"  🔍 Amostra (3 linhas):\n{df.head(3).to_string()}\n")
    return df


def baixar_via_csv(filtro_sigla=None, debug=False) -> pd.DataFrame:
    """Fallback: baixa o CSV completo e filtra localmente."""
    print(f"📡  Baixando CSV completo (~8MB)...")
    try:
        resp = SESSION.get(CSV_DOWNLOAD, timeout=120, verify=False)
        resp.raise_for_status()
        resp.encoding = "utf-8-sig"
    except requests.RequestException as e:
        print(f"❌  Falha no download: {e}")
        sys.exit(1)

    from io import StringIO
    df = pd.read_csv(StringIO(resp.text), sep=";", dtype=str)
    print(f"  ✅ CSV baixado: {len(df):,} linhas × {len(df.columns)} colunas")

    if debug:
        print(f"  🔍 Colunas: {list(df.columns)}")
        print(f"  🔍 Amostra:\n{df.head(3).to_string()}\n")

    # Filtra base tarifária
    if "DscBaseTarifaria" in df.columns:
        df = df[df["DscBaseTarifaria"].str.strip() == "Tarifa de Aplicação"]
        print(f"  🔽 Após filtro Base Tarifária: {len(df):,} linhas")

    if filtro_sigla and "SigAgente" in df.columns:
        df = df[df["SigAgente"].str.upper() == filtro_sigla.upper()]

    return df


# ─── PROCESSAMENTO ────────────────────────────────────────────────────────────

def processar_distribuidoras(df: pd.DataFrame, debug=False) -> dict:
    """
    Para cada distribuidora:
      1. Filtra apenas concessionárias
      2. Seleciona somente a REH mais recente
      3. Estrutura tarifas por subgrupo / modalidade / posto / classe
    """
    if df.empty:
        print("⚠  DataFrame vazio — nenhum dado para processar.")
        return {}

    # Garante que SigAgente existe
    if "SigAgente" not in df.columns:
        print("❌  Coluna 'SigAgente' não encontrada. Verifique o CSV/API.")
        print(f"   Colunas disponíveis: {list(df.columns)}")
        sys.exit(1)

    df = df.copy()
    df["SigAgente"] = df["SigAgente"].str.strip().str.upper()

    # ── 1. Filtra concessionárias ────────────────────────────────────────────
    siglas_todas = df["SigAgente"].unique()
    siglas_conc  = [s for s in siglas_todas if eh_concessionaria(s)]
    df = df[df["SigAgente"].isin(siglas_conc)]
    print(f"  🏢 Concessionárias identificadas: {len(siglas_conc)}")

    # ── 2. Seleciona REH mais recente por distribuidora ───────────────────────
    if "DscREH" in df.columns:
        df["_reh_sort"] = df["DscREH"].apply(normaliza_reh)

        # Para cada distribuidora, pega o maior (ano, numero)
        idx_max_reh = (
            df.groupby("SigAgente")["_reh_sort"]
            .transform("max") == df["_reh_sort"]
        )
        df = df[idx_max_reh].copy()
        df.drop(columns=["_reh_sort"], inplace=True)
        print(f"  📋 Registros após seleção da REH mais recente: {len(df):,}")

    # ── 3. Estrutura resultado ────────────────────────────────────────────────
    resultado = {}

    for sigla, grupo in df.groupby("SigAgente"):
        reh        = grupo["DscREH"].iloc[0] if "DscREH" in grupo else None
        dt_inicio  = grupo["DatInicioVigencia"].iloc[0] if "DatInicioVigencia" in grupo else None
        dt_fim     = grupo["DatFimVigencia"].iloc[0]    if "DatFimVigencia"    in grupo else None
        cnpj       = grupo["NumCNPJDistribuidora"].iloc[0] if "NumCNPJDistribuidora" in grupo else None

        tarifas = []
        for _, row in grupo.iterrows():
            tusd = parse_float_br(row.get("VlrTUSD"))
            te   = parse_float_br(row.get("VlrTE"))

            tarifas.append({
                "subgrupo":          row.get("DscSubGrupo", "").strip(),
                "modalidade":        row.get("DscModalidadeTarifaria", "").strip(),
                "classe":            row.get("DscClasse", "").strip(),
                "subclasse":         row.get("DscSubClasse", "").strip(),
                "detalhe":           row.get("DscDetalhe", "").strip(),
                "posto_tarifario":   row.get("NomPostoTarifario", "").strip(),
                "unidade":           row.get("DscUnidadeTerciaria", "").strip(),
                "agente_acessante":  row.get("SigAgenteAcessante", "").strip(),
                "vlr_tusd":          tusd,
                "vlr_te":            te,
                "vlr_total":         round(tusd + te, 6) if tusd is not None and te is not None else None,
            })

        resultado[sigla] = {
            "sigla":           sigla,
            "cnpj":            cnpj,
            "reh":             reh,
            "vigencia_inicio": dt_inicio,
            "vigencia_fim":    dt_fim,
            "base_tarifaria":  "Tarifa de Aplicação",
            "tarifas":         tarifas,
            "total_registros": len(tarifas),
        }

    return resultado


# ─── RESUMO ────────────────────────────────────────────────────────────────────

def imprimir_resumo(resultado: dict):
    """Imprime tabela resumida no terminal."""
    if not resultado:
        print("  ⚠  Nenhuma distribuidora no resultado.")
        return

    print(f"\n{'─'*70}")
    print(f"  {'DISTRIBUIDORA':<20} {'REH':<15} {'VIGÊNCIA':>12}  {'REGISTROS':>10}")
    print(f"{'─'*70}")

    for sigla, d in sorted(resultado.items()):
        reh    = str(d.get("reh", "?"))[:14]
        inicio = str(d.get("vigencia_inicio", "?"))[:10]
        regs   = d.get("total_registros", 0)
        print(f"  {sigla:<20} {reh:<15} {inicio:>12}  {regs:>10}")

    print(f"{'─'*70}")
    print(f"  Total: {len(resultado)} distribuidoras\n")


def exportar_csv_flat(resultado: dict, path_csv: Path):
    """Exporta versão flat (uma linha por tarifa) em CSV."""
    linhas = []
    for sigla, d in resultado.items():
        for t in d.get("tarifas", []):
            linhas.append({
                "sigla":           sigla,
                "cnpj":            d.get("cnpj"),
                "reh":             d.get("reh"),
                "vigencia_inicio": d.get("vigencia_inicio"),
                "vigencia_fim":    d.get("vigencia_fim"),
                **t,
            })
    df_out = pd.DataFrame(linhas)
    df_out.to_csv(path_csv, index=False, encoding="utf-8-sig", sep=";")
    print(f"  📄 CSV exportado: {path_csv}  ({len(df_out):,} linhas)")


# ─── MAIN ─────────────────────────────────────────────────────────────────────

def main():
    # Suprime warnings de SSL (servidor ANEEL usa TLS legado)
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    parser = argparse.ArgumentParser(
        description="Extrator de Tarifas ANEEL — Base Homologadas (Dados Abertos)"
    )
    parser.add_argument("--output", default=DEFAULT_OUTPUT,
                        help="Arquivo JSON de saída (default: tarifas_aneel_homologadas.json)")
    parser.add_argument("--csv", action="store_true",
                        help="Também exporta CSV flat (uma linha por tarifa)")
    parser.add_argument("--sigla", default=None,
                        help="Filtra uma única distribuidora (ex: --sigla ENEL-SP)")
    parser.add_argument("--debug", action="store_true",
                        help="Mostra amostra dos dados brutos")
    args = parser.parse_args()

    out_path = Path(args.output)

    print("""
╔══════════════════════════════════════════════════════════════╗
║  Extrator de Tarifas ANEEL — Base Homologadas                ║
║  Fonte: dadosabertos.aneel.gov.br                            ║
╚══════════════════════════════════════════════════════════════╝
""")
    print(f"  Saída JSON: {out_path}")
    if args.csv:
        print(f"  Saída CSV:  {out_path.with_suffix('.csv')}")
    if args.sigla:
        print(f"  Filtro:     {args.sigla.upper()}")
    print()

    # ── Download ─────────────────────────────────────────────────────────────
    df = baixar_via_api_paginada(filtro_sigla=args.sigla, debug=args.debug)

    if df.empty:
        print("❌  Nenhum dado retornado.")
        sys.exit(1)

    # ── Processamento ─────────────────────────────────────────────────────────
    print("\n🔄  Processando filtros (concessionárias + REH mais recente)...")
    resultado = processar_distribuidoras(df, debug=args.debug)

    # ── Resumo ────────────────────────────────────────────────────────────────
    imprimir_resumo(resultado)

    # ── Salva JSON ────────────────────────────────────────────────────────────
    envelope = {
        "gerado_em":     datetime.now(timezone.utc).isoformat(),
        "fonte":         "Dados Abertos ANEEL — Tarifas Homologadas Distribuidoras",
        "url_fonte":     "https://dadosabertos.aneel.gov.br/dataset/tarifas-distribuidoras-energia-eletrica",
        "resource_id":   RESOURCE_ID,
        "filtros":       {
            "tipo_outorga":   "Concessionária",
            "base_tarifaria": "Tarifa de Aplicação",
            "reh":            "Mais recente por distribuidora",
        },
        "total_distribuidoras": len(resultado),
        "distribuidoras": resultado,
    }

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(envelope, f, ensure_ascii=False, indent=2, default=str)

    print(f"✅  JSON salvo: {out_path}  ({out_path.stat().st_size / 1024:.1f} KB)")

    # ── Exporta CSV flat (opcional) ───────────────────────────────────────────
    if args.csv:
        exportar_csv_flat(resultado, out_path.with_suffix(".csv"))

    print(f"""
╔══════════════════════════════════════════════════════════════╗
║  Concluído! {len(resultado)} distribuidoras extraídas         
║  → {out_path}
╚══════════════════════════════════════════════════════════════╝
""")


if __name__ == "__main__":
    main()
