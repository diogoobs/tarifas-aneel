#!/usr/bin/env python3
"""
Extrator de Horários dos Postos Tarifários — ANEEL
===================================================
Dashboard: https://app.powerbi.com/view?r=eyJrIjoiZTQyNGM4...

USO:
    python3 extrator_horarios_aneel.py
    python3 extrator_horarios_aneel.py --debug
"""

import asyncio, argparse, json, re, sys, unicodedata
from datetime import datetime, timezone
from pathlib import Path

try:
    from playwright.async_api import async_playwright, TimeoutError as PwTimeout
except ImportError:
    print("❌  pip install playwright && playwright install chromium"); sys.exit(1)

DASHBOARD_URL = (
    "https://app.powerbi.com/view?r=eyJrIjoiZTQyNGM4ZWItYzI2ZC00YmU0LTg0OWUt"
    "NzMwODRmMmFhNTcwIiwidCI6IjQwZDZmOWI4LWVjYTctNDZhMi05MmQ0LWVhNGU5YzAxNz"
    "BlMSIsImMiOjR9"
)
REPO_DIR  = Path("/Users/diogosilva/Documents/Claude/Tarifa Justa/tarifas-aneel")
JSON_PATH = REPO_DIR / "data" / "tarifas_aneel.json"
LOAD_WAIT = 22_000
DEBUG_DIR = REPO_DIR / "debug_horarios"


# ─── JS: extrai a tabela completa ─────────────────────────────────────────────
# A tabela Power BI tem duas partes:
#   1. Coluna de rótulos (nomes das distribuidoras) — role="rowheader" ou primeira coluna fixa
#   2. Células de dados (horários) — role="gridcell" class="pivotTableCellNoWrap"
# Ambas têm role="row" como pai, mas em containers separados.
# A estratégia é pegar todas as rows e dentro de cada row pegar todas as gridcells.

EXTRACT_JS = """
() => {
    // Estrutura confirmada por diagnóstico:
    // Cada [role="row"] tem 6 gridcells:
    //   [0] class="prefix-cell"           → "Selecionar Linha" (ignorar)
    //   [1] class="pivotTableCellNoWrap"  → Nome da distribuidora
    //   [2] class="pivotTableCellNoWrap"  → Intermediário 1
    //   [3] class="pivotTableCellNoWrap"  → Horário Ponta
    //   [4] class="pivotTableCellNoWrap"  → Intermediário 2
    //   [5] class="pivotTableCellNoWrap"  → REH
    const TIME_RE = /\\d{1,2}:\\d{2}/;
    const rows = [];

    for (const row of document.querySelectorAll('[role="row"]')) {
        // Pega só as células de dados (exclui prefix-cell)
        const cells = [...row.querySelectorAll('[role="gridcell"]')]
            .filter(c => !c.classList.contains('prefix-cell'));

        if (cells.length < 4) continue;

        const texts = cells.map(c => c.innerText?.trim() || '');

        // Linha válida: primeira célula é nome (não horário) e tem >= 2 horários
        const timeCount = texts.slice(1).filter(t => TIME_RE.test(t)).length;
        if (timeCount < 2) continue;
        if (TIME_RE.test(texts[0])) continue; // primeira célula não pode ser horário

        rows.push(texts);
    }
    return rows;
}
"""

# JS alternativo: lê a tabela inteira por posição Y (agrupa células pela mesma linha Y)
EXTRACT_BY_POSITION_JS = """
() => {
    const TIME_RE  = /^\\d{1,2}:\\d{2}-\\d{1,2}:\\d{2}$/;
    const REH_RE   = /^\\d[\\.\\d]*\\/\\d{4}$/;
    const NAME_RE  = /^[A-ZÁÉÍÓÚÀÃÕÂÊÔÇÑ][a-záéíóúàãõâêôçñA-Z0-9 \\-\\.]+$/;

    // Coleta TODOS os gridcells com suas posições
    const cells = [];
    for (const el of document.querySelectorAll('[role="gridcell"], [role="rowheader"]')) {
        const text = el.innerText?.trim();
        if (!text || text.length < 2) continue;
        const rect = el.getBoundingClientRect();
        if (rect.width < 5 || rect.height < 5) continue;
        cells.push({
            text,
            x: Math.round(rect.left),
            y: Math.round(rect.top),
            w: Math.round(rect.width),
            h: Math.round(rect.height),
        });
    }

    // Agrupa por linha (Y próximos ± 5px)
    const rows = {};
    for (const c of cells) {
        const key = Math.round(c.y / 5) * 5;
        if (!rows[key]) rows[key] = [];
        rows[key].push(c);
    }

    // Ordena células de cada linha por X e monta array de linhas
    const result = [];
    for (const [y, rowCells] of Object.entries(rows).sort((a,b) => a[0]-b[0])) {
        const sorted = rowCells.sort((a,b) => a.x - b.x);
        const texts  = sorted.map(c => c.text);
        // Linha válida: tem ao menos 2 horários
        const timeCount = texts.filter(t => TIME_RE.test(t) || /\\d{2}:\\d{2}/.test(t)).length;
        if (timeCount >= 2) result.push(texts);
    }
    return result;
}
"""

SCROLL_TABLE_JS = """
() => {
    // Rola o container scrollável da tabela Power BI
    let scrolled = false;
    for (const el of document.querySelectorAll('.scrollable-cells-container, [class*="scroll"]')) {
        const before = el.scrollTop;
        el.scrollTop += 250;
        if (el.scrollTop !== before) { scrolled = true; break; }
    }
    // Fallback: rola qualquer container scrollável grande
    if (!scrolled) {
        for (const el of document.querySelectorAll('*')) {
            if (el.scrollHeight > el.clientHeight + 50) {
                const rect = el.getBoundingClientRect();
                if (rect.height > 150 && rect.height < 700) {
                    const before = el.scrollTop;
                    el.scrollTop += 250;
                    if (el.scrollTop !== before) { scrolled = true; break; }
                }
            }
        }
    }
    return scrolled;
}
"""

# ─── HELPERS ──────────────────────────────────────────────────────────────────

def parse_horario(s):
    if not s: return None
    s = re.sub(r'[–—]', '-', s).strip()
    m = re.search(r'(\d{1,2}:\d{2})\s*-\s*(\d{1,2}:\d{2})', s)
    if m:
        return {"inicio": m.group(1).zfill(5), "fim": m.group(2).zfill(5)}
    return None

def parse_linha(cells):
    """
    Ordem confirmada: [nome, int1, ponta, int2, reh]
    """
    if len(cells) < 4: return None
    nome = cells[0].strip()
    if not nome or len(nome) < 2: return None
    # Exclui cabeçalhos
    if any(h in nome for h in ["Distribuidora", "Selecionar", "Intermediário",
                                 "Horário", "REH", "Região", "Tipo"]):
        return None

    return {
        "distribuidora":   nome,
        "intermediario_1": parse_horario(cells[1]) if len(cells) > 1 else None,
        "ponta":           parse_horario(cells[2]) if len(cells) > 2 else None,
        "intermediario_2": parse_horario(cells[3]) if len(cells) > 3 else None,
        "reh_horarios":    cells[4].strip() if len(cells) > 4 else None,
    }

def normalizar(s):
    return unicodedata.normalize("NFKD", s or "").encode("ascii","ignore").decode().lower().strip()

def match_key(nome_dash, json_keys):
    nd = normalizar(nome_dash)
    for k in json_keys:
        if normalizar(k) == nd: return k
    for k in json_keys:
        kn = normalizar(k)
        if nd in kn or kn in nd: return k
    nd2 = re.sub(r'\s*(s\.?a\.?|dis|energia|eletrica|distribuidora)$', '', nd).strip()
    for k in json_keys:
        kn2 = re.sub(r'\s*(s\.?a\.?|dis|energia|eletrica|distribuidora)$',
                     '', normalizar(k)).strip()
        if nd2 and kn2 and (nd2 in kn2 or kn2 in nd2): return k
    return None

async def screenshot(page, name, debug):
    if debug:
        DEBUG_DIR.mkdir(exist_ok=True)
        try:
            await page.screenshot(path=str(DEBUG_DIR / f"{name}.png"))
            print(f"   📸 {name}.png")
        except: pass


# ─── EXTRAÇÃO COMPLETA COM SCROLL ─────────────────────────────────────────────

async def extrair_nomes_coluna_fixa(page):
    """
    Extrai os nomes da coluna fixa (frozen) da tabela.
    No Power BI, a primeira coluna fica num container separado.
    """
    nomes = await page.evaluate("""
    () => {
        // Procura pela coluna de rótulos/nomes
        // Geralmente fica num container com classe "row-header" ou similar
        const candidates = [];
        for (const el of document.querySelectorAll('[role="rowheader"], .pivotTableCellNoWrap')) {
            const t = el.innerText?.trim();
            if (t && t.length > 2 && !/\\d{2}:\\d{2}/.test(t) && !/^\\d+\\.?\\d*\\/\\d{4}$/.test(t)
                && !['Distribuidora','Região','Tipo','Intermediário','Horário','REH'].some(h => t.includes(h))) {
                candidates.push(t);
            }
        }
        return [...new Set(candidates)];
    }
    """)
    return nomes

async def extrair_dados_por_posicao(page):
    """
    Usa posições Y para correlacionar nomes com horários.
    Funciona mesmo quando nome e horários estão em containers separados.
    """
    data = await page.evaluate("""
    () => {
        const TIME_RE = /\\d{1,2}:\\d{2}/;
        const items = [];

        for (const el of document.querySelectorAll('[role="gridcell"], [role="rowheader"]')) {
            const text = el.innerText?.trim();
            if (!text || text.length < 2) continue;
            const rect = el.getBoundingClientRect();
            if (rect.width < 5 || rect.height < 5) continue;
            // Só pega elementos visíveis na viewport
            if (rect.top < 0 || rect.top > window.innerHeight) continue;

            items.push({
                text,
                y: Math.round(rect.top),
                x: Math.round(rect.left),
                isTime: TIME_RE.test(text),
                isReh:  /^\\d[\\.\\d]*\\/\\d{4}$/.test(text),
            });
        }

        // Agrupa por Y (±8px de tolerância)
        const groups = {};
        for (const item of items) {
            const key = Math.round(item.y / 8) * 8;
            if (!groups[key]) groups[key] = [];
            groups[key].push(item);
        }

        // Monta linhas: precisa ter ao menos 2 horários
        const rows = [];
        for (const [y, cells] of Object.entries(groups).sort((a,b)=>+a[0]-+b[0])) {
            cells.sort((a,b) => a.x - b.x);
            const times = cells.filter(c => c.isTime);
            if (times.length >= 2) {
                rows.push(cells.map(c => c.text));
            }
        }
        return rows;
    }
    """)
    return data

async def extrair_todas(page, debug):
    """Extrai todas as linhas fazendo scroll."""
    todas = {}
    prev_count = -1
    no_new = 0

    while True:
        # Tenta os dois métodos
        rows = await page.evaluate(EXTRACT_JS)
        if not rows:
            rows = await extrair_dados_por_posicao(page)

        for r in rows:
            parsed = parse_linha(r)
            if parsed and parsed["distribuidora"] and parsed["ponta"]:
                nome = parsed["distribuidora"]
                if nome not in todas:
                    todas[nome] = parsed

        print(f"   📊 Linhas acumuladas: {len(todas)}", end="\r")

        if len(todas) == prev_count:
            no_new += 1
            if no_new >= 5: break
        else:
            no_new = 0
            prev_count = len(todas)

        scrolled = await page.evaluate(SCROLL_TABLE_JS)
        if not scrolled:
            await page.keyboard.press("PageDown")
        await page.wait_for_timeout(700)

    print()
    return todas


# ─── INTEGRAÇÃO NO JSON ───────────────────────────────────────────────────────

def integrar(json_path, horarios):
    with open(json_path, encoding="utf-8") as f:
        data = json.load(f)
    keys = list(data["distribuidoras"].keys())
    ok, nok = 0, []
    for nome, h in horarios.items():
        key = match_key(nome, keys)
        if key:
            data["distribuidoras"][key]["horarios_posto"] = {
                "intermediario_1": h.get("intermediario_1"),
                "ponta":           h.get("ponta"),
                "intermediario_2": h.get("intermediario_2"),
                "reh_horarios":    h.get("reh_horarios"),
                "nota":            "Dias úteis. Sáb/Dom/feriados: Fora Ponta integral.",
                "extraido_em":     datetime.now(timezone.utc).isoformat(),
            }
            ok += 1
        else:
            nok.append(nome)
    data["horarios_atualizados_em"] = datetime.now(timezone.utc).isoformat()
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, default=str)
    return ok, nok


# ─── MAIN ─────────────────────────────────────────────────────────────────────

async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--headless", action="store_true", default=True)
    parser.add_argument("--debug",    action="store_true")
    parser.add_argument("--json",     default=str(JSON_PATH))
    args = parser.parse_args()

    json_path = Path(args.json)
    if not json_path.exists():
        print(f"❌  JSON não encontrado: {json_path}"); sys.exit(1)

    print("""
╔══════════════════════════════════════════════════════════════╗
║  Extrator de Horários dos Postos Tarifários — ANEEL          ║
╚══════════════════════════════════════════════════════════════╝
""")

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=args.headless,
            args=["--no-sandbox", "--disable-setuid-sandbox",
                  "--disable-blink-features=AutomationControlled"],
        )
        ctx = await browser.new_context(
            viewport={"width": 1440, "height": 900}, locale="pt-BR",
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36",
        )
        page = await ctx.new_page()

        print("📡  Abrindo dashboard...")
        try:
            await page.goto(DASHBOARD_URL, wait_until="domcontentloaded", timeout=60_000)
        except PwTimeout:
            print("❌  Timeout"); await browser.close(); sys.exit(1)

        print(f"⏳  Aguardando renderização (~{LOAD_WAIT//1000}s)...")
        await page.wait_for_timeout(LOAD_WAIT)
        await screenshot(page, "01_carregado", args.debug)

        print("📋  Extraindo horários (com scroll)...")
        horarios = await extrair_todas(page, args.debug)

        if args.debug:
            await screenshot(page, "02_final", args.debug)
        await browser.close()

    print(f"\n✅  {len(horarios)} distribuidoras extraídas")

    if not horarios:
        print("❌  Nenhum dado extraído — verifique debug screenshots")
        sys.exit(1)

    # Resumo
    print(f"\n{'─'*60}")
    for nome, h in sorted(horarios.items())[:8]:
        p = h.get("ponta") or {}
        print(f"  {nome:<28} Ponta: {p.get('inicio','?')}–{p.get('fim','?')}")
    if len(horarios) > 8:
        print(f"  ... e mais {len(horarios)-8}")
    print(f"{'─'*60}")

    print(f"\n🔄  Integrando no JSON...")
    ok, nok = integrar(json_path, horarios)
    print(f"  ✅ {ok} distribuidoras atualizadas no JSON")
    if nok:
        print(f"  ⚠️  Sem match ({len(nok)}): {nok[:8]}")

    print(f"""
╔══════════════════════════════════════════════════════════════╗
║  Concluído! {ok} distribuidoras com horários integrados
╚══════════════════════════════════════════════════════════════╝

Próximo passo:
  cd "{REPO_DIR}"
  git add data/tarifas_aneel.json
  git commit -m "chore: adiciona horários dos postos tarifários"
  git push
""")

if __name__ == "__main__":
    asyncio.run(main())
