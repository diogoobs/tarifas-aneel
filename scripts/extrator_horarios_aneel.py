#!/usr/bin/env python3
"""
Extrator de Horários dos Postos Tarifários — ANEEL
===================================================
Fonte: https://app.powerbi.com/view?r=eyJrIjoiZTQyNGM4ZWItYzI2ZC00YmU0...
       (Dashboard "Consulte aqui os postos tarifários das distribuidoras")

Lê os horários de cada distribuidora (Intermediário 1, Ponta, Intermediário 2)
e os integra ao tarifas_aneel.json existente.

INSTALAÇÃO:
    pip install playwright pandas
    playwright install chromium

USO:
    python3 extrator_horarios_aneel.py
    python3 extrator_horarios_aneel.py --debug       # com screenshots
    python3 extrator_horarios_aneel.py --headless    # sem janela (padrão)
"""

import asyncio
import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    from playwright.async_api import async_playwright, TimeoutError as PwTimeout
except ImportError:
    print("❌  Execute: pip install playwright && playwright install chromium")
    sys.exit(1)

DASHBOARD_URL = (
    "https://app.powerbi.com/view?r=eyJrIjoiZTQyNGM4ZWItYzI2ZC00YmU0LTg0OWUt"
    "NzMwODRmMmFhNTcwIiwidCI6IjQwZDZmOWI4LWVjYTctNDZhMi05MmQ0LWVhNGU5YzAxNz"
    "BlMSIsImMiOjR9"
)

REPO_DIR  = Path("/Users/diogosilva/Documents/Claude/Tarifa Justa/tarifas-aneel")
JSON_PATH = REPO_DIR / "data" / "tarifas_aneel.json"

LOAD_WAIT_MS   = 20_000
RENDER_WAIT_MS = 3_000
DEBUG_DIR      = Path("debug_horarios")


# ─── EXTRAÇÃO DA TABELA ───────────────────────────────────────────────────────

GET_TABLE_JS = """
() => {
    // Procura a tabela com dados de horários
    // O Power BI renderiza tabelas como elementos com role="grid" ou "table"
    const results = [];

    // Tenta por role="row"
    const rows = document.querySelectorAll('[role="row"]');
    for (const row of rows) {
        const cells = row.querySelectorAll('[role="gridcell"], [role="cell"], td');
        if (cells.length >= 4) {
            const texts = [...cells].map(c => c.textContent.trim());
            if (texts[0] && texts[0].length > 2) results.push(texts);
        }
    }

    // Fallback: procura por tabelas HTML normais
    if (results.length === 0) {
        for (const table of document.querySelectorAll('table')) {
            for (const row of table.querySelectorAll('tr')) {
                const cells = row.querySelectorAll('td, th');
                if (cells.length >= 3) {
                    results.push([...cells].map(c => c.textContent.trim()));
                }
            }
        }
    }

    return results;
}
"""

GET_ALL_TEXT_JS = """
() => {
    const items = [];
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, null, false);
    let node;
    while ((node = walker.nextNode())) {
        const t = node.textContent.trim();
        if (t.length > 2) items.push(t);
    }
    return [...new Set(items)];
}
"""

# Seletores para o dropdown de distribuidoras
SLICER_SELECTORS = [
    '[aria-label*="Distribuidora"]',
    '[aria-label*="distribuidora"]',
    '[title*="Distribuidora"]',
    '[role="combobox"]',
    '.slicer-dropdown-menu',
]

SCROLL_JS = """
() => {
    let scrolled = 0;
    for (const el of document.querySelectorAll('*')) {
        if (el.scrollHeight > el.clientHeight + 20) {
            const style = window.getComputedStyle(el);
            if ((style.overflow + style.overflowY).includes('auto') ||
                (style.overflow + style.overflowY).includes('scroll')) {
                el.scrollTop += 32;
                scrolled++;
            }
        }
    }
    return scrolled;
}
"""


# ─── PARSING DOS HORÁRIOS ─────────────────────────────────────────────────────

def parse_horario(s):
    """
    Converte '16:30-17:30' ou '16:30–17:30' para dict padronizado.
    Retorna None se não for um horário válido.
    """
    if not s or not re.search(r'\d{2}:\d{2}', s):
        return None
    # Normaliza separadores
    s = s.replace('–', '-').replace('—', '-').strip()
    m = re.search(r'(\d{2}:\d{2})\s*[-–]\s*(\d{2}:\d{2})', s)
    if m:
        return {"inicio": m.group(1), "fim": m.group(2), "raw": s}
    return None


def parse_linha_tabela(cells):
    """
    Interpreta uma linha da tabela do dashboard.
    Colunas esperadas: Distribuidora | Intermediário 1 | Horário Ponta | Intermediário 2 | REH
    Retorna dict com os dados ou None se não for linha válida.
    """
    if len(cells) < 4:
        return None

    # Primeira coluna: nome da distribuidora
    nome = cells[0].strip()
    if not nome or len(nome) < 3:
        return None
    # Exclui cabeçalhos
    if any(h in nome for h in ["Distribuidora", "Intermediário", "Horário", "REH"]):
        return None

    int1  = parse_horario(cells[1]) if len(cells) > 1 else None
    ponta = parse_horario(cells[2]) if len(cells) > 2 else None
    int2  = parse_horario(cells[3]) if len(cells) > 3 else None
    reh   = cells[4].strip() if len(cells) > 4 else None

    return {
        "distribuidora":    nome,
        "intermediario_1":  int1,
        "ponta":            ponta,
        "intermediario_2":  int2,
        "reh_horarios":     reh,
    }


# ─── PLAYWRIGHT ───────────────────────────────────────────────────────────────

async def screenshot(page, name, debug):
    if debug:
        DEBUG_DIR.mkdir(exist_ok=True)
        path = str(DEBUG_DIR / f"{name}.png")
        try:
            await page.screenshot(path=path)
            print(f"   📸 {name}.png")
        except: pass


async def get_distribuidoras_slicer(page):
    """Coleta lista de distribuidoras no slicer."""
    names = set()
    prev_count = -1

    for _ in range(100):
        raw = await page.evaluate("""
        () => {
            const names = new Set();
            for (const sel of ['[role="option"]', '[role="listitem"]',
                               '.slicerText', '[class*="slicerItem"]']) {
                for (const el of document.querySelectorAll(sel)) {
                    const t = el.textContent.trim();
                    if (t.length > 2 && t.length < 60) names.add(t);
                }
            }
            return Array.from(names);
        }
        """)

        valid = {n for n in raw if not any(x in n for x in
                 ["Distribuidora", "Região", "Tipo", "Concession"])}
        names.update(valid)

        if len(names) == prev_count:
            break
        prev_count = len(names)

        await page.evaluate(SCROLL_JS)
        await page.wait_for_timeout(300)

    return sorted(names)


async def selecionar_distribuidora(page, nome):
    """Seleciona uma distribuidora no slicer."""
    # Tenta abrir o slicer
    for sel in SLICER_SELECTORS:
        try:
            el = page.locator(sel).first
            if await el.is_visible(timeout=2000):
                await el.click()
                await page.wait_for_timeout(500)
                break
        except: pass

    # Clica no item
    for _ in range(60):
        for sel in [
            f'[role="option"]:has-text("{nome}")',
            f'[role="listitem"]:has-text("{nome}")',
            f'li:has-text("{nome}")',
        ]:
            try:
                loc = page.locator(sel).first
                if await loc.is_visible(timeout=400):
                    await loc.scroll_into_view_if_needed()
                    await loc.click()
                    await page.wait_for_timeout(RENDER_WAIT_MS)
                    return True
            except: pass

        await page.evaluate(SCROLL_JS)
        await page.wait_for_timeout(200)

    return False


async def extrair_tabela(page):
    """Extrai os dados da tabela de horários."""
    rows = await page.evaluate(GET_TABLE_JS)
    resultados = []

    for row in rows:
        parsed = parse_linha_tabela(row)
        if parsed:
            resultados.append(parsed)

    # Fallback: tenta extrair do texto bruto da página
    if not resultados:
        texts = await page.evaluate(GET_ALL_TEXT_JS)
        # Procura por padrões de horário no texto
        for i, t in enumerate(texts):
            if re.match(r'^\d{2}:\d{2}[-–]\d{2}:\d{2}$', t):
                print(f"   📍 Horário encontrado no texto: {t}")

    return resultados


# ─── INTEGRAÇÃO COM O JSON ────────────────────────────────────────────────────

def normalizar_nome(nome):
    """Normaliza nome para comparação — remove acentos e caixa."""
    import unicodedata
    return unicodedata.normalize("NFKD", nome).encode("ascii", "ignore").decode().lower().strip()


def match_distribuidora(nome_dashboard, json_keys):
    """
    Tenta encontrar a distribuidora do dashboard no JSON.
    O dashboard usa nomes como 'Enel SP', o JSON usa 'Enel SP' também —
    mas pode haver variações.
    """
    nome_norm = normalizar_nome(nome_dashboard)

    # Busca exata primeiro
    for key in json_keys:
        if normalizar_nome(key) == nome_norm:
            return key

    # Busca parcial
    for key in json_keys:
        key_norm = normalizar_nome(key)
        if nome_norm in key_norm or key_norm in nome_norm:
            return key

    return None


def integrar_horarios(json_path, horarios_por_distrib):
    """Integra os horários extraídos no JSON de tarifas."""
    with open(json_path, encoding="utf-8") as f:
        data = json.load(f)

    json_keys = list(data["distribuidoras"].keys())
    atualizados = 0
    nao_encontrados = []

    for nome_dash, horarios in horarios_por_distrib.items():
        key = match_distribuidora(nome_dash, json_keys)
        if key:
            data["distribuidoras"][key]["horarios_posto"] = {
                "intermediario_1": horarios.get("intermediario_1"),
                "ponta":           horarios.get("ponta"),
                "intermediario_2": horarios.get("intermediario_2"),
                "reh_horarios":    horarios.get("reh_horarios"),
                "fonte":           "Dashboard Postos Tarifários ANEEL",
                "extraido_em":     datetime.now(timezone.utc).isoformat(),
                "nota":            (
                    "Horários válidos para dias úteis. "
                    "Sábados, domingos e feriados: Fora Ponta integral."
                ),
            }
            atualizados += 1
        else:
            nao_encontrados.append(nome_dash)

    # Atualiza metadados
    data["horarios_atualizados_em"] = datetime.now(timezone.utc).isoformat()

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, default=str)

    return atualizados, nao_encontrados


# ─── MAIN ─────────────────────────────────────────────────────────────────────

async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--headless", action="store_true", default=True)
    parser.add_argument("--debug",    action="store_true")
    parser.add_argument("--json",     default=str(JSON_PATH))
    args = parser.parse_args()

    json_path = Path(args.json)
    if not json_path.exists():
        print(f"❌  JSON não encontrado: {json_path}")
        sys.exit(1)

    print("""
╔══════════════════════════════════════════════════════════════╗
║  Extrator de Horários dos Postos Tarifários — ANEEL          ║
╚══════════════════════════════════════════════════════════════╝
""")
    print(f"  JSON alvo: {json_path}")

    horarios_por_distrib = {}

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=args.headless,
            args=["--no-sandbox", "--disable-setuid-sandbox",
                  "--disable-blink-features=AutomationControlled"],
        )
        ctx = await browser.new_context(
            viewport={"width": 1440, "height": 900},
            locale="pt-BR",
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36"
            ),
        )
        page = await ctx.new_page()

        print("📡  Abrindo dashboard de horários...")
        try:
            await page.goto(DASHBOARD_URL, wait_until="domcontentloaded", timeout=60_000)
        except PwTimeout:
            print("❌  Timeout — dashboard inacessível")
            await browser.close()
            sys.exit(1)

        print(f"⏳  Aguardando Power BI renderizar (~{LOAD_WAIT_MS//1000}s)...")
        await page.wait_for_timeout(LOAD_WAIT_MS)
        await screenshot(page, "01_inicial", args.debug)

        # Tenta extrair tabela sem selecionar distribuidora
        # (se o dashboard mostrar todas de uma vez)
        print("\n🔍  Verificando se tabela mostra todas as distribuidoras...")
        rows_all = await extrair_tabela(page)

        if rows_all:
            print(f"  ✅ {len(rows_all)} linhas encontradas sem filtro")
            for row in rows_all:
                horarios_por_distrib[row["distribuidora"]] = row
        else:
            # Precisa iterar pelas distribuidoras no slicer
            print("  ℹ️  Tabela não visível — iterando pelo slicer...")
            distribuidoras = await get_distribuidoras_slicer(page)
            print(f"  📋 {len(distribuidoras)} distribuidoras no slicer: {distribuidoras[:5]}...")

            total = len(distribuidoras)
            for idx, nome in enumerate(distribuidoras, 1):
                print(f"  [{idx:02d}/{total}] {nome}")

                if not await selecionar_distribuidora(page, nome):
                    print(f"    ✗ Não foi possível selecionar")
                    continue

                await screenshot(page, f"{idx:02d}_{nome[:20]}", args.debug)
                rows = await extrair_tabela(page)

                if rows:
                    row = rows[0]  # pega a linha da distribuidora selecionada
                    horarios_por_distrib[nome] = row
                    p_str = row.get("ponta", {})
                    p_raw = p_str.get("raw", "?") if p_str else "?"
                    print(f"    ✓ Ponta: {p_raw}")
                else:
                    print(f"    ✗ Sem dados")

        await browser.close()

    if not horarios_por_distrib:
        print("\n❌  Nenhum horário extraído.")
        sys.exit(1)

    print(f"\n✅  {len(horarios_por_distrib)} distribuidoras com horários extraídos")

    # Integra no JSON
    print(f"\n🔄  Integrando no JSON...")
    atualizados, nao_encontrados = integrar_horarios(json_path, horarios_por_distrib)

    print(f"  ✅ {atualizados} distribuidoras atualizadas no JSON")
    if nao_encontrados:
        print(f"  ⚠️  Não encontrados no JSON: {nao_encontrados}")

    print(f"""
╔══════════════════════════════════════════════════════════════╗
║  Concluído! {atualizados} distribuidoras com horários integrados
╚══════════════════════════════════════════════════════════════╝

Próximo passo:
  cd "{REPO_DIR}"
  git add data/tarifas_aneel.json
  git commit -m "chore: adiciona horários dos postos tarifários"
  git push
""")


if __name__ == "__main__":
    asyncio.run(main())
