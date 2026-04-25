# /dokumante — Modül Dokümantasyonu Güncelle

`docs/inflation-database-methodology.md` içindeki ilgili modül bölümünü gerçek implementasyona göre doldurur veya günceller, ardından Notion metodoloji sayfasını otomatik olarak günceller.

**Notion Sayfası:** https://www.notion.so/T-rkiye-Enflasyon-Veritaban-Metodoloji-33c7536c26a58108a119f11f00688cbe  
**Page ID:** `33c7536c26a58108a119f11f00688cbe` (`.env` → `NOTION_METHODOLOGY_PAGE_ID`)  
**Token:** `.env` → `NOTION_TOKEN`

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

### 5. Notion'a senkronize et

`.env` dosyasından `NOTION_TOKEN` ve `NOTION_METHODOLOGY_PAGE_ID` oku, sonra şu Python kodunu `Bash` aracıyla çalıştır:

```python
import os, json, requests
from dotenv import load_dotenv

load_dotenv()
token = os.getenv("NOTION_TOKEN")
page_id = os.getenv("NOTION_METHODOLOGY_PAGE_ID")
headers = {
    "Authorization": f"Bearer {token}",
    "Notion-Version": "2022-06-28",
    "Content-Type": "application/json",
}

# Mevcut tüm blokları sil
children = requests.get(f"https://api.notion.com/v1/blocks/{page_id}/children", headers=headers).json()
for block in children.get("results", []):
    requests.delete(f"https://api.notion.com/v1/blocks/{block['id']}", headers=headers)

# Yeni içeriği markdown → Notion blocks olarak dönüştür ve gönder
# docs/inflation-database-methodology.md dosyasının tamamını oku,
# her satırı aşağıdaki kurallara göre Notion block'a çevir:
#   "# " ile başlayan → heading_1
#   "## " ile başlayan → heading_2
#   "### " ile başlayan → heading_3
#   "---" → divider
#   "- " ile başlayan → bulleted_list_item
#   "```" ile başlayan kod bloğu → code block (language: python/bash/etc.)
#   "| " ile başlayan → paragraph (tablolar düz metin olarak aktarılır)
#   diğerleri → paragraph (boş satırlar atlanır)
# Blokları 100'lük gruplar halinde PATCH ile gönder
```

Senkronizasyon başarılıysa konsola `✅ Notion güncellendi` yaz. Hata varsa hata mesajını göster ve işleme devam et.

### 6. Commit et
Değişiklikleri commit et:
```
docs(mXX): Modül XX dokümantasyonu tamamlandı
```

## Notlar

- Placeholder olan `*Doldurulacak*` satırları tamamen kaldır
- Mevcut içerik varsa üzerine yaz (eski bilgi kalmasın)
- Standart Markdown kullan (emoji ✅🔲 dahil)
- Kod dosyası yollarını güncel repo yapısına göre yaz
- Notion API rate limit: 3 req/sn — büyük sayfalarda kısa `sleep(0.4)` ekle
