#!/usr/bin/env python3
"""
Extrator de Tarifas ANEEL — Playwright (download automático do portal)
======================================================================
Fonte: https://portalrelatorios.aneel.gov.br/luznatarifa/basestarifas#!

INSTALAÇÃO:
    pip install playwright
    playwright install chromium

USO:
    python3 extrator_playwright.py              # baixa e processa
    python3 extrator_playwright.py --debug      # com screenshots
    python3 extrator_playwright.py --headless   # sem janela (padrão no Actions)
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
    print("❌  Execute: pip install playwright && playwright install chromium")
    sys.exit(1)

try:
    import pandas as pd
except ImportError:
    print("❌  Execute: pip install pandas openpyxl")
    sys.exit(1)

PORTAL_URL   = "https://portalrelatorios.aneel.gov.br/luznatarifa/basestarifas#!"
DEFAULT_OUT  = "data/tarifas_aneel.json"
PAGE_WAIT_MS = 15_000   # aguarda Power BI renderizar


# ─── HELPERS (reutilizados do extrator principal) ──────────────────────────────

def parse_float_br(s):
    if s is None or str(s).strip() in ("", "-", "nan", "0"):
        return None
    try:
        return float(str(s).replace(".", "").replace(",", "."))
    except ValueError:
        return None

def normaliza_reh(reh: str) -> tuple:
    m = re.search(r"(\d[\d\.]+).*?(\d{4})\s*$", str(reh).strip())
    if not m:
        return (0, 0)
    return (int(m.group(2)), int(m.group(1).replace(".", "")))

def eh_linha_valida(sigla: str) -> bool:
    if not sigla or not str(sigla).strip():
        return False
    s = str(sigla).strip()
    for termo in ["Filtros", "Base Tarifária", "Tipo de Outorga", "Ano é", "Flag é", "Em branco"]:
        if termo in s:
            return False
    return True


# ─── APLICAR FILTROS NO POWER BI ─────────────────────────────────────────────

async def aplicar_filtros(page, debug=False):
    """Aplica os filtros no painel Power BI."""
    print("🔧  Aplicando filtros...")

    # Aguarda os slicers carregarem
    await page.wait_for_timeout(PAGE_WAIT_MS)

    if debug:
        await page.screenshot(path="debug_01_inicial.png")
        print("   📸 debug_01_inicial.png")

    # Filtro: Tipo de Outorga = Concessionária
    await _selecionar_filtro(page, "Tipo de Outorga", "Concessionária", debug)

    # Filtro: Base Tarifária = Tarifa de Aplicação
    await _selecionar_filtro(page, "Base Tarifária", "Tarifa de Aplicação", debug)

    # Aguarda dados recarregarem
    await page.wait_for_timeout(3000)

    if debug:
        await page.screenshot(path="debug_02_filtros.png")
        print("   📸 debug_02_filtros.png")

    print("   ✅ Filtros aplicados")


async def _selecionar_filtro(page, label: str, valor: str, debug=False):
    """Tenta selecionar um valor em um slicer/dropdown do Power BI."""
    strategies = [
        # Dropdown com aria-label
        f'[aria-label*="{label}"]',
        # Texto do slicer
        f'[title="{label}"]',
        # Role option com o texto
        f'[role="option"]:has-text("{valor}")',
    ]

    for sel in strategies:
        try:
            loc = page.locator(sel).first
            if await loc.is_visible(timeout=3000):
                await loc.click()
                await page.wait_for_timeout(1000)

                # Tenta clicar no valor dentro do dropdown aberto
                opt = page.locator(f'[role="option"]:has-text("{valor}"), '
                                    f'li:has-text("{valor}")').first
                if await opt.is_visible(timeout=2000):
                    await opt.click()
                    await page.wait_for_timeout(1000)
                    print(f"   ✅ {label} = {valor}")
                    return
        except Exception:
            pass

    print(f"   ⚠  Não foi possível aplicar filtro: {label} = {valor}")


# ─── DOWNLOAD DO EXCEL ────────────────────────────────────────────────────────

async def baixar_excel(page, debug=False) -> bytes | None:
    """
    Clica em 'Baixar dados', seleciona 'Dados com layout atual' e xlsx,
    intercepta o download e retorna os bytes do arquivo.
    """
    print("📥  Iniciando download do Excel...")

    # Configura interceptação de download
    async with page.expect_download(timeout=60_000) as dl_info:
        # Procura e clica no botão "Baixar dados"
        clicked = False
        for sel in [
            '[aria-label*="Baixar"]',
            '[aria-label*="Download"]',
            '[title*="Baixar"]',
            'button:has-text("Baixar")',
            '[role="button"]:has-text("Baixar")',
        ]:
            try:
                btn = page.locator(sel).first
                if await btn.is_visible(timeout=3000):
                    await btn.click()
                    clicked = True
                    print(f"   ✅ Botão encontrado: {sel}")
                    break
            except Exception:
                pass

        if not clicked:
            # Fallback: procura pelo ícone de download (seta para baixo)
            try:
                # Power BI usa SVG com path específico para o ícone de download
                await page.click('[data-testid="export-button"], .export-button', timeout=3000)
                clicked = True
            except Exception:
                pass

        if not clicked:
            print("   ❌ Botão 'Baixar dados' não encontrado")
            if debug:
                await page.screenshot(path="debug_03_sem_botao.png")
            return None

        await page.wait_for_timeout(2000)

        if debug:
            await page.screenshot(path="debug_03_modal.png")
            print("   📸 debug_03_modal.png")

        # Seleciona "Dados com layout atual"
        for sel in [
            'text="Dados com layout atual"',
            '[aria-label*="layout atual"]',
            'label:has-text("layout atual")',
        ]:
            try:
                opt = page.locator(sel).first
                if await opt.is_visible(timeout=3000):
                    await opt.click()
                    break
            except Exception:
                pass

        await page.wait_for_timeout(1000)

        # Garante formato xlsx
        for sel in ['select', '[role="combobox"]']:
            try:
                combo = page.locator(sel).first
                if await combo.is_visible(timeout=2000):
                    await combo.select_option(label="xlsx (Excel com no máximo 150.000 linhas)")
                    break
            except Exception:
                pass

        await page.wait_for_timeout(500)

        # Clica em Exportar
        for sel in [
            'button:has-text("Exportar")',
            '[aria-label="Exportar"]',
            'text="Exportar"',
        ]:
            try:
                btn = page.locator(sel).first
                if await btn.is_visible(timeout=3000):
                    await btn.click()
                    print("   ✅ Exportar clicado")
                    break
            except Exception:
                pass

    try:
        download = await dl_info.value
        path = await download.path()
        data = Path(path).read_bytes()
        print(f"   ✅ Download concluído: {len(data)/1024:.0f} KB")
        return data
    except Exception as e:
        print(f"   ❌ Erro no download: {e}")
        return None


# ─── PROCESSAMENTO DO EXCEL ───────────────────────────────────────────────────

def processar_excel(data: bytes, debug=False) -> dict:
    """Processa os bytes do Excel e retorna o dict de distribuidoras."""
    df = pd.read_excel(BytesIO(data), dtype=str)
    print(f"  ✅ {len(df):,} linhas × {len(df.columns)} colunas")

    df.columns = [str(c).strip() for c in df.columns]
    df["Sigla"] = df.get("Sigla", pd.Series(dtype=str)).fillna("").astype(str)

    # Remove metadados
    df = df[df["Sigla"].apply(eh_linha_valida)].copy()

    # Filtra base tarifária
    if "Base Tarifária" in df.columns:
        df = df[df["Base Tarifária"].fillna("").str.strip() == "Tarifa de Aplicação"]

    # REH mais recente
    df["_sort"] = df.get("Resolução ANEEL", pd.Series(dtype=str)).apply(normaliza_reh)
    df = df[df.groupby("Sigla")["_sort"].transform("max") == df["_sort"]].copy()
    df.drop(columns=["_sort"], inplace=True)

    resultado = {}
    for sigla, grupo in df.groupby("Sigla"):
        reh = grupo.get("Resolução ANEEL", pd.Series()).iloc[0] if len(grupo) else None
        ini = grupo.get("Início Vigência", pd.Series()).iloc[0] if len(grupo) else None
        fim = grupo.get("Fim Vigência",    pd.Series()).iloc[0] if len(grupo) else None

        tarifas = []
        for _, row in grupo.iterrows():
            tusd = parse_float_br(row.get("TUSD"))
            te   = parse_float_br(row.get("TE"))
            tarifas.append({
                "vigencia_inicio": str(row.get("Início Vigência", "")).split(" ")[0],
                "vigencia_fim":    str(row.get("Fim Vigência",    "")).split(" ")[0],
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
                "vlr_total":  round(tusd + te, 6) if tusd is not None and te is not None else None,
            })

        resultado[sigla] = {
            "sigla":           sigla,
            "reh":             str(reh) if reh else None,
            "vigencia_inicio": str(ini).split(" ")[0] if ini else None,
            "vigencia_fim":    str(fim).split(" ")[0] if fim else None,
            "base_tarifaria":  "Tarifa de Aplicação",
            "tarifas":         tarifas,
            "total_registros": len(tarifas),
        }

    return resultado


# ─── MAIN ─────────────────────────────────────────────────────────────────────

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
║  Extrator ANEEL — Playwright (download automático)           ║
╚══════════════════════════════════════════════════════════════╝
""")

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
            accept_downloads=True,
        )
        page = await ctx.new_page()

        print(f"📡  Acessando portal ANEEL...")
        await page.goto(PORTAL_URL, wait_until="domcontentloaded", timeout=60_000)

        await aplicar_filtros(page, debug=args.debug)
        excel_bytes = await baixar_excel(page, debug=args.debug)
        await browser.close()

    if not excel_bytes:
        print("❌  Falha no download — abortando")
        sys.exit(1)

    print("\n🔄  Processando Excel...")
    resultado = processar_excel(excel_bytes, debug=args.debug)

    envelope = {
        "gerado_em":            datetime.now(timezone.utc).isoformat(),
        "fonte":                "Portal Luz na Tarifa — ANEEL (download automático)",
        "url_fonte":            PORTAL_URL,
        "filtros": {
            "tipo_outorga":     "Concessionária",
            "base_tarifaria":   "Tarifa de Aplicação",
            "reh":              "Mais recente por distribuidora",
        },
        "total_distribuidoras": len(resultado),
        "distribuidoras":       resultado,
    }

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(envelope, f, ensure_ascii=False, indent=2, default=str)

    print(f"\n✅  {len(resultado)} distribuidoras → {out_path}")

if __name__ == "__main__":
    asyncio.run(main())
