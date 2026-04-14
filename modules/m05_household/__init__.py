"""
Modül 05 — Mobilya, Mefruşat ve Ev Bakım  (COICOP 05, %7.92)

Tip B: Discovery + Tracked
  - Discovery fazı bir kez çalışır, seçilen SKU'lar YAML'a yazılır
  - Sonraki günler sabit sepet olarak fiyatlanır
  - Self-heal: eksik SKU'lar günlük run'da otomatik yenilenir

Kullanım:
  python -m pipeline.runner --discover-items   # SKU keşfi
  python -m pipeline.runner --module 05         # Günlük fiyat takibi
  python -m pipeline.runner --module 05 --dry-run
"""

import logging
import os
from datetime import date, datetime

import yaml

from db.models import ScrapeRun
from db.repository import get_connection, upsert_scrape_run
from modules.base import BaseModule

logger = logging.getLogger(__name__)
_MODULE_DIR = os.path.dirname(__file__)


# ── YAML yükleme / yazma ──────────────────────────────────────────────────────

def _load_items() -> list[dict]:
    path = os.path.join(_MODULE_DIR, "config", "items.yaml")
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f).get("items", [])


def _write_items_yaml(entries: list[dict]) -> None:
    path = os.path.join(_MODULE_DIR, "config", "items.yaml")
    header = (
        "# Modül 05 — Takip listesi\n"
        "# --discover-items ile güncellenir. Eksik SKU'lar otomatik yenilenir.\n\n"
    )
    with open(path, "w", encoding="utf-8") as f:
        f.write(header)
        yaml.dump({"items": entries}, f, allow_unicode=True, default_flow_style=False, sort_keys=False)


# ── Yardımcılar ───────────────────────────────────────────────────────────────

def _validate_record(rec) -> list[str]:
    errors = []
    if rec.price <= 0:
        errors.append(f"Sifir/negatif fiyat: {rec.price}")
    if rec.price > 100_000:
        errors.append(f"Anormal yuksek fiyat: {rec.price}")
    return errors


async def _heal_missing_skus(entry: dict, found_records: list, scraper, default_top_n: int) -> bool:
    """Eksik SKU'ları discovery ile yenileriyle değiştirir. Değişiklik olursa True döner."""
    tracked = entry.get("tracked_skus") or []
    if not tracked:
        return False

    found_set = {str(r.sku) for r in found_records}
    missing_positions = [i for i, s in enumerate(tracked) if str(s["sku"]) not in found_set]
    if not missing_positions:
        return False

    keyword, coicop = entry["keyword"], entry["coicop"]
    try:
        candidates = await scraper.discover_keyword(keyword, coicop, top_n=default_top_n + len(missing_positions))
    except Exception as exc:
        logger.warning("[m05:heal] %s discovery hatasi: %s", keyword, exc)
        return False

    existing_ids = {str(s["sku"]) for s in tracked}
    replacements = [c for c in candidates if str(c["sku"]) not in existing_ids]
    if not replacements:
        logger.warning("[m05:heal] %s: %d eksik SKU ama discovery yeni aday donmedi", keyword, len(missing_positions))
        return False

    replaced = 0
    for pos in missing_positions:
        if replaced >= len(replacements):
            break
        old, new = tracked[pos], replacements[replaced]
        logger.info("[m05:heal] %s | %s (%s) -> %s (%s)",
                    keyword, old.get("brand", "?"), old.get("sku"), new.get("brand", "?"), new.get("sku"))
        tracked[pos] = new
        replaced += 1

    entry["tracked_skus"] = tracked
    return replaced > 0


# ── Modül ─────────────────────────────────────────────────────────────────────

class HouseholdModule(BaseModule):
    coicop_code = "05"
    name = "Mobilya, Mefruşat ve Ev Bakım"
    weight = 7.92

    async def setup_schema(self, conn) -> None:
        # TODO: yeni tablo gerekiyorsa db/schema.sql + schema_sqlite.sql'e DDL ekle
        pass

    # ── Discovery ─────────────────────────────────────────────────────────────

    async def discover_items(self) -> None:
        """
        items.yaml'daki keyword'ler için top-N SKU keşfeder ve tracked_skus günceller.
        Kullanım: python -m pipeline.runner --discover-items
        """
        entries = _load_items()
        # TODO: scraper sınıfını import et ve burada kullan
        # async with MyScraper() as scraper:
        #     for entry in entries:
        #         skus = await scraper.discover_keyword(entry["keyword"], entry["coicop"])
        #         entry["tracked_skus"] = skus
        #         await scraper._sleep(2.0, 5.0)
        _write_items_yaml(entries)
        logger.info("[m05:discover] items.yaml guncellendi — %d keyword, %d SKU",
                    len(entries), sum(len(e.get("tracked_skus", [])) for e in entries))

    # ── Tracked scraping helper ────────────────────────────────────────────────

    async def _run_tracked_source(
        self,
        scraper,
        entries: list[dict],
        dry_run: bool,
        default_top_n: int,
        label: str,
        sleep_range: tuple[float, float] = (3.0, 7.0),
    ) -> tuple[list[ScrapeRun], bool]:
        """Bir kaynak için tracked SKU listesini fiyatlar, self-heal uygular."""
        runs: list[ScrapeRun] = []
        changed = False

        for entry in entries:
            keyword, coicop_code = entry["keyword"], entry["coicop"]
            tracked_skus = entry.get("tracked_skus") or []

            if not tracked_skus:
                logger.warning("[m05:%s] %s icin tracked_skus bos — discover calistirin", label, keyword)
                continue

            run = ScrapeRun(market=f"m05:{label}:{coicop_code}:{keyword}",
                            run_date=date.today(), started_at=datetime.now())
            valid = []
            try:
                records = await scraper.scrape_tracked(keyword=keyword, coicop_code=coicop_code, tracked_skus=tracked_skus)
                error_count = 0
                for rec in records:
                    errs = _validate_record(rec)
                    if errs:
                        logger.warning("[m05:%s] %s / %s gecersiz: %s", label, keyword, rec.sku, "; ".join(errs))
                        error_count += 1
                    else:
                        valid.append(rec)

                run.products_scraped = len(valid)
                run.errors_count = error_count

                if dry_run:
                    logger.info("[m05:%s] Dry-run %s (%s): %d urun", label, keyword, coicop_code, len(valid))
                    for r in valid[:3]:
                        disc = f" -> {r.discounted_price} TL" if r.discounted_price else ""
                        print(f"  [{label} {r.coicop_code}] {r.brand} {r.model[:40]} | {r.price} TL{disc}")
                    if len(valid) > 3:
                        print(f"  ... ve {len(valid) - 3} urun daha")
                else:
                    # TODO: DB upsert fonksiyonunu import et ve kullan
                    # async with get_connection() as conn:
                    #     inserted = await batch_upsert_<tablo>(conn, valid)
                    pass

                run.status = "success" if error_count == 0 else "partial"

            except Exception as exc:
                logger.error("[m05:%s] %s kritik hata: %s", label, keyword, exc, exc_info=True)
                run.status = "failed"
                run.error_details = str(exc)

            run.finished_at = datetime.now()
            if not dry_run:
                async with get_connection() as conn:
                    await upsert_scrape_run(conn, run)
            logger.info("[m05:%s] %s — %s, %.1fs", label, keyword, run.status,
                        (run.finished_at - run.started_at).total_seconds())
            runs.append(run)

            if not dry_run and run.status in ("success", "partial"):
                try:
                    if await _heal_missing_skus(entry, valid, scraper, default_top_n):
                        changed = True
                except Exception as exc:
                    logger.warning("[m05:heal] %s hatasi: %s", keyword, exc)

            await scraper._sleep(*sleep_range)

        return runs, changed

    # ── Ana run ───────────────────────────────────────────────────────────────

    async def run(self, dry_run: bool = False) -> list[ScrapeRun]:
        items = _load_items()
        runs: list[ScrapeRun] = []
        items_changed = False

        logger.info("[m05] %d keyword basliyor", len(items))

        # TODO: scraper sınıfını import et
        # async with MyScraper() as scraper:
        #     new_runs, changed = await self._run_tracked_source(
        #         scraper, items, dry_run, default_top_n=30, label="myscraper")
        #     runs.extend(new_runs)
        #     items_changed |= changed

        if items_changed:
            _write_items_yaml(items)
            logger.info("[m05:heal] items.yaml guncellendi")

        return runs
