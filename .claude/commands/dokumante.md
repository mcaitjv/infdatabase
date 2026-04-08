# /dokumante — Modül Dokümantasyonu Güncelle

`docs/inflation-database-methodology.md` içindeki ilgili modül bölümünü gerçek implementasyona göre doldurur veya günceller. Sonuç Notion'a yapıştırmaya hazır Markdown formatındadır.

## Kullanım

```
/dokumante          → hangi modülü dokümante etmek istediğini sorar
/dokumante 07       → Modül 07'yi dokümante eder
/dokumante 01 07    → Modül 01 ve 07'yi dokümante eder
```

## Talimatlar

Kullanıcının belirttiği modül(ler) için aşağıdaki adımları izle:

### 1. Mevcut kodu oku
- `modules/mXX_<ad>/__init__.py` — modül sınıfı, ağırlık, orkestrasyon
- `modules/mXX_<ad>/scrapers/*.py` — tüm scraper dosyaları
- `modules/mXX_<ad>/config/*.yaml` — konfigürasyon dosyaları
- `db/models.py` — ilgili model(ler)
- `db/schema.sql` — ilgili tablo(lar)

### 2. Mevcut dokümantasyonu oku
- `docs/inflation-database-methodology.md` içindeki ilgili `# MODÜL XX` bölümünü oku

### 3. Bölümü güncelle
`docs/inflation-database-methodology.md` içindeki `# MODÜL XX` bölümünü aşağıdaki şablona göre doldur:

```markdown
# MODÜL XX — <Ad>

**COICOP Kodu:** XX
**Ağırlık:** %XX.XX
**Durum:** ✅ Tamamlandı — Günlük çalışıyor

---

## Veri Kaynakları

| Sağlayıcı | URL | Yöntem | Kapsam |
|-----------|-----|--------|--------|
| ... | ... | ... | ... |

---

## Takip Edilen [Ürünler / Yakıt Tipleri / Kategoriler]

(Modüle göre uygun tablo/liste)

---

## Kapsanan Şehirler

(Şehir-ilçe tablosu)

---

## Veritabanı Şeması

(İlgili tablo ASCII şeması + UNIQUE constraint açıklaması)

---

## Scraper Teknik Detayları

(Her scraper için: URL, yöntem, parse stratejisi, özel notlar)

---

## Günlük Çıktı İstatistikleri

| Sağlayıcı | Kayıt/Gün | ... |
|-----------|-----------|-----|
| **Toplam** | **N** | |

---

## Kod Dosyaları

| Dosya | Açıklama |
|-------|----------|
| `modules/mXX_.../` | ... |
```

### 4. Durum alanını güncelle
Metodoloji dosyasının başındaki **Modül Listesi** tablosunda ilgili satırı `🔲 Planlandı` → `✅ Tamamlandı` olarak güncelle.

### 5. Commit et
Değişiklikleri commit et:
```
docs(mXX): Modül XX dokümantasyonu tamamlandı
```

## Notlar

- Placeholder olan `*Doldurulacak*` satırları tamamen kaldır
- Mevcut içerik varsa üzerine yaz (eski bilgi kalmasın)
- Notion'a yapıştırma için standart Markdown kullan (emoji ✅🔲 dahil)
- Kod dosyası yollarını güncel repo yapısına göre yaz
