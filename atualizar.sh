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
#   ./atualizar.sh              # tarifas + horários
#   ./atualizar.sh --so-tarifas # só tarifas (sem horários)
# ─────────────────────────────────────────────────────────────────────────────

set -e
REPO_DIR="/Users/diogosilva/Documents/Claude/Tarifa Justa/tarifas-aneel"
cd "$REPO_DIR"

XLSX="data.xlsx"
SO_TARIFAS=false
[[ "$1" == "--so-tarifas" ]] && SO_TARIFAS=true

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
    echo "   Salve como: $REPO_DIR/$XLSX"
    exit 1
fi

# ── 2. Instala dependências ───────────────────────────────────
echo "🔍  Verificando dependências..."
python3 -m pip install -q pandas openpyxl playwright
python3 -m playwright install chromium --quiet 2>/dev/null || true
echo "   ✅ OK"
echo ""

# ── 3. Processa o Excel (tarifas) ────────────────────────────
echo "🚀  Etapa 1/2 — Extraindo tarifas do Excel..."
python3 scripts/extrator_tarifas_aneel.py \
    --input  "$XLSX" \
    --output data/tarifas_aneel.json \
    --csv

# ── 4. Extrai horários dos postos (Playwright) ────────────────
if [ "$SO_TARIFAS" = false ]; then
    echo ""
    echo "🕒  Etapa 2/2 — Extraindo horários dos postos tarifários..."
    python3 scripts/extrator_horarios_aneel.py \
        --json data/tarifas_aneel.json \
        --headless || echo "   ⚠️  Horários não extraídos — verifique conexão com o dashboard ANEEL"
fi

# ── 5. Verifica mudanças ──────────────────────────────────────
echo ""
if git diff --quiet data/; then
    echo "ℹ️   Dados sem alteração — nenhum commit necessário."
    exit 0
fi

# ── 6. Commita e envia ────────────────────────────────────────
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
