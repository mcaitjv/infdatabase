"""
THY Probe v2 — Dogrudan arama sonuc sayfasina git, API yakala.

thy.com/turkishairlines.com search URL parametreleri:
  departurePort, arrivalPort, departureDate, cabinInd, tripType, passengerCount
"""
import asyncio
import json
from datetime import date, timedelta
from pathlib import Path

OUT = Path(__file__).parent

ORIGIN = "IST"
DEST   = "AYT"
DEP_DATE = (date.today() + timedelta(days=7)).strftime("%Y-%m-%d")

# THY direct search URL (Turkish)
SEARCH_URL = (
    "https://www.turkishairlines.com/tr-tr/rezervasyon/ucus-arama/"
    f"?activeTab=FLIGHTONLY"
    f"&departurePort={ORIGIN}"
    f"&arrivalPort={DEST}"
    f"&departureDate={DEP_DATE}"
    f"&cabinInd=Y"              # Y = Economy
    f"&tripType=O"              # O = OneWay
    f"&numberOfEconomyClassSeats=1"
    f"&passengerCount=1"
    f"&flightSearchType=S"
)

THY_DOMAINS  = ("turkishairlines.com", "thy.com")
IGNORED_EXTS = (".js", ".css", ".png", ".svg", ".gif", ".woff", ".ico", ".jpg", ".webp", ".woff2")


async def probe():
    from playwright.async_api import async_playwright

    captures = []

    async def on_response(res):
        url = res.url
        if not any(d in url for d in THY_DOMAINS):
            return
        if any(url.endswith(e) for e in IGNORED_EXTS):
            return
        if any(k in url for k in ["_next/static", "remoteEntry", "akam", "quantummetric", "rb_b2"]):
            return
        try:
            ct = res.headers.get("content-type", "")
            body = await res.json() if "json" in ct else None
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
            headless=True,
            args=["--disable-blink-features=AutomationControlled"],
        )
        ctx = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
            viewport={"width": 1400, "height": 900},
            extra_http_headers={"Accept-Language": "tr-TR,tr;q=0.9"},
        )
        page = await ctx.new_page()
        page.on("response", on_response)

        print(f"[1] Arama sayfasi yukleniyor...")
        print(f"    {SEARCH_URL}")
        try:
            await page.goto(SEARCH_URL, wait_until="domcontentloaded", timeout=45000)
        except Exception as exc:
            print(f"  Hata: {exc}")

        print("[2] 20s bekleniyor (sonuclar yukleniyor)...")
        await page.wait_for_timeout(20000)
        await page.screenshot(path=str(OUT / "thy2_results.png"))
        print(f"  Baslik: {await page.title()}")
        print(f"  URL: {page.url}")

        # DOM fiyat dene
        dom = await page.evaluate("""
            () => {
                const results = [];
                const sels = [
                    '[class*="price"]', '[class*="fare"]', '[class*="amount"]',
                    '[data-price]', '[class*="ticket"]', '[class*="flight-price"]',
                ];
                for (const sel of sels) {
                    document.querySelectorAll(sel).forEach(el => {
                        const t = el.innerText.trim();
                        if (/[0-9]/.test(t) && t.length < 50 && t.length > 2) {
                            results.push({ sel: sel.slice(0,30), text: t.slice(0,40) });
                        }
                    });
                }
                return results.slice(0, 20);
            }
        """)
        print(f"[3] DOM fiyatlar: {dom}")

        # Ozet
        print(f"\n{'='*60}")
        for c in captures:
            url = c.get("url", "")
            status = c.get("status", "?")
            method = c.get("method", "?")
            ct = c.get("content_type", "")
            print(f"  [{method}] {status} {url[:100]}")
            body = c.get("body")
            if isinstance(body, (dict, list)):
                bs = str(body)
                if any(k in bs.lower() for k in ["price", "fare", "flight", "offer", "ucus", "availability"]):
                    print(f"    >>> UCUS: {bs[:300]}")
                    print(f"    keys: {list(body.keys()) if isinstance(body, dict) else 'list'}")

        # Sadece kritik captures'i kaydet (boyut siniri)
        critical = [c for c in captures if isinstance(c.get("body"), (dict, list))]
        out_path = OUT / "thy_probe2_captures.json"
        out_path.write_text(
            json.dumps(critical, ensure_ascii=False, indent=2)[:500000],
            encoding="utf-8",
        )
        print(f"\n{len(captures)} toplam, {len(critical)} JSON yaniti. Kaydedildi: {out_path}")

        await browser.close()


if __name__ == "__main__":
    asyncio.run(probe())
