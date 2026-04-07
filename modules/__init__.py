"""
Modül kaydı — tüm COICOP modüllerini merkezi olarak tutar.

Yeni modül eklemek:
  1. modules/mXX_<ad>/__init__.py içinde BaseModule alt sınıfı oluştur
  2. Aşağıdaki ALL_MODULES sözlüğüne ekle
"""

from modules.base import BaseModule
from modules.m01_food import FoodModule
from modules.m07_fuel import FuelModule

ALL_MODULES: dict[str, type[BaseModule]] = {
    "01": FoodModule,
    "07": FuelModule,
}


def get_modules(codes: list[str] | None = None) -> list[BaseModule]:
    """
    Belirtilen COICOP kodlarına göre modül örnekleri döner.
    codes=None → kayıtlı tüm modüller.
    """
    if codes is None:
        return [cls() for cls in ALL_MODULES.values()]
    result = []
    for code in codes:
        if code not in ALL_MODULES:
            raise ValueError(f"Bilinmeyen modül kodu: {code!r}. Geçerli: {list(ALL_MODULES)}")
        result.append(ALL_MODULES[code]())
    return result
