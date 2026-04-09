"""
Pipeline Runner — Modül tabanlı orkestratör
--------------------------------------------
Kullanım:
  python -m pipeline.runner                          # tüm modülleri çalıştır
  python -m pipeline.runner --module 01              # sadece Gıda modülü
  python -m pipeline.runner --module 01,07           # Gıda + Yakıt
  python -m pipeline.runner --dry-run                # DB'ye yazmadan test
  python -m pipeline.runner --setup-schema           # DB tablolarını oluştur (ilk kurulumda)
  python -m pipeline.runner --discover-branches      # Gıda modülü şube keşfi
"""

import argparse
import asyncio
import logging
import os
from datetime import date

from dotenv import load_dotenv

load_dotenv()

from db.repository import get_connection
from modules import get_modules
from modules.m01_food import FoodModule
from modules.m05_household import HouseholdModule

os.makedirs("logs", exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(
            os.path.join("logs", f"{date.today()}.log"),
            encoding="utf-8",
        ),
    ],
)
logger = logging.getLogger(__name__)


async def main(
    module_codes: list[str] | None,
    dry_run: bool,
    setup_schema: bool,
    do_discover: bool,
    do_discover_appliances: bool,
) -> None:
    if do_discover:
        await FoodModule().discover_branches()
        return

    if do_discover_appliances:
        await HouseholdModule().discover_appliances()
        return

    modules = get_modules(module_codes)

    if setup_schema:
        async with get_connection() as conn:
            for mod in modules:
                logger.info("[runner] %s şeması uygulanıyor...", mod.name)
                await mod.setup_schema(conn)
        logger.info("[runner] Tüm şemalar uygulandı.")
        return

    for mod in modules:
        logger.info(
            "[runner] Modül %s başlıyor: %s (ağırlık: %.2f%%)",
            mod.coicop_code, mod.name, mod.weight,
        )
        runs = await mod.run(dry_run=dry_run)
        success = sum(1 for r in runs if r.status == "success")
        failed  = sum(1 for r in runs if r.status == "failed")
        logger.info(
            "[runner] Modül %s tamamlandı — %d başarılı, %d başarısız",
            mod.coicop_code, success, failed,
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Enflasyon veritabanı pipeline")
    parser.add_argument(
        "--module",
        default=None,
        help="Virgülle ayrılmış COICOP modül kodları (örn: 01,07). Varsayılan: tüm modüller.",
    )
    parser.add_argument("--dry-run", action="store_true", help="DB'ye yazma, sadece ekrana bas")
    parser.add_argument("--setup-schema", action="store_true", help="DB tablolarını oluştur")
    parser.add_argument(
        "--discover-branches",
        action="store_true",
        help="Gıda modülü için şube keşfi (config/branches.yaml oluşturur)",
    )
    parser.add_argument(
        "--discover-appliances",
        action="store_true",
        help="Modül 05 beyaz eşya SKU keşfi (appliances.yaml tracked_skus doldurur)",
    )
    args = parser.parse_args()

    codes = [c.strip() for c in args.module.split(",")] if args.module else None

    asyncio.run(main(
        module_codes           = codes,
        dry_run                = args.dry_run,
        setup_schema           = args.setup_schema,
        do_discover            = args.discover_branches,
        do_discover_appliances = args.discover_appliances,
    ))
