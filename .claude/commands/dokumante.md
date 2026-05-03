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

`.env` dosyasından `NOTION_TOKEN` ve `NOTION_PARENT_PAGE_ID` oku, sonra şu Python kodunu `Bash` aracıyla çalıştır.

**Strateji:** Mevcut sayfayı silme/düzenleme yok. Her çalıştırmada bugünün tarihiyle yeni bir alt sayfa oluştur, tüm markdown'ı oraya yükle. Eski sayfalar Notion'da versiyon geçmişi olarak kalır.

```python
import os, re, time, requests
from datetime import date
from dotenv import load_dotenv

load_dotenv()
token = os.getenv("NOTION_TOKEN")
parent_id = os.getenv("NOTION_PARENT_PAGE_ID")
headers = {
    "Authorization": f"Bearer {token}",
    "Notion-Version": "2022-06-28",
    "Content-Type": "application/json",
}
API = "https://api.notion.com/v1"

# ── 1. Yeni sayfa oluştur ─────────────────────────────────────────────────────
today = date.today().isoformat()  # 2026-05-02
title = f"Türkiye Enflasyon Veritabanı Metodoloji — {today}"
r = requests.post(f"{API}/pages", headers=headers, timeout=30, json={
    "parent": {"page_id": parent_id},
    "properties": {
        "title": {"title": [{"text": {"content": title}}]}
    }
})
r.raise_for_status()
new_page_id = r.json()["id"]
print(f"Yeni sayfa oluşturuldu: {title} ({new_page_id})")

# ── 2. Markdown → Notion blocks ───────────────────────────────────────────────
def rich(text):
    return [{"type": "text", "text": {"content": text[:2000]}}]

def md_to_blocks(md):
    blocks, in_code, code_lang, code_buf = [], False, "", []
    valid_langs = {"python","bash","shell","javascript","typescript","json","yaml",
                   "sql","html","css","markdown","go","rust","java","ruby","php",
                   "c","c++","c#","swift","kotlin","scala","perl","lua","r",
                   "powershell","plain text","diff"}
    for line in md.split("\n"):
        s = line.rstrip("\n")
        if in_code:
            if s.startswith("```"):
                lang = code_lang.lower() if code_lang.lower() in valid_langs else "plain text"
                blocks.append({"object":"block","type":"code","code":{
                    "rich_text": rich("\n".join(code_buf)[:2000] or " "),
                    "language": lang}})
                in_code, code_buf = False, []
            else:
                code_buf.append(s)
            continue
        if s.startswith("```"):
            code_lang, in_code, code_buf = s[3:].strip() or "plain text", True, []
        elif not s.strip(): continue
        elif s.startswith("# "):   blocks.append({"object":"block","type":"heading_1","heading_1":{"rich_text":rich(s[2:])}})
        elif s.startswith("## "):  blocks.append({"object":"block","type":"heading_2","heading_2":{"rich_text":rich(s[3:])}})
        elif s.startswith("### "): blocks.append({"object":"block","type":"heading_3","heading_3":{"rich_text":rich(s[4:])}})
        elif s.strip() == "---":   blocks.append({"object":"block","type":"divider","divider":{}})
        elif s.startswith("- ") or s.startswith("* "):
            blocks.append({"object":"block","type":"bulleted_list_item","bulleted_list_item":{"rich_text":rich(s[2:])}})
        elif re.match(r"^\d+\.\s", s):
            blocks.append({"object":"block","type":"numbered_list_item","numbered_list_item":{"rich_text":rich(re.sub(r"^\d+\.\s","",s))}})
        elif s.startswith("> "):   blocks.append({"object":"block","type":"quote","quote":{"rich_text":rich(s[2:])}})
        else:                      blocks.append({"object":"block","type":"paragraph","paragraph":{"rich_text":rich(s)}})
    return blocks

with open("docs/inflation-database-methodology.md", encoding="utf-8") as f:
    blocks = md_to_blocks(f.read())
print(f"Generated {len(blocks)} blocks")

# ── 3. 100'lük batch'ler halinde yeni sayfaya ekle ────────────────────────────
for i in range(0, len(blocks), 100):
    r = requests.patch(f"{API}/blocks/{new_page_id}/children",
                       headers=headers, json={"children": blocks[i:i+100]}, timeout=60)
    r.raise_for_status()
    time.sleep(0.4)

page_url = f"https://www.notion.so/{new_page_id.replace('-', '')}"
print(f"✅ Notion güncellendi ({len(blocks)} blok)")
print(f"   {page_url}")
```

Senkronizasyon tamamlanınca yeni sayfanın URL'ini konsola yaz. Hata varsa hata mesajını göster ve işleme devam et.

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
