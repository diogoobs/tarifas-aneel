#!/usr/bin/env python3
"""
Extrator de Tarifas ANEEL — Playwright
=======================================
Abre o portal, aplica filtros e clica em Baixar dados para obter o Excel.
"""

import asyncio
import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from io import BytesIO

try:
    from playwright.async_api import async_playwright, TimeoutError as PwTimeout
except ImportError:
    print("❌  pip install playwright && playwright install chromium")
    sys.exit(1)

try:
    import pandas as pd
except ImportError:
    print("❌  pip install pandas openpyxl")
    sys.exit(1)

PORTAL_URL   = "https://portalrelatorios.aneel.gov.br/luznatarifa/basestarifas#!"
DEFAULT_OUT  = "data/tarifas_aneel.json"
DEBUG_DIR    = Path("debug_screenshots")


# ─── HELPERS ──────────────────────────────────────────────────────────────────

def parse_float_br(s):
    if s is None or str(s).strip() in ("", "-", "nan"): return None
    try: return float(str(s).replace(".", "").replace(",", "."))
    except: return None

def normaliza_reh(reh):
    m = re.search(r"(\d[\d\.]+).*?(\d{4})\s*$", str(reh).strip())
    if not m: return (0, 0)
    return (int(m.group(2)), int(m.group(1).replace(".", "")))

def eh_linha_valida(s):
    s = str(s).strip()
    if not s: return False
    for t in ["Filtros", "Base Tarifária", "Tipo de Outorga", "Ano é", "Flag é"]:
        if t in s: return False
    return True

async def screenshot(page, name, debug):
    if debug:
        DEBUG_DIR.mkdir(exist_ok=True)
        await page.screenshot(path=str(DEBUG_DIR / f"{name}.png"))
        print(f"   📸 {name}.png")


# ─── PROCESSAMENTO DO EXCEL ───────────────────────────────────────────────────

def processar_excel(data: bytes) -> dict:
    df = pd.read_excel(BytesIO(data), dtype=str)
    print(f"  ✅ Excel: {len(df):,} linhas × {len(df.columns)} colunas")
    df.columns = [c.strip() for c in df.columns]
    df["Sigla"] = df.get("Sigla", pd.Series(dtype=str)).fillna("").astype(str)
    df = df[df["Sigla"].apply(eh_linha_valida)].copy()
    if "Base Tarifária" in df.columns:
        df = df[df["Base Tarifária"].fillna("").str.strip() == "Tarifa de Aplicação"]
    col_reh = "Resolução ANEEL"
    if col_reh in df.columns:
        df["_s"] = df[col_reh].apply(normaliza_reh)
        df = df[df.groupby("Sigla")["_s"].transform("max") == df["_s"]].copy()
        df.drop(columns=["_s"], inplace=True)
    resultado = {}
    for sigla, g in df.groupby("Sigla"):
        reh = g[col_reh].iloc[0] if col_reh in g else None
        tarifas = []
        for _, row in g.iterrows():
            tusd = parse_float_br(row.get("TUSD"))
            te   = parse_float_br(row.get("TE"))
            tarifas.append({
                "vigencia_inicio": str(row.get("Início Vigência","")).split(" ")[0],
                "vigencia_fim":    str(row.get("Fim Vigência","")).split(" ")[0],
                "subgrupo":   str(row.get("Subgrupo",   "") or "").strip(),
                "modalidade": str(row.get("Modalidade", "") or "").strip(),
                "classe":     str(row.get("Classe",     "") or "").strip(),
                "subclasse":  str(row.get("Subclasse",  "") or "").strip(),
                "detalhe":    str(row.get("Detalhe",    "") or "").strip(),
                "acessante":  str(row.get("Acessante",  "") or "").strip(),
                "posto":      str(row.get("Posto",      "") or "").strip(),
                "unidade":    str(row.get("Unidade",    "") or "").strip(),
                "vlr_tusd":   tusd,
                "vlr_te":     te,
                "vlr_total":  round(tusd+te, 6) if tusd is not None and te is not None else None,
            })
        resultado[sigla] = {
            "sigla": sigla, "reh": str(reh) if reh else None,
            "base_tarifaria": "Tarifa de Aplicação",
            "tarifas": tarifas, "total_registros": len(tarifas),
        }
    return resultado


# ─── PLAYWRIGHT ───────────────────────────────────────────────────────────────

async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output",   default=DEFAULT_OUT)
    parser.add_argument("--headless", action="store_true", default=True)
    parser.add_argument("--debug",    action="store_true")
    args = parser.parse_args()

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    print("""
╔══════════════════════════════════════════════════════════════╗
║  Extrator ANEEL — Playwright                                 ║
╚══════════════════════════════════════════════════════════════╝
""")

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=args.headless,
            args=["--no-sandbox", "--disable-setuid-sandbox",
                  "--disable-blink-features=AutomationControlled",
                  "--disable-web-security"],
        )
        ctx = await browser.new_context(
            viewport={"width": 1440, "height": 900},
            locale="pt-BR",
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            accept_downloads=True,
        )
        page = await ctx.new_page()

        # ── 1. Carrega portal ─────────────────────────────────
        print("📡  Abrindo portal ANEEL...")
        try:
            await page.goto(PORTAL_URL, wait_until="domcontentloaded", timeout=45_000)
        except PwTimeout:
            print("❌  Timeout ao carregar o portal")
            await screenshot(page, "erro_timeout", True)
            await browser.close()
            sys.exit(1)

        print("   ✅ Portal carregado")
        await screenshot(page, "01_portal", args.debug)

        # ── 2. Aguarda Power BI renderizar ────────────────────
        print("⏳  Aguardando Power BI renderizar...")
        await page.wait_for_timeout(18_000)
        await screenshot(page, "02_apos_espera", args.debug)

        # ── 3. Aplica filtros ─────────────────────────────────
        print("🔧  Aplicando filtros...")

        # Filtra Tipo de Outorga = Concessionária
        for filtro, valor in [("Tipo de Outorga", "Concessionária"),
                               ("Base Tarifária", "Tarifa de Aplicação")]:
            for sel in [f'[aria-label*="{filtro}"]', f'[title*="{filtro}"]',
                        f'div:has-text("{filtro}") [role="combobox"]']:
                try:
                    el = page.locator(sel).first
                    if await el.is_visible(timeout=2000):
                        await el.click()
                        await page.wait_for_timeout(1000)
                        opt = page.locator(f'[role="option"]:has-text("{valor}")').first
                        if await opt.is_visible(timeout=2000):
                            await opt.click()
                            await page.wait_for_timeout(1500)
                            print(f"   ✅ {filtro} = {valor}")
                            break
                except: pass

        await page.wait_for_timeout(2000)
        await screenshot(page, "03_filtros", args.debug)

        # ── 4. Clica em Baixar dados ──────────────────────────
        print("📥  Clicando em Baixar dados...")

        download_clicked = False
        # O botão "Baixar dados" no Power BI tem um ícone de seta
        # Tenta vários seletores possíveis
        for sel in [
            '[aria-label="Baixar dados"]',
            '[title="Baixar dados"]',
            'button[aria-label*="Baixar"]',
            '[aria-label*="Export"]',
            '[title*="Export"]',
            # Seletor por posição — o botão fica no canto superior direito da tabela
            '.visual-container button[aria-label]',
        ]:
            try:
                btns = page.locator(sel)
                count = await btns.count()
                for i in range(count):
                    btn = btns.nth(i)
                    if await btn.is_visible(timeout=1000):
                        await btn.click()
                        download_clicked = True
                        print(f"   ✅ Botão encontrado: {sel}")
                        break
                if download_clicked:
                    break
            except: pass

        if not download_clicked:
            # Fallback: procura por qualquer botão visível próximo ao texto "Baixar"
            try:
                btn = page.get_by_text("Baixar dados", exact=False).first
                if await btn.is_visible(timeout=3000):
                    await btn.click()
                    download_clicked = True
                    print("   ✅ Botão 'Baixar dados' encontrado por texto")
            except: pass

        if not download_clicked:
            print("   ❌ Botão 'Baixar dados' não encontrado")
            await screenshot(page, "erro_sem_botao", True)
            # Lista todos os botões visíveis para debug
            btns = await page.query_selector_all("button, [role='button']")
            print(f"   Botões na página ({len(btns)}):")
            for btn in btns[:15]:
                label = await btn.get_attribute("aria-label") or ""
                title = await btn.get_attribute("title") or ""
                text  = (await btn.inner_text() or "")[:30]
                if label or title or text:
                    print(f"     aria-label='{label}' title='{title}' text='{text}'")
            await browser.close()
            sys.exit(1)

        await page.wait_for_timeout(2000)
        await screenshot(page, "04_modal_aberto", args.debug)

        # ── 5. Seleciona "Dados com layout atual" e xlsx ──────
        print("   Selecionando opções de export...")

        for sel in ['text="Dados com layout atual"',
                    'label:has-text("layout atual")',
                    '[aria-label*="layout atual"]']:
            try:
                opt = page.locator(sel).first
                if await opt.is_visible(timeout=3000):
                    await opt.click()
                    break
            except: pass

        await page.wait_for_timeout(500)

        # Garante xlsx no dropdown
        for sel in ['select', '[role="combobox"]', 'select[aria-label]']:
            try:
                combo = page.locator(sel).first
                if await combo.is_visible(timeout=2000):
                    await combo.select_option(label="xlsx (Excel com no máximo 150.000 linhas)")
                    break
            except: pass

        await page.wait_for_timeout(500)
        await screenshot(page, "05_opcoes_export", args.debug)

        # ── 6. Clica Exportar e intercepta download ───────────
        print("   Exportando...")
        try:
            async with page.expect_download(timeout=60_000) as dl_info:
                for sel in ['button:has-text("Exportar")',
                            '[aria-label="Exportar"]',
                            'button:has-text("Export")']:
                    try:
                        btn = page.locator(sel).first
                        if await btn.is_visible(timeout=3000):
                            await btn.click()
                            print("   ✅ Exportar clicado")
                            break
                    except: pass

            download = await dl_info.value
            path = await download.path()
            excel_bytes = Path(path).read_bytes()
            print(f"   ✅ Download: {len(excel_bytes)/1024:.0f} KB")
        except Exception as e:
            print(f"   ❌ Erro no download: {e}")
            await screenshot(page, "erro_download", True)
            await browser.close()
            sys.exit(1)

        await browser.close()

    # ── 7. Processa Excel ─────────────────────────────────────
    print("\n🔄  Processando Excel...")
    resultado = processar_excel(excel_bytes)

    # Resumo
    print(f"\n{'─'*65}")
    for sigla, d in sorted(resultado.items()):
        reh = re.search(r"(\d[\d\.]+)", str(d.get("reh",""))).group(1)[:8] if d.get("reh") else "?"
        print(f"  {sigla:<28} {reh:>8}  {d.get('total_registros',0):>6}")
    print(f"{'─'*65}")
    print(f"  Total: {len(resultado)} distribuidoras\n")

    envelope = {
        "gerado_em":            datetime.now(timezone.utc).isoformat(),
        "fonte":                "Portal Luz na Tarifa — ANEEL (Playwright)",
        "url_fonte":            PORTAL_URL,
        "filtros": {
            "base_tarifaria":   "Tarifa de Aplicação",
            "reh":              "Mais recente por distribuidora",
        },
        "total_distribuidoras": len(resultado),
        "distribuidoras":       resultado,
    }

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(envelope, f, ensure_ascii=False, indent=2, default=str)

    print(f"✅  {len(resultado)} distribuidoras → {out_path}")

if __name__ == "__main__":
    asyncio.run(main())
