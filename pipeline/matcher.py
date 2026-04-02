"""
Ürün Eşleştirme
----------------
Aşama 1: Barkod (EAN-13) — %100 doğru
Aşama 2: Fuzzy matching (rapidfuzz) — eşik 85/100
"""

import re
import unicodedata

from rapidfuzz import fuzz

_FUZZY_THRESHOLD = 85


def _normalize(text: str) -> str:
    """Türkçe karakterleri Latin'e çevirir, noktalama ve fazla boşlukları temizler."""
    # NFD normalize → combining char'ları ayır → ASCII'ye dönüştür
    nfkd = unicodedata.normalize("NFKD", text)
    ascii_text = nfkd.encode("ascii", "ignore").decode("ascii")
    # Küçük harf, alfanumerik + boşluk bırak
    cleaned = re.sub(r"[^a-z0-9\s]", " ", ascii_text.lower())
    return re.sub(r"\s+", " ", cleaned).strip()


def is_same_product_by_name(name_a: str, name_b: str) -> bool:
    """İki ürün adının aynı ürüne ait olup olmadığını fuzzy match ile kontrol eder."""
    score = fuzz.token_sort_ratio(_normalize(name_a), _normalize(name_b))
    return score >= _FUZZY_THRESHOLD


def find_best_match(
    candidate: str,
    pool: list[str],
) -> tuple[str, int] | None:
    """
    Aday ürün adını havuzdaki adlarla karşılaştırır.
    En yüksek skorlu eşleşmeyi döner; eşik altındaysa None.
    """
    best_name, best_score = None, 0
    normalized_candidate = _normalize(candidate)

    for name in pool:
        score = fuzz.token_sort_ratio(normalized_candidate, _normalize(name))
        if score > best_score:
            best_score = score
            best_name = name

    if best_score >= _FUZZY_THRESHOLD:
        return best_name, best_score
    return None
