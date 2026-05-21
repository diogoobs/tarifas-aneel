#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
# atualizar.sh — Processa o Excel da ANEEL e publica no GitHub
#
# ANTES DE RODAR:
#   1. Acesse https://portalrelatorios.aneel.gov.br/luznatarifa/basestarifas#!
#   2. Filtros: Tipo Outorga=Concessionária | Base Tarifária=Tarifa de Aplicação
#              Ano/Mês=mais recente disponível
#   3. Clique "Baixar dados" → "Dados com layout atual" → xlsx → Exportar
#   4. Salve o arquivo como data.xlsx nesta pasta
#
# USO:
#   ./atualizar.sh              # processa data.xlsx
#   ./atualizar.sh outro.xlsx   # processa outro arquivo
# ─────────────────────────────────────────────────────────────────────────────

set -e
REPO_DIR="/Users/diogosilva/Documents/Claude/Tarifa Justa/tarifas-aneel"
cd "$REPO_DIR"

XLSX="${1:-data.xlsx}"

echo ""
echo "╔══════════════════════════════════════════════════════╗"
echo "║  Tarifa Justa — Atualização de Tarifas ANEEL         ║"
echo "╚══════════════════════════════════════════════════════╝"
echo ""

# ── 1. Verifica arquivo ───────────────────────────────────────
if [ ! -f "$XLSX" ]; then
    echo "❌  Arquivo não encontrado: $XLSX"
    echo ""
    echo "   Baixe o Excel em:"
    echo "   https://portalrelatorios.aneel.gov.br/luznatarifa/basestarifas#!"
    echo ""
    echo "   Filtros: Tipo Outorga=Concessionária"
    echo "            Base Tarifária=Tarifa de Aplicação"
    echo "            Ano/Mês=mais recente"
    echo ""
    echo "   Salve como: $REPO_DIR/$XLSX"
    exit 1
fi

# ── 2. Instala dependências ───────────────────────────────────
echo "🔍  Verificando dependências..."
python3 -m pip install -q pandas openpyxl
echo "   ✅ OK"
echo ""

# ── 3. Processa o Excel ───────────────────────────────────────
echo "🚀  Processando $XLSX..."
python3 scripts/extrator_tarifas_aneel.py \
    --input  "$XLSX" \
    --output data/tarifas_aneel.json \
    --csv

# ── 4. Verifica mudanças ──────────────────────────────────────
echo ""
if git diff --quiet data/; then
    echo "ℹ️   Dados sem alteração — nenhum commit necessário."
    exit 0
fi

# ── 5. Commita e envia ────────────────────────────────────────
DATA=$(date +%Y-%m-%d)
echo "📤  Enviando para o GitHub..."
git add data/tarifas_aneel.json data/tarifas_aneel.csv
git commit -m "chore: atualiza tarifas ANEEL $DATA"
git push

echo ""
echo "╔══════════════════════════════════════════════════════╗"
echo "║  ✅ Concluído! Dados publicados no GitHub.           ║"
echo "║                                                      ║"
echo "║  URL dos dados:                                      ║"
echo "║  raw.githubusercontent.com/diogoobs/                 ║"
echo "║  tarifas-aneel/main/data/tarifas_aneel.json          ║"
echo "╚══════════════════════════════════════════════════════╝"
echo ""
