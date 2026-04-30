"""
Pegasus Airlines — flypgs.com ucus arama probe
IST -> AYT, +7 gun, Economy

Strateji 1: Dogrudan search URL ile git
Strateji 2: Ana sayfadan form doldur
"""
import asyncio
import json
from datetime import date, timedelta
from pathlib import Path

OUT = Path(__file__).parent

ORIGIN   = "IST"
DEST     = "AYT"
DEP_DATE = (date.today() + timedelta(days=7))
DATE_URL = DEP_DATE.strftime("%d.%m.%Y")   # 07.05.2026

PEGASUS_DOMAINS = ("flypgs.com", "pegasusair.com")
IGNORED = (".js", ".css", ".png", ".svg", ".gif", ".woff", ".ico", ".jpg", ".webp", ".woff2")


async def probe():
    from playwright.async_api import async_playwright

    captures = []

    async def on_response(res):
        url = res.url
        if not any(d in url for d in PEGASUS_DOMAINS):
            return
        if any(url.endswith(e) for e in IGNORED):
            return
        try:
            ct = res.headers.get("content-type", "")
            body = await res.json() if "json" in ct else (await res.text())[:800]
            req_body = None
            try:
                req_body = res.request.post_data
            except Exception:
                pass
            captures.append({
                "method": res.request.method,
                "url": url,
                "status": res.status,
                "content_type": ct,
                "req_body": req_body,
                "body": body,
            })
        except Exception as exc:
            captures.append({"url": url, "status": res.status, "error": str(exc)})

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=False,
            args=["--disable-blink-features=AutomationControlled"],
        )
        ctx = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
            viewport={"width": 1400, "height": 900},
            extra_http_headers={"Accept-Language": "tr-TR,tr;q=0.9"},
        )
        page = await ctx.new_page()
        page.on("response", on_response)

        # ── Strateji 1: Dogrudan URL ────────────────────────────────────────
        search_url = (
            f"https://www.flypgs.com/ucak-bileti/{ORIGIN.lower()}-{DEST.lower()}"
            f"?departureDate={DATE_URL}&adult=1&child=0&infant=0&flightType=single"
        )
        print(f"[1] Dogrudan URL deneniyor: {search_url}")
        try:
            await page.goto(search_url, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(12000)
            await page.screenshot(path=str(OUT / "pegasus_direct_results.png"))
            print(f"  URL: {page.url}")
            print(f"  Baslik: {await page.title()}")
        except Exception as exc:
            print(f"  Hata: {exc}")

        # ── Strateji 2: Ana sayfa form ──────────────────────────────────────
        if len(captures) == 0 or not any("flight" in c.get("url","").lower() or "ucak" in c.get("url","").lower() for c in captures):
            print("[2] Ana sayfa deneniyor...")
            await page.goto("https://www.flypgs.com/tr", wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(3000)
            await page.screenshot(path=str(OUT / "pegasus_home.png"))

            # Cookie kapat
            for label in ["Kabul Et", "Tamam", "Kabul", "OK", "Accept"]:
                try:
                    btn = page.get_by_role("button", name=label)
                    if await btn.count() > 0:
                        await btn.first.click()
                        print(f"  Cookie: {label}")
                        await page.wait_for_timeout(800)
                        break
                except Exception:
                    pass

            # Nereden
            print(f"[3] Nereden: {ORIGIN}")
            for sel in [
                'input[placeholder*="Nereden"]', 'input[placeholder*="Kalkış"]',
                'input[id*="origin"]', '[class*="departure"] input', 'input[name*="origin"]',
            ]:
                try:
                    el = page.locator(sel).first
                    if await el.count() > 0:
                        await el.click()
                        await page.wait_for_timeout(400)
                        await page.keyboard.type(ORIGIN, delay=80)
                        await page.wait_for_timeout(2000)
                        print(f"  Nereden bulundu: {sel}")

                        # Dropdown
                        for item_sel in ['[role="option"]', '[class*="suggestion"] li', 'li[class*="airport"]']:
                            items = page.locator(item_sel)
                            count = await items.count()
                            for i in range(min(count, 5)):
                                text = await items.nth(i).inner_text()
                                if ORIGIN in text or "İstanbul" in text or "Istanbul" in text:
                                    await items.nth(i).click()
                                    print(f"  Seçildi: {text.strip()[:50]}")
                                    break
                        await page.wait_for_timeout(700)
                        break
                except Exception:
                    continue

            # Nereye
            print(f"[4] Nereye: {DEST}")
            for sel in [
                'input[placeholder*="Nereye"]', 'input[placeholder*="Varış"]',
                'input[id*="destination"]', '[class*="arrival"] input',
            ]:
                try:
                    el = page.locator(sel).first
                    if await el.count() > 0:
                        await el.click()
                        await page.wait_for_timeout(400)
                        await page.keyboard.type(DEST, delay=80)
                        await page.wait_for_timeout(2000)
                        print(f"  Nereye bulundu: {sel}")

                        for item_sel in ['[role="option"]', '[class*="suggestion"] li', 'li[class*="airport"]']:
                            items = page.locator(item_sel)
                            count = await items.count()
                            for i in range(min(count, 5)):
                                text = await items.nth(i).inner_text()
                                if DEST in text or "Antalya" in text:
                                    await items.nth(i).click()
                                    print(f"  Seçildi: {text.strip()[:50]}")
                                    break
                        await page.wait_for_timeout(700)
                        break
                except Exception:
                    continue

            await page.screenshot(path=str(OUT / "pegasus_form_filled.png"))

            # Ara
            print("[5] Arama...")
            for label in ["Ara", "Uçuş Ara", "Search", "Uçuş Bul"]:
                try:
                    btn = page.get_by_role("button", name=label)
                    if await btn.count() > 0:
                        await btn.first.click()
                        print(f"  Tiklandı: {label}")
                        break
                except Exception:
                    pass

            print("[6] 15s bekleniyor...")
            await page.wait_for_timeout(15000)
            await page.screenshot(path=str(OUT / "pegasus_results.png"))

        # ── Ozet ──────────────────────────────────────────────────────────────
        print(f"\n{'='*60}")
        print(f"Yakalanan: {len(captures)}")
        for c in captures:
            url = c.get("url","")
            print(f"  [{c.get('method','?')}] {c.get('status','?')} {url[:100]}")
            body = c.get("body")
            if isinstance(body, (dict, list)):
                bs = str(body).lower()
                if any(k in bs for k in ["price", "fare", "flight", "offer", "pax", "seat"]):
                    print(f"    >>> UCUS VERISI: {str(body)[:300]}")
                    print(f"    keys: {list(body.keys()) if isinstance(body, dict) else type(body)}")

        # Kaydet
        out_path = OUT / "pegasus_probe_captures.json"
        out_path.write_text(
            json.dumps(captures, ensure_ascii=False, indent=2)[:300000],
            encoding="utf-8",
        )
        print(f"\nKaydedildi: {out_path}")
        await browser.close()


if __name__ == "__main__":
    asyncio.run(probe())
