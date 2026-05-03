"""
THY (Turkish Airlines) — thy.com ucus arama probe
IST -> AYT, +7 gun, Economy

Hedef: Hangi API endpoint'i cagriliyor, response yapisi ne?
Cikti: data/thy_probe_captures.json + ekran goruntuleri
"""
import asyncio
import json
from datetime import date, timedelta
from pathlib import Path

OUT = Path(__file__).parent

ORIGIN = "IST"
DEST   = "AYT"
DATE_DISPLAY = (date.today() + timedelta(days=7)).strftime("%d/%m/%Y")

THY_DOMAINS  = ("thy.com", "tkcloudapi.com", "turkishairlines.com")
IGNORED_EXTS = (".js", ".css", ".png", ".svg", ".gif", ".woff", ".ico", ".jpg", ".webp")

# JavaScript fonksiyonlari — f-string DEGIL, arguman olarak geciriliyor
JS_CLOSE_COOKIE = """
() => {
    const labels = ['Kabul Et','Tamam','Kabul','OK','Accept','Kapat','I Accept','I agree'];
    for (const btn of document.querySelectorAll('button')) {
        if (labels.includes(btn.innerText.trim()) && btn.offsetParent) {
            btn.click(); return btn.innerText.trim();
        }
    }
    return null;
}
"""

JS_CLICK_ORIGIN = """
(origin) => {
    const selectors = [
        'input[placeholder*="Kalkis"], input[placeholder*="Nereden"], input[placeholder*="From"]',
        'input[id*="origin"], input[name*="origin"], input[id*="from"]',
        'input[data-testid*="origin"], input[aria-label*="Kalkis"]',
        '.search-input input, [class*="departure"] input, [class*="origin"] input',
    ];
    for (const sel of selectors) {
        const el = document.querySelector(sel);
        if (el && el.offsetParent !== null) {
            el.value = '';
            el.click();
            return sel;
        }
    }
    for (const inp of document.querySelectorAll('input[type="text"], input:not([type])')) {
        const ph = (inp.placeholder || '').toLowerCase();
        if (ph.includes('nereden') || ph.includes('from') || ph.includes('kalkis') || ph.includes('kalkış')) {
            inp.click(); return 'ph:' + inp.placeholder;
        }
    }
    return null;
}
"""

JS_SELECT_AIRPORT = """
({code, city}) => {
    const items = document.querySelectorAll(
        'li[class*="airport"], li[class*="suggestion"], [class*="dropdown"] li, [role="option"], [class*="option"]'
    );
    for (const item of items) {
        const t = item.innerText || '';
        if (t.includes(code) || t.toLowerCase().includes(city.toLowerCase())) {
            item.click(); return t.slice(0, 60);
        }
    }
    return null;
}
"""

JS_CLICK_DEST = """
() => {
    const selectors = [
        'input[placeholder*="Nereye"], input[placeholder*="Varis"], input[placeholder*="To"]',
        'input[id*="destination"], input[name*="destination"], input[id*="to"]',
        '[class*="arrival"] input, [class*="destination"] input',
    ];
    for (const sel of selectors) {
        const el = document.querySelector(sel);
        if (el && el.offsetParent !== null) {
            el.value = '';
            el.click();
            return sel;
        }
    }
    return null;
}
"""

JS_CLICK_DATE = """
() => {
    const selectors = [
        'input[placeholder*="Tarih"], input[placeholder*="Date"], input[type="date"]',
        'input[id*="date"], input[name*="date"]',
        '[class*="departure-date"] input, [class*="depart"] input',
    ];
    for (const sel of selectors) {
        const el = document.querySelector(sel);
        if (el && el.offsetParent !== null) { el.click(); return sel; }
    }
    return null;
}
"""

JS_CLICK_SEARCH = """
() => {
    const labels = ['Ara', 'Search', 'Ucus Ara', 'Ucus Bul', 'Bilet Ara'];
    for (const btn of document.querySelectorAll('button, [role="button"]')) {
        const t = btn.innerText.trim();
        if (labels.some(l => t.toLowerCase().includes(l.toLowerCase())) && btn.offsetParent) {
            btn.click(); return 'text:' + t;
        }
    }
    const submit = document.querySelector('button[type="submit"], input[type="submit"]');
    if (submit && submit.offsetParent) { submit.click(); return 'submit'; }
    return null;
}
"""

JS_EXTRACT_PRICES = """
() => {
    const results = [];
    document.querySelectorAll('[class*="price"], [class*="fare"], [class*="amount"], [data-price]').forEach(el => {
        const text = el.innerText.trim();
        if (/[0-9]/.test(text) && text.length < 40) {
            results.push({ cls: el.className.slice(0, 40), text });
        }
    });
    return results.slice(0, 20);
}
"""


async def probe():
    from playwright.async_api import async_playwright

    captures = []

    async def on_response(res):
        url = res.url
        if not any(d in url for d in THY_DOMAINS):
            return
        if any(url.endswith(e) for e in IGNORED_EXTS):
            return
        try:
            ct = res.headers.get("content-type", "")
            body = await res.json() if "json" in ct else (await res.text())[:500]
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

        # 1 — Anasayfa
        print("[1] thy.com yukleniyor...")
        try:
            await page.goto("https://www.thy.com/tr-TR", wait_until="domcontentloaded", timeout=40000)
        except Exception:
            await page.goto("https://www.thy.com", wait_until="domcontentloaded", timeout=40000)
        await page.wait_for_timeout(3000)
        await page.screenshot(path=str(OUT / "thy_step1_homepage.png"))
        print(f"  Baslik: {await page.title()}")

        # 2 — Cerez
        r = await page.evaluate(JS_CLOSE_COOKIE)
        if r:
            print(f"  Cookie kapat: {r}")
            await page.wait_for_timeout(1000)

        # 3 — Nereden
        print(f"[2] Nereden: {ORIGIN}")
        r = await page.evaluate(JS_CLICK_ORIGIN, ORIGIN)
        print(f"  Selector: {r}")
        if r:
            await page.wait_for_timeout(400)
            await page.keyboard.type(ORIGIN, delay=80)
            await page.wait_for_timeout(2000)
            await page.screenshot(path=str(OUT / "thy_step2_origin.png"))
            r2 = await page.evaluate(JS_SELECT_AIRPORT, {"code": ORIGIN, "city": "Istanbul"})
            if r2:
                print(f"  Secildi: {r2}")
            else:
                await page.keyboard.press("ArrowDown")
                await page.wait_for_timeout(300)
                await page.keyboard.press("Enter")
                print("  ArrowDown+Enter fallback")
            await page.wait_for_timeout(700)

        # 4 — Nereye
        print(f"[3] Nereye: {DEST}")
        r = await page.evaluate(JS_CLICK_DEST)
        print(f"  Selector: {r}")
        if r:
            await page.wait_for_timeout(400)
            await page.keyboard.type(DEST, delay=80)
            await page.wait_for_timeout(2000)
            await page.screenshot(path=str(OUT / "thy_step3_dest.png"))
            r2 = await page.evaluate(JS_SELECT_AIRPORT, {"code": DEST, "city": "Antalya"})
            if r2:
                print(f"  Secildi: {r2}")
            else:
                await page.keyboard.press("ArrowDown")
                await page.wait_for_timeout(300)
                await page.keyboard.press("Enter")
            await page.wait_for_timeout(700)

        # 5 — Tarih
        print(f"[4] Tarih: {DATE_DISPLAY}")
        r = await page.evaluate(JS_CLICK_DATE)
        print(f"  Selector: {r}")
        if r:
            await page.wait_for_timeout(400)
            await page.keyboard.type(DATE_DISPLAY, delay=50)
            await page.wait_for_timeout(800)
            await page.keyboard.press("Escape")
            await page.wait_for_timeout(500)

        # 6 — Ara
        print("[5] Arama...")
        await page.screenshot(path=str(OUT / "thy_step4_before_search.png"))
        r = await page.evaluate(JS_CLICK_SEARCH)
        print(f"  Butonu: {r}")
        print("[6] 15s bekleniyor...")
        await page.wait_for_timeout(15000)
        await page.screenshot(path=str(OUT / "thy_step5_results.png"))

        # 7 — DOM
        dom = await page.evaluate(JS_EXTRACT_PRICES)
        print(f"[7] DOM fiyatlar: {dom[:5]}")

        # Ozet
        print(f"\n{'='*60}")
        print(f"Yakalanan: {len(captures)}")
        for c in captures:
            print(f"  [{c.get('method','?')}] {c.get('status','?')} {c.get('url','')[:90]}")
            body = c.get("body")
            if isinstance(body, (dict, list)):
                bs = str(body).lower()
                if any(k in bs for k in ["price", "fare", "flight", "ucus", "offer"]):
                    print(f"    >>> UCUS VERISI: {str(body)[:200]}")

        out_path = OUT / "thy_probe_captures.json"
        out_path.write_text(
            json.dumps(captures, ensure_ascii=False, indent=2)[:200000],
            encoding="utf-8",
        )
        print(f"\nKaydedildi: {out_path}")
        await browser.close()


if __name__ == "__main__":
    asyncio.run(probe())
