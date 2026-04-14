# /discover — Discovery Komut Wrapper

Pre-flight kontrolü ile branch/appliance/furniture keşfini çalıştırır. `branches.yaml`'in yanlışlıkla boşalması gibi kazaları önler.

## Kullanım

```
/discover branches      → M01 şube keşfi (config/branches.yaml)
/discover appliances    → M05 beyaz eşya SKU keşfi
/discover furniture     → M05 mobilya/tekstil SKU keşfi
```

## Talimatlar

### 1. Parametre eşlemesi

| Argüman | Hedef YAML | CLI bayrağı |
|---------|-----------|-------------|
| `branches` | `config/branches.yaml` | `--discover-branches` |
| `appliances` | `modules/m05_household/config/appliances.yaml` | `--discover-appliances` |
| `furniture` | `modules/m05_household/config/furniture.yaml` | `--discover-furniture` |

### 2. Pre-flight check

Hedef YAML dosyasını Read ile oku. Boş değilse (entries var ise) kullanıcıya göster:

```
config/branches.yaml'da hali hazırda 3 şehir × 5 market = 15 entry var.
Discovery bu dosyanın üzerine yazacak. Devam edelim mi?
```

Kullanıcı onayı beklemeden devam etme. Onay geldiyse devam et.

Boşsa direkt devam et, onay sorma.

### 3. Komutu çalıştır

Discovery uzun sürer (branches ~5 dk, appliances/furniture ~3 dk her biri). Background olarak çalıştır:

```bash
python -m pipeline.runner --discover-<kind>
```

`run_in_background=true` Bash parametresi ile. Çalışmaya başladığını kullanıcıya söyle.

### 4. Bitince özetle

Background task tamamlanınca hedef YAML dosyasını tekrar oku ve özet ver:

```
Keşif tamamlandı — config/branches.yaml

Istanbul: 5 market (a101, bim, carrefour, migros, sok)
Ankara:   5 market
Izmir:    5 market

Toplam: 15 şube ID'si yazıldı.
```

Appliances/furniture için:
```
Toplam N keyword işlendi, her birinde top-3 SKU seçildi.
En yoğun kategori: <kategori> (<N> SKU)
```

### 5. Dikkat

- **Asla discovery'yi foreground çalıştırma** — diğer tool call'ları bloklar.
- Eğer pipeline zaten çalışıyorsa (`logs/pipeline.pid` mevcutsa) discovery başlatma — çakışma riski.
- Keşif bitince git durumunu göster (`git status` short): kullanıcı diff'i kontrol edip commit edebilsin.
