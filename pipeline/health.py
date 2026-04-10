"""
pipeline/health.py — Pipeline Sağlık Kontrol Sistemi
------------------------------------------------------
Her günlük çalışma sonrasında (veya bağımsız olarak) veri kalitesini kontrol eder:
  - Beklenen kayıt sayısına ulaşıldı mı?
  - Hangi SKU/lokasyon/kombinasyonlar eksik?
  - Dünden bugüne anormal fiyat değişimi var mı?

Kullanım (runner.py üzerinden):
  python -m pipeline.runner --health-check
  python -m pipeline.runner --health-check --date 2026-04-09
"""

import json
import logging
import os
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

# ── Anomali eşikleri ──────────────────────────────────────────────────────────

_THRESHOLDS = {
    "market":    {"warning": 0.15, "error": 0.50},   # M01 / M05 Phase 1
    "appliance": {"warning": 0.10, "error": 0.30},   # M05 Phase 2
    "fuel":      {"warning": 0.05, "error": 0.20},   # M07
}

# M07: her provider için beklenen yakıt tipleri (DB'deki gerçek combination'lara göre)
# Yeni provider eklenirse burayı güncelle
_FUEL_EXPECTED = {
    "petrolofisi": ["gasoline_95", "diesel", "lpg"],
    "opet":        ["gasoline_95", "diesel", "lpg"],
    "shell":       ["gasoline_95", "diesel", "lpg"],
}

# ── Dataclass'lar ─────────────────────────────────────────────────────────────


@dataclass
class PriceAnomaly:
    identifier: str          # "BİM / Domestos 750ml" veya "Philips HR2041"
    yesterday: float
    today: float
    change_pct: float        # pozitif = artış, negatif = düşüş


@dataclass
class ModuleHealthReport:
    module_code: str
    module_name: str
    date: date
    status: str = "ok"                              # "ok" | "warning" | "error"
    records_today: int = 0
    records_yesterday: int = 0
    expected: int = 0                               # 0 = bilinmiyor
    missing: list[str] = field(default_factory=list)
    anomalies: list[PriceAnomaly] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def add_warning(self, msg: str) -> None:
        self.warnings.append(msg)
        if self.status == "ok":
            self.status = "warning"

    def add_error(self, msg: str) -> None:
        self.errors.append(msg)
        self.status = "error"


@dataclass
class PipelineHealthReport:
    date: date
    overall_status: str = "ok"
    modules: list[ModuleHealthReport] = field(default_factory=list)


# ── Yardımcı fonksiyonlar ─────────────────────────────────────────────────────


def _load_appliances_yaml() -> list[dict]:
    path = Path("modules") / "m05_household" / "config" / "appliances.yaml"
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f).get("appliances", [])


def _load_fuel_locations() -> list[dict]:
    path = Path("modules") / "m07_fuel" / "config" / "locations.yaml"
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f).get("locations", [])


def _pct_label(pct: float) -> str:
    sign = "+" if pct >= 0 else ""
    return f"{sign}{pct:.1f}%"


# ── Modül bazında kontrol fonksiyonları ───────────────────────────────────────


async def check_market_health(conn, target_date: date) -> ModuleHealthReport:
    """
    M01 + M05 Phase 1: price_snapshots bütünlük ve anomali kontrolü.
    Her iki modül de aynı tabloyu kullandığı için birlikte kontrol edilir.
    """
    yesterday = target_date - timedelta(days=1)
    report = ModuleHealthReport(
        module_code="01+05p1",
        module_name="Market Fiyatları (M01 + M05 Aşama 1)",
        date=target_date,
    )

    # Bugün ve dün yazılan toplam snapshot sayısı
    row_today = await conn.fetchrow(
        "SELECT COUNT(*) as cnt FROM price_snapshots WHERE snapshot_date = $1",
        target_date,
    )
    row_yest = await conn.fetchrow(
        "SELECT COUNT(*) as cnt FROM price_snapshots WHERE snapshot_date = $1",
        yesterday,
    )
    report.records_today     = int(row_today[0]) if row_today else 0
    report.records_yesterday = int(row_yest[0])  if row_yest  else 0

    # Dün hiç veri yoksa (ilk gün) anomali karşılaştırması atlanır
    if report.records_today == 0:
        report.add_error("Bugün price_snapshots tablosuna hiç kayıt yazılmamış")
        return report

    # Dünkü veriye kıyasla ±%20'den fazla değişim → uyarı
    if report.records_yesterday > 0:
        change = abs(report.records_today - report.records_yesterday) / report.records_yesterday
        if change > 0.20:
            report.add_warning(
                f"Kayıt sayısı dünden %{change*100:.0f} farklı "
                f"({report.records_yesterday} → {report.records_today})"
            )

    # Başarısız scrape_runs: MarketFiyati run'ları (Trendyol hariç)
    # Aynı market için birden fazla run varsa (scheduler + manuel), en az biri success ise OK
    failed_runs = await conn.fetch(
        """
        SELECT market
        FROM scrape_runs
        WHERE run_date = $1
          AND (market LIKE 'm01:%' OR (market LIKE 'm05:%' AND market NOT LIKE 'm05:trendyol%'))
        GROUP BY market
        HAVING MAX(CASE WHEN status = 'success' THEN 1 ELSE 0 END) = 0
        """,
        target_date,
    )
    for row in failed_runs:
        report.add_error(f"Başarısız run: {row[0]}")
        report.missing.append(str(row[0]))

    # Fiyat anomalisi: dün ve bugün aynı ürün için >%15 değişim
    # Ürün başına tek anomali (konumdan bağımsız, en yüksek % değişimi al)
    thr = _THRESHOLDS["market"]
    anomalies = await conn.fetch(
        """
        SELECT mp.market, mp.market_sku, mp.market_name,
               MAX(CAST(t.price AS REAL))  AS today_price,
               MAX(CAST(y.price AS REAL))  AS yesterday_price
        FROM price_snapshots t
        JOIN price_snapshots y
            ON t.market_product_id = y.market_product_id
        JOIN market_products mp ON mp.id = t.market_product_id
        WHERE t.snapshot_date = $1
          AND y.snapshot_date = $2
          AND y.price > 0
          AND ABS(CAST(t.price AS REAL) - CAST(y.price AS REAL))
              / CAST(y.price AS REAL) > $3
        GROUP BY mp.market, mp.market_sku, mp.market_name
        ORDER BY
            ABS(MAX(CAST(t.price AS REAL)) - MAX(CAST(y.price AS REAL)))
            / MAX(CAST(y.price AS REAL)) DESC
        LIMIT 20
        """,
        target_date,
        yesterday,
        thr["warning"],
    )
    for row in anomalies:
        today_p = float(row[3])
        yest_p  = float(row[4])
        pct     = (today_p - yest_p) / yest_p * 100
        label   = f"{row[0]} / {str(row[2])[:40]}"
        report.anomalies.append(PriceAnomaly(label, yest_p, today_p, round(pct, 1)))
        if abs(pct) / 100 > thr["error"]:
            report.add_error(f"Kritik fiyat değişimi: {label} {_pct_label(pct)}")
        else:
            report.add_warning(f"Fiyat değişimi: {label} {_pct_label(pct)}")

    return report


async def check_appliance_health(conn, target_date: date) -> ModuleHealthReport:
    """M05 Phase 2: appliance_prices bütünlük ve anomali kontrolü."""
    yesterday = target_date - timedelta(days=1)
    report = ModuleHealthReport(
        module_code="05p2",
        module_name="Beyaz Eşya & Küçük Aletler (M05 Aşama 2)",
        date=target_date,
    )

    # YAML'daki beklenen tüm SKU'lar
    appliances = _load_appliances_yaml()
    expected_skus: dict[str, dict] = {}  # sku → {brand, model, keyword}
    for entry in appliances:
        for s in entry.get("tracked_skus", []):
            expected_skus[str(s["sku"])] = {
                "brand":   s.get("brand", "?"),
                "model":   s.get("model", "?")[:40],
                "keyword": entry["keyword"],
            }
    report.expected = len(expected_skus)

    # Bugün DB'de olan SKU'lar
    rows_today = await conn.fetch(
        "SELECT sku FROM appliance_prices WHERE date = $1",
        target_date,
    )
    found_skus = {str(row[0]) for row in rows_today}
    report.records_today = len(found_skus)

    # Dün
    rows_yest = await conn.fetch(
        "SELECT COUNT(*) as cnt FROM appliance_prices WHERE date = $1",
        yesterday,
    )
    report.records_yesterday = int(rows_yest[0][0]) if rows_yest else 0

    # Eksik SKU'lar
    missing_skus = set(expected_skus.keys()) - found_skus
    for sku in sorted(missing_skus):
        info = expected_skus[sku]
        label = f"[{info['keyword']}] {info['brand']} {info['model']} (SKU {sku})"
        report.missing.append(label)
        report.add_warning(f"Eksik SKU: {label}")

    if report.records_today == 0 and report.expected > 0:
        report.add_error("Bugün appliance_prices tablosuna hiç kayıt yazılmamış")
        return report

    # Fiyat anomalisi
    thr = _THRESHOLDS["appliance"]
    anomalies = await conn.fetch(
        """
        SELECT a.sku, a.brand, a.model,
               a.price AS today_price, y.price AS yesterday_price
        FROM appliance_prices a
        JOIN appliance_prices y ON a.sku = y.sku AND a.source = y.source
        WHERE a.date = $1
          AND y.date = $2
          AND y.price > 0
          AND ABS(CAST(a.price AS REAL) - CAST(y.price AS REAL))
              / CAST(y.price AS REAL) > $3
        ORDER BY
            ABS(CAST(a.price AS REAL) - CAST(y.price AS REAL))
            / CAST(y.price AS REAL) DESC
        LIMIT 20
        """,
        target_date,
        yesterday,
        thr["warning"],
    )
    for row in anomalies:
        today_p = float(row[3])
        yest_p  = float(row[4])
        pct     = (today_p - yest_p) / yest_p * 100
        label   = f"{row[1]} {str(row[2])[:35]}"
        report.anomalies.append(PriceAnomaly(label, yest_p, today_p, round(pct, 1)))
        if abs(pct) / 100 > thr["error"]:
            report.add_error(f"Kritik fiyat değişimi: {label} {_pct_label(pct)}")
        else:
            report.add_warning(f"Fiyat değişimi: {label} {_pct_label(pct)}")

    return report


async def check_fuel_health(conn, target_date: date) -> ModuleHealthReport:
    """M07: fuel_prices bütünlük ve anomali kontrolü."""
    yesterday = target_date - timedelta(days=1)
    report = ModuleHealthReport(
        module_code="07",
        module_name="Akaryakıt (M07)",
        date=target_date,
    )

    # Beklenen kombinasyonlar
    locations = _load_fuel_locations()
    expected: set[tuple[str, str, str]] = set()
    for loc in locations:
        city = loc["city"]
        for provider, fuel_types in _FUEL_EXPECTED.items():
            for ft in fuel_types:
                expected.add((provider, city, ft))
    report.expected = len(expected)

    # Bugün DB'de olanlar
    rows_today = await conn.fetch(
        "SELECT provider, city, fuel_type, price FROM fuel_prices WHERE date = $1",
        target_date,
    )
    report.records_today = len(rows_today)
    found: set[tuple[str, str, str]] = set()
    today_prices: dict[tuple[str, str, str], float] = {}
    for row in rows_today:
        key = (str(row[0]), str(row[1]), str(row[2]))
        found.add(key)
        today_prices[key] = float(row[3])

    # Dün
    rows_yest = await conn.fetch(
        "SELECT provider, city, fuel_type, price FROM fuel_prices WHERE date = $1",
        yesterday,
    )
    yest_prices: dict[tuple[str, str, str], float] = {}
    for row in rows_yest:
        key = (str(row[0]), str(row[1]), str(row[2]))
        yest_prices[key] = float(row[3])
    report.records_yesterday = len(rows_yest)

    if report.records_today == 0 and report.expected > 0:
        report.add_error("Bugün fuel_prices tablosuna hiç kayıt yazılmamış")
        return report

    # Eksik kombinasyonlar
    missing = expected - found
    for provider, city, ft in sorted(missing):
        label = f"{provider} / {city} / {ft}"
        report.missing.append(label)
        report.add_warning(f"Eksik yakıt verisi: {label}")

    # Fiyat anomalisi
    thr = _THRESHOLDS["fuel"]
    for key, today_p in today_prices.items():
        if key not in yest_prices:
            continue
        yest_p = yest_prices[key]
        if yest_p == 0:
            continue
        pct = (today_p - yest_p) / yest_p * 100
        if abs(pct) / 100 > thr["warning"]:
            provider, city, ft = key
            label = f"{provider} / {city} / {ft}"
            report.anomalies.append(
                PriceAnomaly(label, yest_p, today_p, round(pct, 1))
            )
            if abs(pct) / 100 > thr["error"]:
                report.add_error(f"Kritik yakıt fiyat değişimi: {label} {_pct_label(pct)}")
            else:
                report.add_warning(f"Yakıt fiyat değişimi: {label} {_pct_label(pct)}")

    return report


# ── Ana entry point ───────────────────────────────────────────────────────────


async def run_health_check(
    conn,
    target_date: date | None = None,
) -> PipelineHealthReport:
    """
    Tüm modülleri kontrol eder, PipelineHealthReport döner.
    target_date=None → bugün.
    """
    if target_date is None:
        target_date = date.today()

    pipeline = PipelineHealthReport(date=target_date)

    for check_fn in [check_market_health, check_appliance_health, check_fuel_health]:
        try:
            mod_report = await check_fn(conn, target_date)
            pipeline.modules.append(mod_report)
        except Exception as exc:
            logger.error("[health] %s kontrolü sırasında hata: %s", check_fn.__name__, exc, exc_info=True)

    # Genel durum: en kötü modül durumunu al
    statuses = {r.status for r in pipeline.modules}
    if "error" in statuses:
        pipeline.overall_status = "error"
    elif "warning" in statuses:
        pipeline.overall_status = "warning"
    else:
        pipeline.overall_status = "ok"

    return pipeline


# ── Raporlama ─────────────────────────────────────────────────────────────────

_STATUS_ICON = {"ok": "✓ TAMAM", "warning": "⚠ UYARI", "error": "✗ HATA"}


def format_report(report: PipelineHealthReport) -> str:
    """İnsan okunabilir konsol raporu oluşturur."""
    lines: list[str] = []
    sep = "═" * 56
    lines.append(sep)
    lines.append(f" INFDATABASE SAĞLIK RAPORU — {report.date}")
    lines.append(sep)

    for mod in report.modules:
        icon = _STATUS_ICON.get(mod.status, mod.status.upper())
        header = f"[{mod.module_code.upper()}] {mod.module_name}"
        lines.append(f"\n{header:<42}  {icon}")

        # Kayıt özeti
        if mod.expected > 0:
            completeness = f"{mod.records_today}/{mod.expected}"
        else:
            completeness = str(mod.records_today)

        yest_diff = ""
        if mod.records_yesterday > 0 and mod.records_today > 0:
            pct = (mod.records_today - mod.records_yesterday) / mod.records_yesterday * 100
            yest_diff = f"  ({_pct_label(pct)} dün)"

        lines.append(f"  Kayıtlar  : {completeness}{yest_diff}")

        # Eksikler
        if mod.missing:
            lines.append(f"  Eksik     : {len(mod.missing)} adet")
            for m in mod.missing[:5]:
                lines.append(f"    - {m}")
            if len(mod.missing) > 5:
                lines.append(f"    ... ve {len(mod.missing)-5} eksik daha")

        # Anomaliler
        if mod.anomalies:
            lines.append(f"  Anomali   : {len(mod.anomalies)} fiyat değişimi")
            for a in mod.anomalies[:3]:
                lines.append(
                    f"    - {a.identifier[:40]}  "
                    f"{a.yesterday:.2f} → {a.today:.2f} TL  "
                    f"({_pct_label(a.change_pct)})"
                )
            if len(mod.anomalies) > 3:
                lines.append(f"    ... ve {len(mod.anomalies)-3} anomali daha")
        else:
            lines.append("  Anomali   : —")

        # Hatalar
        for e in mod.errors:
            lines.append(f"  [HATA] {e}")

    lines.append(f"\n{sep}")
    overall_icon = _STATUS_ICON.get(report.overall_status, report.overall_status.upper())
    lines.append(f"GENEL DURUM: {overall_icon}  •  {report.date}")
    lines.append(f"Detaylı rapor: logs/health_{report.date}.json")
    lines.append(sep)
    return "\n".join(lines)


def save_report(report: PipelineHealthReport, log_dir: str = "logs") -> str:
    """JSON raporunu logs/ klasörüne kaydeder. Dosya yolunu döner."""
    os.makedirs(log_dir, exist_ok=True)
    path = os.path.join(log_dir, f"health_{report.date}.json")

    def _serial(obj):
        if isinstance(obj, date):
            return str(obj)
        if hasattr(obj, "__dataclass_fields__"):
            return asdict(obj)
        return str(obj)

    data = {
        "date":           str(report.date),
        "overall_status": report.overall_status,
        "modules": [
            {
                "module_code":        m.module_code,
                "module_name":        m.module_name,
                "status":             m.status,
                "records_today":      m.records_today,
                "records_yesterday":  m.records_yesterday,
                "expected":           m.expected,
                "missing_count":      len(m.missing),
                "missing":            m.missing,
                "anomaly_count":      len(m.anomalies),
                "anomalies": [
                    {
                        "identifier":  a.identifier,
                        "yesterday":   a.yesterday,
                        "today":       a.today,
                        "change_pct":  a.change_pct,
                    }
                    for a in m.anomalies
                ],
                "warnings": m.warnings,
                "errors":   m.errors,
            }
            for m in report.modules
        ],
    }

    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, default=_serial)

    logger.info("[health] Rapor kaydedildi: %s", path)
    return path
