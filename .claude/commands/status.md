# /status — Pipeline Durum Özeti

Bugünkü (veya belirtilen tarihteki) pipeline çalışmasının durumunu özetler. Büyük log dosyasını tam okumak yerine health JSON + hedefli Grep kullanır.

## Kullanım

```
/status                → bugünün durumu
/status 2026-04-10     → belirli tarih
```

## Talimatlar

Kullanıcı `/status [TARİH]` dediğinde şu adımları izle. Tarih verilmezse bugünü kullan.

### 1. Sağlık JSON'ını oku

`logs/health_YYYY-MM-DD.json` — yoksa `logs/health_*.json` içinden en yeniyi al.
Eğer hiç yoksa "bugün henüz health raporu üretilmemiş" uyarısı ver, log taramasına geç.

### 2. Log dosyasını Grep ile tara (Read KULLANMA)

`logs/YYYY-MM-DD.log` üzerinde şu pattern'leri ara:

- `\[runner\]` → modül geçişleri (`head_limit=30`)
- `ERROR|Traceback|başarısız|failed` → hatalar, context=2 ile (`head_limit=20`)
- Son aktif scraper için log dosyasının **son 3 satırını** PowerShell ile al:
  ```
  Get-Content logs/YYYY-MM-DD.log -Tail 3
  ```

### 3. PID lock kontrol et

`logs/pipeline.pid` varsa → PID'in yaşayıp yaşamadığını `Get-Process -Id <pid>` ile doğrula.
- Process varsa: pipeline aktif
- Process yoksa: stale lock, pipeline ölmüş

### 4. Durum bilgilerini derle

Her modül için log'dan şunu çıkar:
- `[runner] Modül XX başlıyor` var + `tamamlandı` yok → **running**
- `[runner] Modül XX tamamlandı` var → **done** (health JSON'dan status al: ok/warning/error)
- Hiç `başlıyor` kaydı yok → **waiting**

### 5. Görsel raporu render et

Aşağıdaki şablonu doldur ve kullanıcıya ver. Her modül için uygun sembol ve progress bar kullan:

**Semboller:**
- `✓` — tamamlandı, ok
- `⚠` — tamamlandı, warning/error
- `⟳` — şu an çalışıyor
- `·` — bekliyor (henüz başlamadı)

**Progress bar** (20 karakter):
- done-ok:      `████████████████████`
- done-warning: `████████████████████`
- done-error:   `████████████████████`
- running:      `██████████░░░░░░░░░░`
- waiting:      `░░░░░░░░░░░░░░░░░░░░`

```
Pipeline — YYYY-MM-DD  ·  [DURUM]  [başlangıç saati varsa]

┌─ AKIŞ ─────────────────────────────────────────────┐
│                                                     │
│  [M01 ?] ──▶ [M05 ?] ──▶ [M07 ?] ──▶ [M13 ?]      │
│   Gıda        Ev&Mob       Yakıt       Bakım        │
│                                                     │
└─────────────────────────────────────────────────────┘

  M01  Gıda & İçecekler   ████████████████████  ✓   X.XXX kayıt
  M05  Ev & Mobilya       ██████████░░░░░░░░░░  ⟳   çalışıyor
  M07  Ulaştırma          ░░░░░░░░░░░░░░░░░░░░  ·   bekliyor
  M13  Kişisel Bakım      ░░░░░░░░░░░░░░░░░░░░  ·   bekliyor

  Son eylem: [m05:vestel] camasir_makinesi ✓  (HH:MM:SS)

M05 Parts:          kayıt    beklenen   durum
  appliances        266/269    ⚠  3 eksik SKU
  mobilya           132/134    ⚠  2 Vivense eksik
  züccaciye           0/ 90    ✗  HİÇ KAYIT YOK
  evbakim             0/  0    ✓

Anomaliler (N adet, en kritik 3):
  kaynak / ürün adı   +X.X%  ⚠
  kaynak / ürün adı   +X.X%  ⚠
  kaynak / ürün adı   −X.X%  ⚠

Hatalar (varsa):
  ✗ açıklama
```

**Özel durumlar:**
- M05 parts bilgisi sadece health JSON varsa göster; yoksa parts satırını atla.
- Anomaliler yoksa o bölümü atla.
- Pipeline tamamlandıysa "Son eylem" satırı yerine "Süre: X dk" yaz.
- `[DURUM]` yerine: `⟳ DEVAM EDİYOR` / `✓ TAMAMLANDI` / `⚠ HATA İLE BİTTİ` / `✗ ÇÖKTÜ`

### 6. Yapma!

- Log dosyasını `Read` ile tam OKUMA — 2000+ satır, token israfı.
- Gerekmedikçe DB sorgusu yapma.
- Rapor 30 satırı geçmesin.

## Neden var?

Log dosyaları 2000+ satır, tam okumak ~50K token. Health JSON + hedefli grep aynı bilgiyi ~3K token'da verir (%95 tasarruf).
