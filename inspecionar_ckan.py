#!/usr/bin/env python3
"""Inspeciona o CSV do CKAN já baixado (reaproveita o temp da última execução).
Mostra as 18 colunas, as de baixa cardinalidade (candidatas a filtro) e uma
amostra do campo REH — pra eu acertar o mapeamento e o filtro Concessionária."""
import glob, os, tempfile, sys
import pandas as pd

cands = (glob.glob(os.path.join(tempfile.gettempdir(), "aneel_ckan.csv"))
         + glob.glob("/tmp/aneel_ckan.csv"))
if not cands:
    print("Não achei o CSV no temp. Rode antes: bash atualizar.sh --dry-run")
    sys.exit(1)
path = cands[0]
print("Lendo:", path)
df = pd.read_csv(path, sep=",", dtype=str, on_bad_lines="skip", low_memory=False)
print(f"\n{len(df):,} linhas x {df.shape[1]} colunas\n")
print("=== TODAS AS COLUNAS ===")
for c in df.columns:
    print(f"  {c!r}  (nunique={df[c].nunique()})")

print("\n=== COLUNAS DE BAIXA CARDINALIDADE (candidatas a filtro) ===")
for c in df.columns:
    n = df[c].nunique(dropna=True)
    if n <= 30:
        vals = sorted(v for v in df[c].dropna().unique())[:30]
        print(f"\n--- {c} ({n}) ---\n  {vals}")

# amostra do campo que parece REH / resolução
for c in df.columns:
    if any(t in c.lower() for t in ("reh", "resol", "homolog")):
        print(f"\n=== AMOSTRA {c} ===")
        print("  ", df[c].dropna().unique()[:5])
