#!/bin/bash
# ─────────────────────────────────────────────────────────────
# atualizar.sh — Extrai tarifas da ANEEL e envia para o GitHub
#
# USO:
#   ./atualizar.sh           # extrai todas as distribuidoras
#   ./atualizar.sh ENEL-SP   # extrai apenas uma distribuidora
# ─────────────────────────────────────────────────────────────

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

SIGLA="${1:-}"

echo ""
echo "╔══════════════════════════════════════════════════════╗"
echo "║  Tarifa Justa — Atualização de Tarifas ANEEL         ║"
echo "╚══════════════════════════════════════════════════════╝"
echo ""

# ── 1. Verifica dependências ──────────────────────────────────
echo "🔍  Verificando dependências..."
python3 -m pip install -q requests pandas urllib3
echo "   ✅ OK"

# ── 2. Roda o extrator ────────────────────────────────────────
echo ""
if [ -n "$SIGLA" ]; then
    echo "🚀  Extraindo distribuidora: $SIGLA"
    python3 scripts/extrator_tarifas_aneel.py \
        --output data/tarifas_aneel.json \
        --csv \
        --sigla "$SIGLA"
else
    echo "🚀  Extraindo todas as distribuidoras..."
    python3 scripts/extrator_tarifas_aneel.py \
        --output data/tarifas_aneel.json \
        --csv
fi

# ── 3. Verifica se houve mudança ──────────────────────────────
echo ""
if git diff --quiet data/; then
    echo "ℹ️   Dados sem alteração — nenhum commit necessário."
    exit 0
fi

# ── 4. Commita e envia ────────────────────────────────────────
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
