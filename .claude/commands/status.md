# /status — Pipeline Durum Özeti

Bugünkü (veya belirtilen tarihteki) pipeline çalışmasının durumunu özetler. Büyük log dosyasını tam okumak yerine health JSON + hedefli Grep kullanır.

## Kullanım

```
/status                → bugünün durumu
/status 2026-04-10     → belirli tarih
```

## Talimatlar

Kullanıcı `/status [TARİH]` dediğinde şu adımları izle. Tarih verilmezse bugünü (`date` komutu ile TR saatine göre) kullan.

### 1. Sağlık JSON'ını oku (küçük, yapısal)

```
logs/health_YYYY-MM-DD.json
```

Yoksa `logs/health_*.json` dosyalarından en yeniyi al. Bu dosya zaten özet bilgiyi içerir:
- `run_metadata.status`, `run_metadata.duration_seconds`
- Her modül için: `records_today`, `records_yesterday`, `status`, `anomalies`, `missing`
- `anomalies` listesi (en yüksek 10)

Eğer JSON yoksa "bugün henüz health raporu üretilmemiş" uyarısı ver ve log taramasına geç.

### 2. Log dosyasını Grep ile tara (tam okuma YAPMA)

```
logs/YYYY-MM-DD.log
```

Grep tool kullan, **Read KULLANMA**. Şu pattern'leri ara (`output_mode=content`, `-n=false`):

- `\[runner\]` — modül başlangıç/bitiş satırları
- `ERROR|Traceback|başarısız|failed` — hatalar
- `tamamland` — tamamlanma bildirimleri
- `Modül.*başlıyor` — modül transition'ları

Her pattern için `head_limit=30` ile sınırla. Eğer hata satırları varsa bunların etrafında `-C 2` context al.

### 3. PID lock kontrol et

`logs/pipeline.pid` dosyası var mı? Varsa pipeline hâlâ çalışıyor demektir — bunu belirt.

### 4. Özet raporu yaz

Kullanıcıya şu formatta özet ver (Türkçe, kısa):

```
Pipeline durumu — 2026-04-11

Durum: devam ediyor | tamamlandı | hatalı
Süre: X dakika (tamamlandıysa)

Modüller:
  M01 Gıda     — 4521 kayıt, ok
  M05 Ev Eş.   — 312 kayıt, warning (2 anomali)
  M07 Yakıt    — 84 kayıt, ok

Anomaliler: N adet (en yüksek 3 tanesini listele)
Hatalar:    N satır (varsa ilk 3 tanesini göster)
```

### 5. Yapma!

- Log dosyasını `Read` ile tam OKUMA — 2000+ satır, token israfı.
- Gerekmedikçe DB sorgusu yapma — health JSON zaten sayıları içerir.
- Uzun çıktı verme. Rapor 20 satırdan kısa olsun.

## Neden var?

Log dosyaları 2000+ satır, tam okumak ~50K token. Health JSON + hedefli grep aynı bilgiyi ~3K token'da verir (%95 tasarruf).
