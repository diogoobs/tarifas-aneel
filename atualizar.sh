#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
# atualizar.sh — Regenera as tarifas ANEEL e publica no GitHub
#
# Agora 100% automático: baixa direto do Dados Abertos da ANEEL (CKAN).
# Não precisa mais baixar xlsx à mão, nem proxy Cloudflare, nem Playwright.
#
# USO:
#   ./atualizar.sh            # baixa, regenera o JSON e faz push
#   ./atualizar.sh --dry-run  # baixa e regenera, mas NÃO faz commit/push
# ─────────────────────────────────────────────────────────────────────────────

set -e
REPO_DIR="/Users/diogosilva/Documents/Tarifa Justa/App Analisador de Fatura/tarifas-aneel"
cd "$REPO_DIR"

DRY_RUN=false
[[ "$1" == "--dry-run" ]] && DRY_RUN=true

echo ""
echo "╔══════════════════════════════════════════════════════╗"
echo "║  Tarifa Justa — Atualização de Tarifas ANEEL         ║"
echo "╚══════════════════════════════════════════════════════╝"
echo ""

# ── 1. Dependências ───────────────────────────────────────────
echo "🔍  Verificando dependências..."
python3 -m pip install -q requests pandas
echo "   ✅ OK"
echo ""

# ── 2. Baixa do CKAN e regenera o JSON ────────────────────────
echo "🚀  Baixando do Dados Abertos e regenerando o JSON..."
python3 atualizar_ckan.py --output data/tarifas_aneel.json

# ── 3. Verifica mudanças ──────────────────────────────────────
echo ""
if git diff --quiet data/; then
    echo "ℹ️   Dados sem alteração — nenhum commit necessário."
    exit 0
fi

if [ "$DRY_RUN" = true ]; then
    echo "🧪  --dry-run: JSON regenerado, sem publicar. Revise com:"
    echo "     git diff --stat data/"
    exit 0
fi

# ── 4. Commita e envia ────────────────────────────────────────
DATA=$(date +%Y-%m-%d)
echo "📤  Enviando para o GitHub..."
git add data/tarifas_aneel.json
git commit -m "chore: atualiza tarifas ANEEL $DATA"
git push

echo ""
echo "╔══════════════════════════════════════════════════════╗"
echo "║  ✅ Concluído! Dados publicados no GitHub.           ║"
echo "║  raw.githubusercontent.com/diogoobs/                 ║"
echo "║  tarifas-aneel/main/data/tarifas_aneel.json          ║"
echo "╚══════════════════════════════════════════════════════╝"
echo ""
