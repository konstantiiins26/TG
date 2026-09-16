#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Быстрые локальные проверки чистой логики снайпера (без сети).

Запуск:  python test_gift_sniper.py

Покрывает то, что можно проверить детерминированно:
  * нормализацию адресов TON (raw <-> user-friendly, CRC16),
  * работу whitelist,
  * экономику сделки (комиссии с цены продажи, роялти, undercut),
  * перцентиль floor и его устойчивость к выбросам.

Сетевые вызовы (TonAPI / Getgems / Claude) здесь НЕ проверяются.
"""

import base64
import sys
from decimal import Decimal

import gift_sniper as gs


_failures = []


def check(name, condition, detail=""):
    """Мини-ассерт: копит провалы вместо остановки на первом."""
    if condition:
        print(f"  ok   {name}")
    else:
        print(f"  FAIL {name} {detail}")
        _failures.append(name)


def make_friendly_address(workchain: int, hash_hex: str, bounceable=True) -> str:
    """
    Собирает корректный user-friendly адрес TON из raw-компонентов.
    Используется как эталон для проверки декодера.
    """
    flags = 0x11 if bounceable else 0x51
    wc_byte = 0xFF if workchain == -1 else workchain
    body = bytes([flags, wc_byte]) + bytes.fromhex(hash_hex)
    crc = gs._crc16_xmodem(body)
    return base64.urlsafe_b64encode(body + crc.to_bytes(2, "big")).decode()


# =============================================================================
print("\n[1] Нормализация адресов TON")
# =============================================================================

H = "a1b2c3d4e5f60718293a4b5c6d7e8f90a1b2c3d4e5f60718293a4b5c6d7e8f90"

# --- raw-форма ---------------------------------------------------------------
check("raw принимается", gs.normalize_ton_address(f"0:{H}") == f"0:{H}")
check("raw в верхнем регистре приводится к нижнему",
      gs.normalize_ton_address(f"0:{H.upper()}") == f"0:{H}")
check("masterchain (-1) принимается", gs.normalize_ton_address(f"-1:{H}") == f"-1:{H}")

# --- round-trip friendly -> raw ---------------------------------------------
friendly = make_friendly_address(0, H)
check("friendly декодируется в тот же raw",
      gs.normalize_ton_address(friendly) == f"0:{H}",
      f"got={gs.normalize_ton_address(friendly)}")

# Ключевой кейс: две формы ОДНОГО адреса должны совпасть после нормализации.
check("raw и friendly одного адреса эквивалентны",
      gs.normalize_ton_address(friendly) == gs.normalize_ton_address(f"0:{H}"))

# non-bounceable (UQ...) — тот же адрес, другой флаг.
check("non-bounceable форма даёт тот же raw",
      gs.normalize_ton_address(make_friendly_address(0, H, bounceable=False)) == f"0:{H}")

# base64 в обычном алфавите (+/) вместо url-safe (-_).
std_b64 = friendly.replace("-", "+").replace("_", "/")
check("обычный base64-алфавит тоже принимается",
      gs.normalize_ton_address(std_b64) == f"0:{H}")

check("masterchain friendly round-trip",
      gs.normalize_ton_address(make_friendly_address(-1, H)) == f"-1:{H}")

# --- отказы ------------------------------------------------------------------
broken = list(base64.urlsafe_b64decode(friendly))
broken[10] ^= 0xFF                                    # портим хэш -> CRC не сойдётся
bad_crc = base64.urlsafe_b64encode(bytes(broken)).decode()
check("битый CRC отвергается", gs.normalize_ton_address(bad_crc) is None)

check("мусор отвергается", gs.normalize_ton_address("не-адрес") is None)
check("пустая строка отвергается", gs.normalize_ton_address("") is None)
check("None отвергается", gs.normalize_ton_address(None) is None)
check("короткий hex в raw отвергается", gs.normalize_ton_address("0:abcd") is None)


# =============================================================================
print("\n[2] Whitelist коллекций")
# =============================================================================

other_hash = "00" * 32
orig_whitelist = gs.COLLECTION_WHITELIST

gs.COLLECTION_WHITELIST = []
check("пустой whitelist пропускает всё", gs.is_collection_trusted(f"0:{H}") is True)

# Самое важное: в whitelist friendly-форма, а с рынка приходит raw.
gs.COLLECTION_WHITELIST = [friendly]
check("friendly в whitelist матчит raw с рынка",
      gs.is_collection_trusted(f"0:{H}") is True)
check("чужая коллекция отклоняется",
      gs.is_collection_trusted(f"0:{other_hash}") is False)
check("пустой адрес коллекции отклоняется при активном whitelist",
      gs.is_collection_trusted("") is False)

gs.COLLECTION_WHITELIST = orig_whitelist


# =============================================================================
print("\n[3] Экономика сделки")
# =============================================================================

floor = Decimal("10")
buy = Decimal("8")

sale = gs.target_sale_price(floor)
check("undercut опускает цену продажи ниже floor", sale < floor, f"sale={sale}")

profit = gs.compute_net_profit(floor, buy)

# Считаем вручную по той же модели, чтобы поймать опечатку в реализации.
expected_sale = floor * (Decimal("1") - gs.UNDERCUT_PCT)
expected = (expected_sale * (Decimal("1") - gs.MARKETPLACE_FEE_PCT - gs.ROYALTY_PCT)
            - buy - gs.GAS_FEE_TON)
check("прибыль совпадает с ручным расчётом", profit == expected,
      f"got={profit} expected={expected}")

# Главное: новая формула обязана быть КОНСЕРВАТИВНЕЕ старой из ТЗ.
old_formula = (floor - buy) - (buy * Decimal("0.05")) - Decimal("0.15")
check("новая формула строго консервативнее старой", profit < old_formula,
      f"new={profit:.4f} old={old_formula:.4f}")

check("покупка по floor убыточна", gs.compute_net_profit(floor, floor) < 0)
check("покупка дороже floor убыточна", gs.compute_net_profit(floor, floor * 2) < 0)
check("глубокая скидка прибыльна", gs.compute_net_profit(floor, Decimal("5")) > 0)

check("ROI при нулевой цене не делит на ноль",
      gs.compute_roi_pct(Decimal("1"), Decimal("0")) == Decimal("0"))


# =============================================================================
print("\n[4] Floor: перцентиль и устойчивость к выбросам")
# =============================================================================

prices = sorted(Decimal(str(p)) for p in [10, 11, 12, 13, 14, 15, 16, 17, 18, 19])
check("P0 == минимум", gs._percentile(prices, Decimal("0")) == Decimal("10"))
check("P100 == максимум", gs._percentile(prices, Decimal("100")) == Decimal("19"))
check("P50 между краями",
      Decimal("14") <= gs._percentile(prices, Decimal("50")) <= Decimal("15"))

# Ключевая причина отказа от min(): один "пылевой" лот не должен ломать floor.
with_outlier = sorted(prices + [Decimal("0.01")])
p5 = gs._percentile(with_outlier, Decimal("5"))
check("выброс 0.01 не утаскивает P5 на дно", p5 > Decimal("1"), f"P5={p5}")
check("а min() именно это и сделал бы", min(with_outlier) == Decimal("0.01"))

check("пустая выборка -> 0", gs._percentile([], Decimal("5")) == Decimal("0"))
check("один элемент -> он сам",
      gs._percentile([Decimal("7")], Decimal("5")) == Decimal("7"))


# =============================================================================
print("\n[5] Красивые номера минта")
# =============================================================================

check("mint 1 красивый (топ-100)", gs.is_pretty_mint(1) is True)
check("mint 100 красивый", gs.is_pretty_mint(100) is True)
check("mint 777 красивый", gs.is_pretty_mint(777) is True)
check("mint 4242 обычный", gs.is_pretty_mint(4242) is False)
check("mint None обычный", gs.is_pretty_mint(None) is False)


# =============================================================================
print("\n" + "=" * 60)
if _failures:
    print(f"ПРОВАЛЕНО: {len(_failures)} проверок -> {_failures}")
    sys.exit(1)
print("ВСЕ ПРОВЕРКИ ПРОЙДЕНЫ")
sys.exit(0)
