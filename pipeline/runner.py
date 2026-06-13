"""
Pipeline Runner — Modül tabanlı orkestratör
--------------------------------------------
Kullanım:
  python -m pipeline.runner                                     # tüm modülleri çalıştır
  python -m pipeline.runner --module 01                         # sadece Gıda modülü
  python -m pipeline.runner --module 01,07                      # Gıda + Yakıt
  python -m pipeline.runner --module 07 --part sehirlerarasi_otobus   # tek part
  python -m pipeline.runner --module 07 --part yolcu_tasima,sehirlerarasi_otobus
  python -m pipeline.runner --dry-run                           # DB'ye yazmadan test
  python -m pipeline.runner --setup-schema                      # DB tablolarını oluştur
  python -m pipeline.runner --discover-branches                 # Gıda modülü şube keşfi
  python -m pipeline.runner --health-check                      # Sağlık raporu (bugün)
  python -m pipeline.runner --health-check --date 2026-04-09   # Belirli tarih
  python -m pipeline.runner --resume                            # Bugün yarım kalan yerden devam
"""

import argparse
import asyncio
import json
import logging
import os
import threading
import time
from datetime import date

import psutil
from dotenv import load_dotenv

load_dotenv()

from db.repository import get_connection
from modules import get_modules
from modules.m01_food import FoodModule
from modules.m05_household import HouseholdModule
from modules.m07_fuel import FuelModule
from modules.m13_kisisel_bakim import KisiselBakimModule

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

_LOCK_FILE = os.path.join("logs", "pipeline.pid")


def _checkpoint_path() -> str:
    return os.path.join("logs", f"pipeline_checkpoint_{date.today()}.json")


def _load_checkpoint() -> set[str]:
    path = _checkpoint_path()
    if not os.path.exists(path):
        return set()
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return set(data.get("completed", []))
    except (OSError, json.JSONDecodeError):
        return set()


def _mark_checkpoint(code: str, started_at: str) -> None:
    path = _checkpoint_path()
    completed = _load_checkpoint()
    completed.add(code)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"completed": sorted(completed), "started_at": started_at}, f, ensure_ascii=False)


def _clear_checkpoint() -> None:
    path = _checkpoint_path()
    try:
        os.remove(path)
    except OSError:
        pass


def _acquire_lock() -> bool:
    """
    Eş zamanlı pipeline çalışmasını önler.
    True → kilit alındı, False → zaten çalışıyor.
    logs/pipeline.pid dosyasına PID yazar.
    """
    if os.path.exists(_LOCK_FILE):
        try:
            with open(_LOCK_FILE) as f:
                old_pid = int(f.read().strip())
            if psutil.pid_exists(old_pid):
                logger.warning(
                    "[runner] Zaten calisıyor (PID %d) — bu instance durduruluyor", old_pid
                )
                return False
            # Eski PID ölmüş → stale lock, sil ve devam et
            logger.info("[runner] Stale lock temizlendi (PID %d artık yok)", old_pid)
        except (ValueError, OSError):
            pass
    with open(_LOCK_FILE, "w") as f:
        f.write(str(os.getpid()))
    return True


def _release_lock() -> None:
    try:
        os.remove(_LOCK_FILE)
    except OSError:
        pass


# 2026-06-07: pasabahce scraper'ında çöken bir Chromium, asyncio event loop'u
# tamamen bloke etti — modül seviyesindeki asyncio.wait_for(...) bile tetiklenmedi
# (zamanlayıcısı da aynı donmuş loop'a bağlı). Süreç ~2.5 saat askıda kaldı.
# Bu watchdog ayrı bir OS thread'inde çalışır; event loop donsa bile log dosyasının
# yazılmadığını fark edip süreci zorla sonlandırır. Eşik, M01'in şehirler arası
# kasıtlı 10 dakikalık beklemesinden (config: "10 dakika bekleniyor…") belirgin
# şekilde yüksek tutuldu — yanlış pozitif olmasın diye.
_WATCHDOG_TIMEOUT = 1200  # saniye — 20 dakika sessizlik = donmuş kabul et
_WATCHDOG_CHECK_INTERVAL = 60


def _start_watchdog(log_path: str, stop_event: threading.Event) -> threading.Thread:
    """Log dosyasının son yazılma zamanını izler; uzun süre sessizlik
    pipeline'ın donduğunu gösterir ve süreç os._exit ile sonlandırılır.
    Lock dosyası burada da temizlenir — ölü PID zaten stale lock olarak
    bir sonraki çalışmada da temizlenirdi, ama erken temizlemek daha iyi."""

    def _watch() -> None:
        while not stop_event.wait(_WATCHDOG_CHECK_INTERVAL):
            try:
                last_write = os.path.getmtime(log_path)
            except OSError:
                continue
            silent_for = time.time() - last_write
            if silent_for > _WATCHDOG_TIMEOUT:
                logger.critical(
                    "[watchdog] %.0f saniyedir log akışı yok — pipeline donmuş olabilir, "
                    "süreç zorla sonlandırılıyor (PID %d)",
                    silent_for, os.getpid(),
                )
                _release_lock()
                os._exit(1)

    thread = threading.Thread(target=_watch, name="pipeline-watchdog", daemon=True)
    thread.start()
    return thread


# 2026-06-08: Pipeline 11:00'de log yazmayı kesti, sistem 11:14'te
# "System Idle" nedeniyle uykuya daldı (Kernel-Power #42/#107/#131, gerçek
# uyanış ~12:05) — ~50 dakikalık S3 uykusunda Python süreci ve Playwright/
# Chromium alt süreçleri sessizce öldü (traceback yok). Task Scheduler'daki
# WakeToRun yalnızca görevi BAŞLATMAK için uyandırmayı kapsar, ÇALIŞIRKEN
# uykuyu engellemez. Bu yüzden pipeline kendi süresince Windows'a "uyuma"
# diyor — global güç planı ayarlarına dokunmadan (onlar güncellemelerde
# sıfırlanabiliyor).
_ES_CONTINUOUS = 0x80000000
_ES_SYSTEM_REQUIRED = 0x00000001
_ES_AWAYMODE_REQUIRED = 0x00000040


def _prevent_sleep() -> None:
    if os.name != "nt":
        return
    try:
        import ctypes
        ctypes.windll.kernel32.SetThreadExecutionState(
            _ES_CONTINUOUS | _ES_SYSTEM_REQUIRED | _ES_AWAYMODE_REQUIRED
        )
        logger.info("[runner] Uyku engellendi (SetThreadExecutionState) — pipeline süresince sistem uykuya geçmeyecek")
    except Exception as exc:
        logger.warning("[runner] SetThreadExecutionState başarısız — sistem uykuya geçebilir: %s", exc)


def _allow_sleep() -> None:
    if os.name != "nt":
        return
    try:
        import ctypes
        ctypes.windll.kernel32.SetThreadExecutionState(_ES_CONTINUOUS)
    except Exception:
        pass


def _print_safe(text: str) -> None:
    """Windows'ta Unicode print sorununu önler — stdout.buffer üzerinden UTF-8 yazar."""
    import sys
    sys.stdout.buffer.write((text + "\n").encode("utf-8", errors="replace"))


async def main(
    module_codes: list[str] | None,
    dry_run: bool,
    setup_schema: bool,
    do_discover: bool,
    do_discover_m05: bool,
    do_discover_saat: bool,
    do_discover_gunes: bool,
    do_discover_valiz_bavul: bool,
    do_discover_okul_cantasi: bool,
    do_discover_kadin_cantasi: bool,
    do_discover_bebek_arabasi: bool,
    do_health_check: bool,
    health_date: date | None,
    parts: list[str] | None = None,
    resume: bool = False,
) -> None:
    if do_discover:
        await FoodModule().discover_branches()
        return

    if do_discover_m05:
        await HouseholdModule().discover()
        return

    if do_discover_saat:
        await KisiselBakimModule().discover_saat_altin()
        return

    if do_discover_gunes:
        await KisiselBakimModule().discover_gunes_gozlugu()
        return

    if do_discover_valiz_bavul:
        await KisiselBakimModule().discover_valiz_bavul()
        return

    if do_discover_okul_cantasi:
        await KisiselBakimModule().discover_okul_cantasi()
        return

    if do_discover_kadin_cantasi:
        await KisiselBakimModule().discover_kadin_cantasi()
        return

    if do_discover_bebek_arabasi:
        await KisiselBakimModule().discover_bebek_arabasi()
        return

    if do_health_check:
        from pipeline.health import format_report, run_health_check, save_report
        from pipeline.notifier import send_health_email
        async with get_connection() as conn:
            report = await run_health_check(conn, health_date)
        _print_safe(format_report(report))
        save_report(report)
        send_health_email(report)
        return

    # Eş zamanlı çalışmayı önle (Task Scheduler çift tetikleme vs.)
    if not _acquire_lock():
        return

    _prevent_sleep()
    log_path = os.path.join("logs", f"{date.today()}.log")
    watchdog_stop = threading.Event()
    _start_watchdog(log_path, watchdog_stop)
    try:
        await _run_modules(module_codes, dry_run, setup_schema, parts, resume=resume)
    finally:
        watchdog_stop.set()
        _release_lock()
        _allow_sleep()


async def _run_modules(
    module_codes: list[str] | None,
    dry_run: bool,
    setup_schema: bool,
    parts: list[str] | None = None,
    resume: bool = False,
) -> None:
    # branches.yaml boşsa uyar
    _branches_path = os.path.join("config", "branches.yaml")
    if os.path.exists(_branches_path):
        import yaml as _yaml
        with open(_branches_path, encoding="utf-8") as _f:
            _b = _yaml.safe_load(_f)
        if not _b:
            logger.warning(
                "[runner] config/branches.yaml bos — proximity modunda calisacak. "
                "Sabit sube listesi icin: python -m pipeline.runner --discover-branches"
            )

    modules = get_modules(module_codes)

    if setup_schema:
        async with get_connection() as conn:
            for mod in modules:
                logger.info("[runner] %s şeması uygulanıyor...", mod.name)
                await mod.setup_schema(conn)
        logger.info("[runner] Tüm şemalar uygulandı.")
        return

    completed = _load_checkpoint() if resume else set()
    if resume and completed:
        logger.info("[runner] Checkpoint bulundu — tamamlanan modüller atlanıyor: %s", sorted(completed))

    started_at = date.today().isoformat()

    for mod in modules:
        if mod.coicop_code in completed:
            logger.info("[runner] Modül %s zaten tamamlanmış, atlanıyor.", mod.coicop_code)
            continue

        logger.info(
            "[runner] Modül %s başlıyor: %s (ağırlık: %.2f%%)",
            mod.coicop_code, mod.name, mod.weight,
        )
        runs = await mod.run(dry_run=dry_run, parts=parts)
        success = sum(1 for r in runs if r.status == "success")
        failed  = sum(1 for r in runs if r.status == "failed")
        logger.info(
            "[runner] Modül %s tamamlandı — %d başarılı, %d başarısız",
            mod.coicop_code, success, failed,
        )
        if not dry_run:
            _mark_checkpoint(mod.coicop_code, started_at)

    # Dry-run değilse otomatik sağlık raporu bas ve mail gönder
    if not dry_run:
        _clear_checkpoint()
        try:
            from pipeline.health import format_report, run_health_check, save_report
            from pipeline.notifier import send_health_email
            async with get_connection() as conn:
                report = await run_health_check(conn)
            _print_safe(format_report(report))
            save_report(report)
            send_health_email(report)
        except Exception as exc:
            logger.warning("[runner] Sağlık raporu oluşturulamadı: %s", exc)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Enflasyon veritabanı pipeline")
    parser.add_argument(
        "--module",
        default=None,
        help="Virgülle ayrılmış COICOP modül kodları (örn: 01,07). Varsayılan: tüm modüller.",
    )
    parser.add_argument("--dry-run", action="store_true", help="DB'ye yazma, sadece ekrana bas")
    parser.add_argument(
        "--part",
        default=None,
        help="Virgülle ayrılmış part slug'ları (örn: sehirlerarasi_otobus,yolcu_tasima). "
             "Varsayılan: tüm part'lar. Yalnızca --module ile birlikte kullanılır.",
    )
    parser.add_argument("--setup-schema", action="store_true", help="DB tablolarını oluştur")
    parser.add_argument(
        "--discover-branches",
        action="store_true",
        help="Gıda modülü için şube keşfi (config/branches.yaml oluşturur)",
    )
    parser.add_argument(
        "--discover-m05",
        action="store_true",
        help="Modül 05 SKU keşfi (tracked.yaml tracked_skus doldurur)",
    )
    parser.add_argument(
        "--discover-saat",
        action="store_true",
        help="Modül 13 saat_altin keşfi (saatvesaat.com.tr + Trendyol, saat_altin.yaml günceller)",
    )
    parser.add_argument(
        "--discover-gunes",
        action="store_true",
        help="Modül 13 güneş gözlüğü keşfi (Trendyol, seyahat_bebek.yaml günceller)",
    )
    parser.add_argument(
        "--discover-valiz-bavul",
        action="store_true",
        help="Modül 13 valiz & bavul keşfi (5 marka × 5 ürün, seyahat_bebek.yaml günceller)",
    )
    parser.add_argument(
        "--discover-okul-cantasi",
        action="store_true",
        help="Modül 13 okul çantası keşfi (5 marka × 5 ürün, seyahat_bebek.yaml günceller)",
    )
    parser.add_argument(
        "--discover-kadin-cantasi",
        action="store_true",
        help="Modül 13 kadın çantası keşfi (5 marka × 5 ürün, seyahat_bebek.yaml günceller)",
    )
    parser.add_argument(
        "--discover-bebek-arabasi",
        action="store_true",
        help="Modül 13 bebek arabası keşfi (Trendyol, seyahat_bebek.yaml günceller)",
    )
    parser.add_argument(
        "--health-check",
        action="store_true",
        help="Sağlık raporu — DB verisi bütünlük ve anomali kontrolü",
    )
    parser.add_argument(
        "--date",
        default=None,
        help="Sağlık raporu için tarih (YYYY-MM-DD). Varsayılan: bugün.",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Bugün yarım kalan pipeline'ı checkpoint'ten devam ettir.",
    )
    args = parser.parse_args()

    codes = [c.strip() for c in args.module.split(",")] if args.module else None
    hdate = date.fromisoformat(args.date) if args.date else None
    parts = [p.strip() for p in args.part.split(",")] if args.part else None

    asyncio.run(main(
        module_codes              = codes,
        dry_run                   = args.dry_run,
        setup_schema              = args.setup_schema,
        do_discover               = args.discover_branches,
        do_discover_m05           = args.discover_m05,
        do_discover_saat          = args.discover_saat,
        do_discover_gunes         = args.discover_gunes,
        do_discover_valiz_bavul   = args.discover_valiz_bavul,
        do_discover_okul_cantasi  = args.discover_okul_cantasi,
        do_discover_kadin_cantasi = args.discover_kadin_cantasi,
        do_discover_bebek_arabasi = args.discover_bebek_arabasi,
        do_health_check           = args.health_check,
        health_date               = hdate,
        parts                     = parts,
        resume                    = args.resume,
    ))
