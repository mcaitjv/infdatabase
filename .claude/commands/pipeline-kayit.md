# /pipeline-kayit — Modül Pipeline + Health Kaydı

Tamamlanan bir modülü (veya Tip B modüle eklenen bağımsız bir parçayı) iki yere kaydeder:
1. **Pipeline** → `modules/__init__.py` `ALL_MODULES` + `pipeline/runner.py` import
2. **Health mail** → `pipeline/health.py` check fonksiyonu + `run_health_check` kaydı

Notifier (`pipeline/notifier.py`) `report.modules` üzerinde generic döngü kurduğundan **değişiklik gerektirmez** — health'e kaydedilen her modül otomatik maile girer.

---

## Kullanım

```
/pipeline-kayit            → interaktif
/pipeline-kayit 07         → M07 FuelModule — tüm bilgiler koda mevcut
/pipeline-kayit 05 mobilya → M05'e mobilya parçası (health için)
```

---

## Talimatlar

### 0. Dosyaları oku

Her zaman önce şunları oku:
```
modules/__init__.py
pipeline/health.py    (sadece: _THRESHOLDS, check_*_health fonksiyonları, run_health_check)
pipeline/runner.py    (sadece: import satırları, 1-35. satırlar yeterli)
```

### 1. Niyet belirle

Argümanlara göre:
- **Argüman yok** → `AskUserQuestion` ile sor:
  - "Ne kaydetmek istiyorsun?"
  - `a) Yeni modül (ALL_MODULES + health check)`
  - `b) Tip B modüle yeni part (sadece health bölümü)`
- **Tek sayı** (`07`, `01` vb.) → **(a) Yeni modül**
- **Sayı + slug** (`05 mobilya`) → **(b) Yeni part**

### 2. (a) Yeni modül kaydı

#### 2.1 Bilgileri topla

Eksik olanları `AskUserQuestion` ile sor (tek sorguda, maks 3 soru):
- **COICOP kodu**: `modules/m<KOD>_*/` klasörü mevcut mu kontrol et
- **Python sınıf adı**: örn. `FuelModule`, `ClothingModule`
- **Modül tipi**:
  - **A** — Keyword-tabanlı (M01 gibi; `m<KOD>_price_snapshots` tablosu)
  - **B** — Discovery+Tracked (M05 gibi; `m<KOD>_fact_*_price` + config YAML'ları)
  - **C** — Lokasyon-tabanlı (M07 gibi; `m<KOD>_*_prices` + locations.yaml)

Modül tipi belirsizse ilgili `modules/m<KOD>_<slug>/__init__.py` dosyasına bak.

#### 2.2 `modules/__init__.py` güncelle

Zaten kayıtlıysa atla. Değilse:

```python
# Import satırına ekle (mevcut importların altına, alfabetik sıra):
from modules.m<KOD>_<slug> import <SınıfAdı>

# ALL_MODULES dict'ine ekle (kod sırasına göre):
ALL_MODULES = {
    ...,
    "<KOD>": <SınıfAdı>,
}
```

#### 2.3 `pipeline/runner.py` güncelle

Zaten import satırında varsa atla. Değilse diğer modül importlarının altına ekle:
```python
from modules.m<KOD>_<slug> import <SınıfAdı>
```

#### 2.4 `pipeline/health.py` — check fonksiyonu ekle

`run_health_check` içindeki `check_fn` listesine **geçmeden önce** fonksiyonu oluştur.

Tipe göre template seç ve **mevcut check fonksiyonlarından hemen önce** (run_health_check'in üstüne) ekle:

**Tip A (keyword tabanlı):**
```python
async def check_<kod>_health(conn, target_date: date) -> ModuleHealthReport:
    """M<KOD>: m<kod>_price_snapshots bütünlük ve anomali kontrolü."""
    yesterday = target_date - timedelta(days=1)
    report = ModuleHealthReport(
        module_code="<KOD>",
        module_name="<Modül Adı> (M<KOD>)",
        date=target_date,
    )

    row_today = await conn.fetchrow(
        "SELECT COUNT(*) as cnt FROM m<kod>_price_snapshots WHERE snapshot_date = $1",
        target_date,
    )
    row_yest = await conn.fetchrow(
        "SELECT COUNT(*) as cnt FROM m<kod>_price_snapshots WHERE snapshot_date = $1",
        yesterday,
    )
    report.records_today     = int(row_today[0]) if row_today else 0
    report.records_yesterday = int(row_yest[0])  if row_yest  else 0

    if report.records_today == 0:
        report.add_error("Bugün m<kod>_price_snapshots tablosuna hiç kayıt yazılmamış")
        return report

    if report.records_yesterday > 0:
        change = abs(report.records_today - report.records_yesterday) / report.records_yesterday
        if change > 0.20:
            report.add_warning(
                f"Kayıt sayısı dünden %{change*100:.0f} farklı "
                f"({report.records_yesterday} → {report.records_today})"
            )

    # Fiyat anomalisi
    thr = _THRESHOLDS["market"]
    # TODO: Tabloyu ve kolon adlarını modüle göre ayarla
    return report
```

**Tip B (discovery+tracked):**
```python
def _load_m<kod>_parts() -> list[tuple[str, str, dict]]:
    """config/*.yaml dosyalarından (label, stem, categories) döner."""
    config_dir = Path("modules") / "m<kod>_<slug>" / "config"
    parts = []
    for path in sorted(config_dir.glob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        label = data.get("label", path.stem.replace("_", " ").title())
        cats = data.get("categories", {})
        if cats:
            parts.append((label, path.stem, cats))
    return parts


async def check_<kod>_health(conn, target_date: date) -> list[ModuleHealthReport]:
    """M<KOD>: m<kod>_fact_*_price bütünlük ve anomali kontrolü."""
    yesterday = target_date - timedelta(days=1)
    thr = _THRESHOLDS["appliance"]
    # TODO: Tablo adını gerçek şemaya göre güncelle (m<kod>_fact_*_price)

    rows_today = await conn.fetch(
        "SELECT d.source, d.sku, d.category FROM m<kod>_fact_<alan>_price f "
        "JOIN m<kod>_dim_<alan> d ON f.<alan>_key = d.<alan>_key WHERE f.date = $1",
        target_date,
    )
    found_skus: set[tuple[str, str]] = {(str(r[0]), str(r[1])) for r in rows_today}

    reports: list[ModuleHealthReport] = []
    for label, stem, categories in _load_m<kod>_parts():
        report = ModuleHealthReport(
            module_code=f"<KOD>-{stem}",
            module_name=f"{label} (M<KOD>)",
            date=target_date,
        )
        expected: dict[tuple[str, str], str] = {}
        for cat_key, cat_data in categories.items():
            for s in (cat_data.get("tracked_skus") or []):
                src = s.get("source", "?")
                expected[(src, str(s["sku"]))] = f"{src}/{cat_key} {s.get('model','')[:35]}"

        report.expected = len(expected)
        report.records_today = sum(1 for k in expected if k in found_skus)

        for key, lbl in sorted((k, v) for k, v in expected.items() if k not in found_skus):
            report.missing.append(lbl)
            report.add_warning(f"Eksik SKU: {lbl}")

        if report.records_today == 0 and report.expected > 0:
            report.add_error(f"Bugün {label} için hiç kayıt yazılmamış")

        reports.append(report)

    return reports if reports else [
        ModuleHealthReport(module_code="<KOD>", module_name="M<KOD>", date=target_date)
    ]
```

**Tip C (lokasyon tabanlı):**
```python
def _load_m<kod>_locations() -> list[dict]:
    path = Path("modules") / "m<kod>_<slug>" / "config" / "locations.yaml"
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f).get("locations", [])


async def check_<kod>_health(conn, target_date: date) -> ModuleHealthReport:
    """M<KOD>: m<kod>_*_prices bütünlük ve anomali kontrolü."""
    yesterday = target_date - timedelta(days=1)
    report = ModuleHealthReport(
        module_code="<KOD>",
        module_name="<Modül Adı> (M<KOD>)",
        date=target_date,
    )

    locations = _load_m<kod>_locations()
    # TODO: Beklenen kombinasyonları locations ve provider'lara göre hesapla
    report.expected = len(locations)  # placeholder

    row_today = await conn.fetchrow(
        "SELECT COUNT(*) FROM m<kod>_<tablo> WHERE date = $1", target_date
    )
    report.records_today = int(row_today[0]) if row_today else 0

    if report.records_today == 0 and report.expected > 0:
        report.add_error("Bugün m<kod>_<tablo> tablosuna hiç kayıt yazılmamış")

    return report
```

#### 2.5 `run_health_check` listesine ekle

`pipeline/health.py` içindeki `run_health_check` fonksiyonunda:
```python
for check_fn in [check_market_health, check_appliance_health, check_fuel_health]:
```
satırına yeni fonksiyonu ekle:
```python
for check_fn in [check_market_health, check_appliance_health, check_fuel_health, check_<kod>_health]:
```

#### 2.6 Sonuç özeti ver

Hangi dosyaların değiştiğini listele:
- `modules/__init__.py` — `ALL_MODULES["<KOD>"]` eklendi (veya zaten vardı)
- `pipeline/runner.py` — import eklendi (veya zaten vardı)
- `pipeline/health.py` — `check_<kod>_health` + `run_health_check` listesi güncellendi
- **TODO'lar varsa belirt**: tablo adları, kolon adları, anomali eşikleri

---

### 3. (b) Tip B modüle yeni part — health kaydı

Tip B modüllerin health check'i config YAML'larını dinamik okur (`_load_m<kod>_parts()`).
Yeni part eklendiyse bu fonksiyon onu otomatik alır — **ekstra kayıt gerekmez**.

Bunu doğrula:
1. `pipeline/health.py` içinde `_load_m<KOD>_parts()` fonksiyonu var mı?
2. Varsa: "✓ `<part_slug>` config YAML'ı yerleştirildikten sonra health otomatik algılar."
3. Yoksa: → adım 2.4'e git, Tip B template'i ile ekle, adım 2.5 ile kaydet.

---

## Önemli Notlar

- **TODO placeholder'ları bırak**: tablo/kolon adları modüle özgüdür; yanlış isim yazma, TODO yaz.
- `_THRESHOLDS` dict'ine yeni eşik türü eklenmesi gerekiyorsa kullanıcıya sor.
- Notifier değişikliği gerektirmez — `report.modules` generic döngü.
- Commit atmadan önce kullanıcı onayı al.
