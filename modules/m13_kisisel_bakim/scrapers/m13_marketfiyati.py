"""
M13 — marketfiyati.org.tr scraper (TÜBİTAK API)

M01'deki MarketFiyatiScraper'ı genişletir; kişisel bakım keyword'leri için
aynı endpoint'i kullanır. Yeni bir sınıf yerine ince bir wrapper — kaynak mantığı
m01_food/scrapers/marketfiyati.py'de ortak kalır.
"""

from modules.m01_food.scrapers.m01_marketfiyati import MarketFiyatiScraper


class MarketFiyatiScraper13(MarketFiyatiScraper):
    """M13 için MarketFiyatiScraper; market_name log prefix'i m13 olarak override edilir."""

    market_name = "m13:marketfiyati"
