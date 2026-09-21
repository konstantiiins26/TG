#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Быстрые локальные проверки чистой логики снайпера (без сети).

Запуск:  python test_gift_sniper.py

Покрывает то, что можно проверить детерминированно:
  * нормализацию адресов TON (raw <-> user-friendly, CRC16),
  * работу whitelist,
  * экономику сделки (комиссии с цены продажи, роялти, undercut),
  * перцентиль floor и его устойчивость к выбросам,
  * извлечение трейтов и оценку редкости по выборке,
  * ликвидность (конкуренция у floor),
  * дедупликацию с учётом цены и сдвига floor.

Сетевые вызовы (TonAPI / Getgems / Claude) здесь НЕ проверяются.
"""

import base64
import sys
import time
from decimal import Decimal

import gift_sniper as gs


# =============================================================================
# ГЕРМЕТИЧНОСТЬ: тесты не должны зависеть от окружения пользователя
# =============================================================================
# Все настройки бота читаются из переменных окружения при импорте. Если
# запустить тесты в том же окне cmd, где стоял `set BANKROLL_TON=10`, проверки
# начинают мерить чужую конфигурацию вместо кода — и падают на исправном боте.
# Реальный случай: BANKROLL_TON=10 при позиции 10 TON и резерве 1 давал
# available_bankroll = −1, из-за чего «нормальная сделка проходит» проваливалась.
#
# Поэтому фиксируем ВСЕ настраиваемые величины на документированные значения
# по умолчанию. Секции, которым нужны другие, меняют их локально и возвращают
# обратно. «Тесты прошли» обязано означать одно и то же на любой машине.
_PINNED = {
    # экономика
    "MARKETPLACE_FEE_PCT": Decimal("0.02"), "ROYALTY_PCT": Decimal("0.05"),
    "UNDERCUT_PCT": Decimal("0.03"), "GAS_FEE_TON": Decimal("0.15"),
    "MIN_ROI_PCT": Decimal("5"), "PREMIUM_MULT": Decimal("1.0"),
    # floor и выборка
    "FLOOR_PAGE_SIZE": 100, "FLOOR_SAMPLE_PAGES": 5,
    "FLOOR_PERCENTILE": Decimal("5"), "MIN_FLOOR_SAMPLE": 40,
    "FLOOR_CACHE_TTL_SEC": 60, "CANDIDATES_TO_ANALYZE": 5,
    # редкость и похожие лоты
    "RARE_TRAIT_THRESHOLD_PCT": Decimal("5"), "MIN_TRAIT_SAMPLE": 50,
    "PEER_TRAIT": "model", "MIN_PEER_SAMPLE": 4,
    "DEEP_DISCOUNT_PCT": Decimal("60"),
    # ликвидность и дедупликация
    "COMPETITION_BAND_PCT": Decimal("10"), "MAX_COMPETITION": 15,
    "ENABLE_SALES_HISTORY": False, "LIQUIDITY_WINDOW_HOURS": 72,
    "MIN_SALES_IN_WINDOW": 1, "SEEN_TTL_SEC": 300,
    # риск-лимиты и банк
    "MAX_SPEND_PER_TRADE_TON": Decimal("50"),
    "MAX_SPEND_PER_HOUR_TON": Decimal("200"),
    "MAX_SPEND_PER_DAY_TON": Decimal("1000"),
    "MAX_OPEN_POSITIONS": 10, "STOP_AFTER_LOSSES": 3,
    "BANKROLL_TON": Decimal("0"), "RESERVE_TON": Decimal("5"),
    "MAX_POSITION_PCT": Decimal("10"), "HIGH_ROI_PCT": Decimal("100"),
    "MAX_POSITION_PCT_HIGH_ROI": Decimal("10"),
    # выход из позиции и бэктест
    "ENABLE_STOP_LOSS": True, "STOP_LOSS_PCT": Decimal("25"),
    "STOP_LOSS_MIN_HOURS": Decimal("6"), "BACKTEST_HOLD_HOURS": 24,
    "BACKTEST_MAX_SLACK_HOURS": Decimal("6"),
    # сеть и режимы
    "TONAPI_MIN_INTERVAL": Decimal("1.1"), "TONAPI_MAX_RETRIES": 3,
    "TONAPI_DAILY_BUDGET": 10000, "POLL_INTERVAL_SEC": 12,
    "PURCHASE_GAS_TON": Decimal("0.3"), "TRADING_NETWORK": "testnet",
    "ALLOWED_MARKETS": ["Getgems Sales"],
    "DRY_RUN": True, "CONFIRM_LIVE_TRADING": "", "COLLECTION_WHITELIST": [],
    "TELEGRAM_BOT_TOKEN": "", "TELEGRAM_CHAT_ID": "", "HEARTBEAT_MIN": 60,
}
for _name, _value in _PINNED.items():
    setattr(gs, _name, _value)


_failures = []


def source_default(var_name):
    """
    Достаёт значение по умолчанию из `os.getenv("VAR", "default")` в исходнике.

    Нужно там, где тест фиксирует ПРОЕКТНОЕ РЕШЕНИЕ («комиссия 2%»,
    «механизм выключен»), а не текущую настройку. Сравнивать с глобальной
    переменной нельзя: она берётся из окружения, и у пользователя со своими
    `set ...` тест падал бы на верном коде.
    """
    import re as _re
    src = open("gift_sniper.py", encoding="utf-8").read()
    m = _re.search(rf'os\.getenv\(\s*"{_re.escape(var_name)}"\s*,\s*"([^"]*)"', src)
    if m is None:
        raise AssertionError(f"не нашёл значение по умолчанию для {var_name}")
    return m.group(1)


def source_default_const(name):
    """
    То же для КОНСТАНТЫ в исходнике (`NAME = value`), а не переменной окружения.

    Нужно для флагов, которые настройкой быть не должны: «продажа
    реализована» — утверждение о коде, и возможность выставить его через
    окружение превратила бы ворота в формальность.
    """
    import re as _re
    src = open("gift_sniper.py", encoding="utf-8").read()
    m = _re.search(rf'^{_re.escape(name)}\s*=\s*(\S+)', src, _re.M)
    if m is None:
        raise AssertionError(f"не нашёл константу {name} в исходнике")
    return m.group(1)


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
print("\n[6] Трейты и редкость")
# =============================================================================

meta_ok = {"attributes": [
    {"trait_type": "Model", "value": "Plush Pepe"},
    {"trait_type": "Backdrop", "value": "Onyx Black"},
    {"trait_type": "Number", "value": "4242"},      # служебный — не трейт
]}
traits = gs.extract_traits(meta_ok)
check("трейты извлечены", traits == {"model": "Plush Pepe", "backdrop": "Onyx Black"},
      f"got={traits}")
check("номер минта не считается трейтом", "number" not in traits)
check("пустые метаданные -> пусто", gs.extract_traits({}) == {})

# Регрессия: подстрочный матч выбрасывал легитимные трейты, содержащие "id"
# ("Sidekick", "Rider"). Сравнение должно идти по словам.
tricky = gs.extract_traits({"attributes": [
    {"trait_type": "Sidekick", "value": "Cat"},
    {"trait_type": "Rider", "value": "Knight"},
    {"trait_type": "Mint Number", "value": "7"},   # служебный, по слову
    {"trait_type": "Serial ID", "value": "9"},     # служебный, по слову
]})
check("'Sidekick' не выброшен из-за подстроки 'id'", "sidekick" in tricky, f"got={tricky}")
check("'Rider' не выброшен из-за подстроки 'id'", "rider" in tricky)
check("'Mint Number' выброшен по слову", "mint number" not in tricky)
check("'Serial ID' выброшен по слову", "serial id" not in tricky)

# --- Явная редкость от площадки приоритетнее нашей оценки --------------------
check("явная редкость читается",
      gs._explicit_rarity_pct({"attributes": [{"trait_type": "Rarity", "value": "1.5%"}]})
      == Decimal("1.5"))
check("бессмысленная редкость игнорируется",
      gs._explicit_rarity_pct({"attributes": [{"trait_type": "Rarity", "value": "нет"}]})
      is None)

# --- Оценка редкости по выборке ---------------------------------------------
def mk(model, backdrop, price=1.0, addr="x"):
    return gs._normalize_item(addr, "C", "0:" + "11" * 32, 1, Decimal(str(price)), True,
                              traits={"model": model, "backdrop": backdrop})

# 99 обычных + 1 редкий => редкий трейт у 1% выборки.
sample = [mk("Common", "Blue", addr=f"a{i}") for i in range(99)]
sample.append(mk("Common", "Onyx", addr="rare"))
index, total = gs.build_trait_index(sample)
check("индекс трейтов посчитан", total == 100 and index["backdrop"]["Onyx"] == 1,
      f"total={total}")

rare_pct, rare_name = gs.compute_rarity_pct(sample[-1], index, total)
check("редкий лот -> 1%", rare_pct == Decimal("1.00"), f"got={rare_pct}")
check("назван редчайший трейт", rare_name == "backdrop", f"got={rare_name}")
check("редкий распознан", gs.is_rare(rare_pct) is True)

common_pct, _ = gs.compute_rarity_pct(sample[0], index, total)
check("обычный лот не редкий", gs.is_rare(common_pct) is False, f"pct={common_pct}")

# Ключевой кейс безопасности: "не удалось оценить" != "обычный" и != "редкий".
small_index, small_total = gs.build_trait_index(sample[:5])
unknown_pct, _ = gs.compute_rarity_pct(sample[0], small_index, small_total)
check("малая выборка -> редкость None", unknown_pct is None, f"got={unknown_pct}")
check("None НЕ считается редким", gs.is_rare(None) is False)


# =============================================================================
print("\n[7] Ликвидность: конкуренция у floor")
# =============================================================================

floor = Decimal("10")
# Полоса по умолчанию 10% => учитываются лоты <= 11.
prices = sorted(Decimal(str(p)) for p in [10, 10.5, 11, 12, 20])
comp = gs.compute_competition(prices, floor)
check("конкуренты в полосе посчитаны", comp == 3, f"got={comp}")
check("лоты далеко от floor не считаются", comp < len(prices))
check("нулевой floor -> нет конкуренции",
      gs.compute_competition(prices, Decimal("0")) == 0)


# =============================================================================
print("\n[8] Дедупликация")
# =============================================================================

gs._seen_cache.clear()
F = Decimal("10")
lot = {"address": "0:dead", "sale_price_ton": 5.0}
check("первый раз лот анализируется", gs.already_analyzed(lot, F) is False)
check("повтор того же лота пропускается", gs.already_analyzed(lot, F) is True)

# Смена цены — новое торговое событие, его нужно пересчитать.
check("смена цены снимает дедуп",
      gs.already_analyzed({"address": "0:dead", "sale_price_ton": 4.0}, F) is False)
check("другой лот не пропускается",
      gs.already_analyzed({"address": "0:beef", "sale_price_ton": 5.0}, F) is False)

# Ключевой кейс: реальный сдвиг floor обязан снять дедуп — лот, бывший SKIP
# при floor=10, может стать BUY при floor=15.
check("сдвиг floor снимает дедуп", gs.already_analyzed(lot, Decimal("15")) is False)
# ...но дрожание floor на доли процента не должно сбрасывать кэш каждый цикл.
check("дрожание floor не сбрасывает дедуп",
      gs.already_analyzed(lot, Decimal("15.001")) is True)
gs._seen_cache.clear()


# =============================================================================
print("\n[9] История продаж выключена по умолчанию")
# =============================================================================

# Эндпоинт не проверен из dev-среды, поэтому по умолчанию не должен ходить в сеть.
check("ENABLE_SALES_HISTORY выключен по умолчанию", gs.ENABLE_SALES_HISTORY is False)
check("выключенная история возвращает None (без сетевого вызова)",
      gs.fetch_recent_sales_count("0:" + "11" * 32) is None)


# =============================================================================
print("\n[10] Риск-лимиты и хранилище")
# =============================================================================

import os, tempfile, time as _time

_tmpdir = tempfile.mkdtemp()
gs.DB_PATH = os.path.join(_tmpdir, "test_state.db")
gs.db_init()

check("пустая БД: нет открытых позиций", gs.open_positions_count() == 0)
check("пустая БД: нет трат", gs.spend_since(3600) == Decimal("0"))
check("пустая БД: нет серии убытков", gs.consecutive_losses() == 0)

lot = {"address": "0:pos1", "collection_address": "0:coll"}
pid = gs.record_purchase(lot, Decimal("10"), Decimal("12"))
check("позиция записана", isinstance(pid, int) and pid > 0)
check("позиция считается открытой", gs.open_positions_count() == 1)
check("траты учтены", gs.spend_since(3600) == Decimal("10"))

# --- Потолок одной сделки ----------------------------------------------------
ok, why = gs.check_risk_limits(gs.MAX_SPEND_PER_TRADE_TON + Decimal("1"))
check("дорогая сделка блокируется", ok is False, why)
ok, _ = gs.check_risk_limits(Decimal("1"))
check("нормальная сделка проходит", ok is True)

# --- Часовой лимит -----------------------------------------------------------
_orig_hour = gs.MAX_SPEND_PER_HOUR_TON
gs.MAX_SPEND_PER_HOUR_TON = Decimal("12")   # уже потрачено 10
ok, why = gs.check_risk_limits(Decimal("5"))
check("часовой лимит блокирует перерасход", ok is False, why)
ok, _ = gs.check_risk_limits(Decimal("1"))
check("в пределах часового лимита проходит", ok is True)
gs.MAX_SPEND_PER_HOUR_TON = _orig_hour

# --- Лимит открытых позиций --------------------------------------------------
_orig_open = gs.MAX_OPEN_POSITIONS
gs.MAX_OPEN_POSITIONS = 1
ok, why = gs.check_risk_limits(Decimal("1"))
check("лимит открытых позиций блокирует", ok is False, why)
gs.MAX_OPEN_POSITIONS = _orig_open

# --- PnL и серия убытков -----------------------------------------------------
pnl = gs.close_position(pid, Decimal("20"))
check("PnL посчитан при закрытии", pnl is not None and pnl > 0, f"pnl={pnl}")
check("закрытая позиция больше не открыта", gs.open_positions_count() == 0)

# Ключевой кейс: N убытков подряд обязаны остановить торговлю.
for i in range(gs.STOP_AFTER_LOSSES):
    lid = gs.record_purchase({"address": f"0:loss{i}", "collection_address": ""},
                             Decimal("10"), Decimal("10"))
    gs.close_position(lid, Decimal("1"))     # продали сильно дешевле -> убыток
    _time.sleep(0.01)                        # чтобы sell_ts различались
check("серия убытков посчитана",
      gs.consecutive_losses() >= gs.STOP_AFTER_LOSSES, f"got={gs.consecutive_losses()}")
ok, why = gs.check_risk_limits(Decimal("1"))
check("торговля остановлена после серии убытков", ok is False, why)

# Прибыльная сделка обязана сбросить серию.
wid = gs.record_purchase({"address": "0:win", "collection_address": ""},
                         Decimal("1"), Decimal("10"))
gs.close_position(wid, Decimal("50"))
check("прибыль сбрасывает серию убытков", gs.consecutive_losses() == 0)


# =============================================================================
print("\n[11] Уведомления не роняют торговлю")
# =============================================================================

_tok, _chat = gs.TELEGRAM_BOT_TOKEN, gs.TELEGRAM_CHAT_ID
gs.TELEGRAM_BOT_TOKEN = gs.TELEGRAM_CHAT_ID = ""
try:
    gs.notify("тест")
    check("без токена notify() молча ничего не делает", True)
except Exception as e:
    check("без токена notify() молча ничего не делает", False, str(e))

# С битым токеном сеть недоступна — notify обязан проглотить ошибку.
gs.TELEGRAM_BOT_TOKEN, gs.TELEGRAM_CHAT_ID = "bad", "bad"
try:
    gs.notify("тест")
    check("сетевая ошибка в notify() не пробрасывается", True)
except Exception as e:
    check("сетевая ошибка в notify() не пробрасывается", False, str(e))
gs.TELEGRAM_BOT_TOKEN, gs.TELEGRAM_CHAT_ID = _tok, _chat


# =============================================================================
print("\n[12] evaluate_trade — единая функция решения")
# =============================================================================

def mk_snap(floor, competition=0, reliable=True, index=None, total=0):
    return {"floor": Decimal(str(floor)), "competition": competition,
            "floor_reliable": reliable, "trait_index": index or {}, "trait_total": total}

def mk_lot(price, mint=5000, addr="0:t", traits=None):
    return gs._normalize_item(addr, "C", "0:coll", mint, Decimal(str(price)), True,
                              traits=traits or {})

# Глубокая скидка при надёжном floor — покупаем.
ev = gs.evaluate_trade(mk_lot(5), mk_snap(10), None)
check("выгодная сделка разрешена", ev["allowed"] is True, ev["reason"])

# Каждая причина отказа срабатывает отдельно.
check("недостоверный floor блокирует",
      gs.evaluate_trade(mk_lot(5), mk_snap(10, reliable=False), None)["allowed"] is False)
check("убыточная сделка блокируется",
      gs.evaluate_trade(mk_lot(10), mk_snap(10), None)["allowed"] is False)
check("толпа у floor блокирует",
      gs.evaluate_trade(mk_lot(5), mk_snap(10, competition=999), None)["allowed"] is False)
check("мёртвый рынок блокирует",
      gs.evaluate_trade(mk_lot(5), mk_snap(10), 0)["allowed"] is False)

# Покупка выше floor без премии всегда убыточна — отдельной проверки
# "переплата" нет намеренно, расчёт прибыли её уже содержит.
over = gs.evaluate_trade(mk_lot(11, mint=4242), mk_snap(10), None)
check("покупка выше floor блокируется", over["allowed"] is False)
check("причина — убыточность (переплата поглощена расчётом)",
      "убыточно" in over["reason"], over["reason"])

# --- Премия за красоту/редкость ---------------------------------------------
# По умолчанию нейтральна: красота и редкость НЕ должны тихо завышать прибыль.
check("премия по умолчанию выключена (1.0)",
      gs.PREMIUM_MULT == Decimal("1.0"), f"got={gs.PREMIUM_MULT}")
check("при нейтральной премии красота не меняет расчёт",
      gs.compute_net_profit(Decimal("10"), Decimal("5"), premium=True)
      == gs.compute_net_profit(Decimal("10"), Decimal("5"), premium=False))

_orig_prem = gs.PREMIUM_MULT
gs.PREMIUM_MULT = Decimal("2.0")
rare_index, rare_total = {"backdrop": {"Onyx": 1, "Blue": 999}}, 1000
snap_r = mk_snap(10, index=rare_index, total=rare_total)

ev_rare = gs.evaluate_trade(mk_lot(11, mint=4242, traits={"backdrop": "Onyx"}),
                            snap_r, None)
check("с премией редкий лот выше floor покупается",
      ev_rare["allowed"] is True, ev_rare["reason"])
check("редкость распознана", ev_rare["is_rare"] is True)
check("отмечено, что премия применена", ev_rare["premium_applied"] is True)

ev_pretty = gs.evaluate_trade(mk_lot(11, mint=777, traits={"backdrop": "Blue"}),
                              snap_r, None)
check("красивый номер тоже получает премию", ev_pretty["allowed"] is True,
      ev_pretty["reason"])

ev_plain = gs.evaluate_trade(mk_lot(11, mint=4242, traits={"backdrop": "Blue"}),
                             snap_r, None)
check("обычный лот премии НЕ получает и остаётся убыточным",
      ev_plain["allowed"] is False, ev_plain["reason"])
gs.PREMIUM_MULT = _orig_prem


# =============================================================================
print("\n[13] Бэктест: сквозной прогон по синтетической записи")
# =============================================================================

import json as _json

rec_path = os.path.join(_tmpdir, "hist.jsonl")
now_ts = _time.time()

# Сценарий: floor стабильно 10, дешёвый лот за 5 -> сделка должна найтись,
# а через 24ч floor тот же, значит прибыль реальна.
with open(rec_path, "w", encoding="utf-8") as f:
    for hour in range(0, 50):
        row = {"ts": now_ts + hour * 3600, "floor": "10", "sample_size": 100,
               "competition": 1, "floor_reliable": True,
               "trait_index": {}, "trait_total": 0,
               "candidates": [mk_lot(5, addr="0:deal")] if hour == 0 else []}
        f.write(_json.dumps(row) + "\n")

res = gs.run_backtest(rec_path, hold_hours=24)
check("бэктест нашёл сделку", res and res["trades"] == 1, str(res))
check("сделка закрылась", res and res["closed"] == 1, str(res))
check("сделка прибыльна", res and res["pnl"] > 0, str(res and res["pnl"]))
check("winrate 100%", res and res["winrate"] == Decimal("100"))

# Позиция без будущего в записи обязана остаться НЕзакрытой, а не считаться
# прибыльной — иначе бэктест подгоняет результат.
tail_path = os.path.join(_tmpdir, "tail.jsonl")
with open(tail_path, "w", encoding="utf-8") as f:
    f.write(_json.dumps({"ts": now_ts, "floor": "10", "sample_size": 100,
                         "competition": 1, "floor_reliable": True,
                         "trait_index": {}, "trait_total": 0,
                         "candidates": [mk_lot(5, addr="0:late")]}) + "\n")
res2 = gs.run_backtest(tail_path, hold_hours=24)
check("сделка без будущего не закрыта", res2 and res2["unresolved"] == 1, str(res2))
check("незакрытая сделка не попала в winrate", res2 and res2["closed"] == 0)

# Битые строки не должны ронять прогон.
broken_path = os.path.join(_tmpdir, "broken.jsonl")
with open(broken_path, "w", encoding="utf-8") as f:
    f.write("{не json\n")
    f.write(_json.dumps({"ts": now_ts, "floor": "10", "sample_size": 10,
                         "competition": 1, "floor_reliable": True,
                         "trait_index": {}, "trait_total": 0,
                         "candidates": []}) + "\n")
check("битая строка пропускается без падения",
      len(gs._load_recording(broken_path)) == 1)

check("отсутствующий файл обрабатывается",
      gs.run_backtest(os.path.join(_tmpdir, "нет.jsonl")) is None)


# =============================================================================
print("\n[14] Банкролл")
# =============================================================================

gs.DB_PATH = os.path.join(_tmpdir, "bankroll.db")
gs.db_init()

_ob, _or_, _op = gs.BANKROLL_TON, gs.RESERVE_TON, gs.MAX_POSITION_PCT
gs.BANKROLL_TON = Decimal("100")
gs.RESERVE_TON = Decimal("5")
gs.MAX_POSITION_PCT = Decimal("10")
gs.MAX_SPEND_PER_TRADE_TON = Decimal("50")

check("свободно = банк − резерв при нуле позиций",
      gs.available_bankroll() == Decimal("95"), f"got={gs.available_bankroll()}")
check("потолок сделки = 10% банка (меньше абсолютного)",
      gs.max_position_size() == Decimal("10"), f"got={gs.max_position_size()}")

gs.record_purchase({"address": "0:b1", "collection_address": ""}, Decimal("30"), Decimal("40"))
check("капитал в позициях учтён", gs.deployed_capital() == Decimal("30"))
check("свободно уменьшилось на размер позиции",
      gs.available_bankroll() == Decimal("65"), f"got={gs.available_bankroll()}")

# Резерв неприкосновенен: он не должен уходить в сделки.
gs.record_purchase({"address": "0:b2", "collection_address": ""}, Decimal("60"), Decimal("70"))
check("резерв не отдаётся под сделки",
      gs.available_bankroll() == Decimal("5"), f"got={gs.available_bankroll()}")
ok, why = gs.check_risk_limits(Decimal("9"))
check("сделка сверх свободных средств блокируется", ok is False, why)

# Доля банка должна ограничивать сильнее абсолютного лимита.
gs.BANKROLL_TON = Decimal("20")
check("потолок = 10% от 20 = 2", gs.max_position_size() == Decimal("2"))
ok, why = gs.check_risk_limits(Decimal("5"))
check("превышение доли банка блокируется", ok is False, why)

# Без заданного банка доля не применяется — работает абсолютный лимит.
gs.BANKROLL_TON = Decimal("0")
check("без банка действует абсолютный лимит",
      gs.max_position_size() == gs.MAX_SPEND_PER_TRADE_TON)
check("без банка свободных средств нет", gs.available_bankroll() == Decimal("0"))

gs.BANKROLL_TON, gs.RESERVE_TON, gs.MAX_POSITION_PCT = _ob, _or_, _op


# =============================================================================
print("\n[15] Кошелёк: права на файл ключа")
# =============================================================================

import stat as _stat

_owk = gs.WALLET_KEY_FILE
key_path = os.path.join(_tmpdir, "wallet.key")
with open(key_path, "w", encoding="utf-8") as f:
    f.write("SECRET_KEY_MATERIAL_DO_NOT_LEAK\n")

gs.WALLET_KEY_FILE = ""
key, err = gs.load_wallet_key()
check("без пути ключ не загружается", key is None and err is not None)

gs.WALLET_KEY_FILE = os.path.join(_tmpdir, "нет.key")
key, err = gs.load_wallet_key()
check("отсутствующий файл ключа отвергается", key is None and "не найден" in err)

gs.WALLET_KEY_FILE = key_path

if os.name == "nt":
    # На Windows POSIX-битов нет: os.chmod() управляет только флагом "только
    # чтение", и st_mode всегда 0o666 либо 0o444. Проверка прав отвергала бы
    # ЛЮБОЙ файл, поэтому там она заменена на громкое предупреждение.
    print("  --   Windows: проверки POSIX-прав пропущены (битов нет)")
    key, err = gs.load_wallet_key()
    check("на Windows ключ читается, несмотря на отсутствие POSIX-прав",
          key == "SECRET_KEY_MATERIAL_DO_NOT_LEAK", f"err={err}")
else:
    # Ключевой кейс безопасности: читаемый другими ключ обязан быть отвергнут.
    os.chmod(key_path, 0o644)
    key, err = gs.load_wallet_key()
    check("ключ с правами 0644 отвергается", key is None, f"err={err}")
    check("в ошибке названа причина и лечение",
          err and "chmod 600" in err, err)
    check("сам ключ в текст ошибки НЕ попал",
          err and "SECRET_KEY_MATERIAL" not in err, err)

    os.chmod(key_path, 0o600)
    key, err = gs.load_wallet_key()
    check("ключ с правами 0600 читается",
          key == "SECRET_KEY_MATERIAL_DO_NOT_LEAK", f"err={err}")

    os.chmod(key_path, 0o660)          # доступен группе
    key, err = gs.load_wallet_key()
    check("ключ, доступный группе, отвергается", key is None)
    os.chmod(key_path, 0o600)

gs.WALLET_KEY_FILE = _owk


# =============================================================================
print("\n[16] Ворота в живую торговлю")
# =============================================================================

_odry, _oconf, _oexec = gs.DRY_RUN, gs.CONFIRM_LIVE_TRADING, gs.REAL_EXECUTOR_AVAILABLE
_okey, _obank, _owl = gs.WALLET_KEY_FILE, gs.BANKROLL_TON, gs.COLLECTION_WHITELIST
_oapi = gs.ANTHROPIC_API_KEY
gs.ANTHROPIC_API_KEY = "sk-ant-test"
gs.TARGET_COLLECTIONS = [friendly]

# Симуляция должна проходить даже без кошелька и банка.
gs.DRY_RUN = True
check("режим симуляции не требует кошелька и банка",
      gs.preflight_checks(require_ai=True) is True)

# Исполнитель теперь определяется УСПЕХОМ ИМПОРТА tonutils, а не константой.
# Смысл проверки прежний: если библиотеки нет, живой режим обязан быть закрыт
# ЗАРАНЕЕ — иначе бот стартует нормально и упадёт в момент покупки, то есть
# узнает о проблеме тогда, когда реагировать уже поздно.
gs.DRY_RUN = False
gs.CONFIRM_LIVE_TRADING = "I_UNDERSTAND_THE_RISK"
gs.BANKROLL_TON = Decimal("100")
gs.COLLECTION_WHITELIST = [friendly]
gs.WALLET_KEY_FILE = key_path
_real_exec = gs.REAL_EXECUTOR_AVAILABLE
gs.REAL_EXECUTOR_AVAILABLE = False
check("без библиотеки подписи живой режим заблокирован",
      gs.preflight_checks(require_ai=True) is False)
gs.REAL_EXECUTOR_AVAILABLE = _real_exec

check("флаг исполнителя отражает фактический импорт, а не константу",
      gs.REAL_EXECUTOR_AVAILABLE == (gs._EXECUTOR_IMPORT_ERROR == ""),
      f"{gs.REAL_EXECUTOR_AVAILABLE} / {gs._EXECUTOR_IMPORT_ERROR!r}")

# И даже с исполнителем — без явного подтверждения риска.
gs.REAL_EXECUTOR_AVAILABLE = True
gs.CONFIRM_LIVE_TRADING = ""
check("живой режим заблокирован без подтверждения риска",
      gs.preflight_checks(require_ai=True) is False)

gs.CONFIRM_LIVE_TRADING = "I_UNDERSTAND_THE_RISK"
gs.BANKROLL_TON = Decimal("0")
check("живой режим заблокирован без заданного банка",
      gs.preflight_checks(require_ai=True) is False)

gs.BANKROLL_TON = Decimal("100")
gs.COLLECTION_WHITELIST = []
check("живой режим заблокирован с пустым whitelist",
      gs.preflight_checks(require_ai=True) is False)

# Самое важное: покупка НИКОГДА не рапортует успех, не совершив сделку.
gs.REAL_EXECUTOR_AVAILABLE = False
gs.DRY_RUN = False
check("нереализованная покупка возвращает False, а не ложный успех",
      gs.execute_blockchain_buy("0:x", Decimal("1"), "0:sale", "Getgems Sales") is False)
gs.DRY_RUN = True
check("в симуляции покупка возвращает True",
      gs.execute_blockchain_buy("0:x", Decimal("1"), "0:sale", "Getgems Sales") is True)

(gs.DRY_RUN, gs.CONFIRM_LIVE_TRADING, gs.REAL_EXECUTOR_AVAILABLE) = (_odry, _oconf, _oexec)
(gs.WALLET_KEY_FILE, gs.BANKROLL_TON, gs.COLLECTION_WHITELIST) = (_okey, _obank, _owl)
gs.ANTHROPIC_API_KEY = _oapi


# =============================================================================
print("\n[17] Несколько коллекций")
# =============================================================================

_otc, _oak = gs.TARGET_COLLECTIONS, gs.ANTHROPIC_API_KEY
gs.ANTHROPIC_API_KEY = "sk-ant-test"     # секция [16] вернула исходный (пустой)
second = make_friendly_address(0, "b2" * 32)

gs.TARGET_COLLECTIONS = [friendly, second]
check("preflight принимает несколько валидных коллекций",
      gs.preflight_checks(require_ai=True) is True)

# Одна битая коллекция в списке обязана остановить старт: иначе бот молча
# работал бы по части списка, а пользователь думал бы, что смотрит всё.
gs.TARGET_COLLECTIONS = [friendly, "не-адрес"]
check("битый адрес в списке блокирует старт",
      gs.preflight_checks(require_ai=True) is False)

gs.TARGET_COLLECTIONS = []
check("пустой список коллекций блокирует старт",
      gs.preflight_checks(require_ai=True) is False)

gs.TARGET_COLLECTIONS, gs.ANTHROPIC_API_KEY = _otc, _oak


# =============================================================================
print("\n[18] Ранжирование коллекций по активности")
# =============================================================================

rank_path = os.path.join(_tmpdir, "rank.jsonl")
_now = _time.time()
_rows = []
for h in range(48):
    # Живая: дешёвые лоты каждый час новые.
    _rows.append({"ts": _now + h*3600, "collection": "0:alive", "floor": "10",
                  "sample_size": 200, "competition": 3, "floor_reliable": True,
                  "trait_index": {}, "trait_total": 0,
                  "candidates": [{"address": f"0:lot{h}", "sale_price_ton": 8.0}]})
    # Замершая: лот всегда один и тот же.
    _rows.append({"ts": _now + h*3600, "collection": "0:frozen", "floor": "50",
                  "sample_size": 40, "competition": 1, "floor_reliable": True,
                  "trait_index": {}, "trait_total": 0,
                  "candidates": [{"address": "0:stuck", "sale_price_ton": 45.0}]})
    # Офлайн: данных нет.
    _rows.append({"ts": _now + h*3600, "collection": "0:dead", "floor": "0",
                  "sample_size": 0, "competition": 0, "floor_reliable": False,
                  "trait_index": {}, "trait_total": 0, "candidates": []})
with open(rank_path, "w", encoding="utf-8") as f:
    for r in _rows:
        f.write(_json.dumps(r) + "\n")

ranked = gs.rank_collections(rank_path)
check("ранжирование вернуло все коллекции", ranked and len(ranked) == 3, str(ranked and len(ranked)))

by_name = {st["collection"]: st for st in ranked}
check("живая коллекция распознана", by_name["0:alive"]["status"] == "живая",
      by_name["0:alive"]["status"])
check("замершая отличена от живой", by_name["0:frozen"]["status"] == "замерла (оборота нет)",
      by_name["0:frozen"]["status"])
check("офлайн отличён от замершей", by_name["0:dead"]["status"] == "офлайн (нет данных)",
      by_name["0:dead"]["status"])

# Ключевое: у живой оборот строго выше, и она идёт первой в рейтинге.
check("у живой оборот больше нуля", by_name["0:alive"]["turnover_per_hour"] > 0)
check("у замершей оборот ноль", by_name["0:frozen"]["turnover_per_hour"] == 0)
check("рейтинг отсортирован по обороту", ranked[0]["collection"] == "0:alive",
      ranked[0]["collection"])

# Наличие лотов НЕ означает активность — замершая держит 40 лотов и мертва.
check("много лотов не делает коллекцию живой",
      by_name["0:frozen"]["avg_listings"] == 40
      and by_name["0:frozen"]["status"] != "живая")

check("отсутствующий файл обрабатывается",
      gs.rank_collections(os.path.join(_tmpdir, "нет.jsonl")) is None)


# =============================================================================
print("\n[19] Парсинг НАСТОЯЩЕГО ответа TonAPI")
# =============================================================================

# Дословный фрагмент реального ответа
# GET /v2/nfts/collections/EQC212djrq0gglQXi8MSFX1bcw4LHw3Es62lKvt1lZzzsYuF/items
# (коллекция Telegram Gifts "Timeless Books", получен 17.09.2026).
# Эти проверки — единственное в наборе, что подтверждено живыми данными,
# а не моими предположениями о форме ответа.
REAL_NFT = {
    "address": "0:e8ff70dd4fe2a1c3de574bef08678f0a7de9c27c763dd844e975b6670f8011c7",
    # ВНИМАНИЕ: верхнеуровневый index — НЕ номер минта. Здесь он огромный и
    # отрицательный. Номер лежит только в metadata.name ("... #16450").
    "index": -181007727810587533,
    "owner": {"address": "0:158136239adb15dd59df90c641f9efd312cfeb8664f218f4c3e5fce9d95e6c07",
              "name": "Fragment Gift Minter", "is_scam": False, "is_wallet": True},
    "collection": {
        "address": "0:b6d76763aead208254178bc312157d5b730e0b1f0dc4b3ada52afb75959cf3b1",
        "name": "Timeless Books"},
    "verified": True,
    "metadata": {
        "attributes": [{"trait_type": "Model", "value": "Cookbook"},
                       {"trait_type": "Backdrop", "value": "Malachite"},
                       {"trait_type": "Symbol", "value": "Apple"}],
        "name": "Timeless Book #16450",
        "image": "https://nft.fragment.com/gift/timelessbook-16450.webp"},
    "approved_by": ["getgems"],
    "trust": "whitelist",
}
_meta = REAL_NFT["metadata"]

check("номер минта берётся из имени, а не из index",
      gs._extract_mint_index(REAL_NFT, _meta) == 16450,
      f"got={gs._extract_mint_index(REAL_NFT, _meta)}")

# Регрессия: верхнеуровневый index — мусор. Если кто-то "починит" парсер,
# начав его использовать, номера минта станут бессмысленными.
check("огромный отрицательный index НЕ попадает в номер минта",
      gs._extract_mint_index(REAL_NFT, _meta) != REAL_NFT["index"])
check("номер минта в разумных пределах",
      0 < gs._extract_mint_index(REAL_NFT, _meta) < 1_000_000)

check("трейты разобраны",
      gs.extract_traits(_meta) == {"model": "Cookbook", "backdrop": "Malachite",
                                   "symbol": "Apple"},
      str(gs.extract_traits(_meta)))

check("адрес коллекции на месте",
      REAL_NFT["collection"]["address"].startswith("0:b6d7"))

_owl = gs.COLLECTION_WHITELIST
gs.COLLECTION_WHITELIST = ["EQC212djrq0gglQXi8MSFX1bcw4LHw3Es62lKvt1lZzzsYuF"]
check("whitelist по EQ-форме матчит raw-адрес из API",
      gs.is_collection_trusted(REAL_NFT["collection"]["address"]) is True)
gs.COLLECTION_WHITELIST = _owl

# Подтверждено на живых данных: процентов редкости в API НЕТ, хотя интерфейс
# Getgems их показывает. Значит, оценка по выборке — единственный путь.
check("явной редкости в ответе API нет",
      gs._explicit_rarity_pct(_meta) is None)

# Эти два предмета не выставлены — поля sale в ответе нет вообще.
check("без поля sale лот не считается продающимся",
      bool((REAL_NFT.get("sale") or {}).get("price")) is False)


# =============================================================================
print("\n[20] Парсинг ВЫСТАВЛЕННЫХ лотов (реальные sale из TonAPI)")
# =============================================================================

# Дословные блоки sale из того же ответа (limit=50): из 50 предметов
# выставлены были ровно 4. Они закрывают то, чего не было в секции [19]:
# цену, адрес контракта продажи и площадку.
#
# ВАЖНОЕ, подтверждённое этими данными: "Gram" в price.token_name — это
# НЕ отдельный жетон. Рядом стоят currency_type="native" и decimals=9,
# то есть это нативная монета TON, а значение — нанотоны. Интерфейс Getgems
# подписывает цены как GRAM, расчёт в коде ведётся в TON — это одно и то же.
REAL_SALE_RESPONSE = {"nft_items": [
    {   # Getgems Sales, 50 TON
        "address": "0:21fb89b58c779dd566b4eae44590542d76234bdbae879cf2ed0c97ecc328d08b",
        "collection": {"address": "0:b6d76763aead208254178bc312157d5b730e0b1f0dc4b3ada52afb75959cf3b1",
                       "name": "Timeless Books"},
        "metadata": {"name": "Timeless Book #45129",
                     "attributes": [{"trait_type": "Model", "value": "Cookbook"}]},
        "sale": {
            "address": "0:972df524a3aafd933ea65c6554f26915be8f26625dfac58406045af350aaa440",
            "market": {"address": "0:584ee61b2dff0837116d0fcb5078d93964bcbe9c05fd6a141b1bfca5d6a43e18",
                       "name": "Getgems Sales", "is_scam": False, "is_wallet": False},
            "price": {"currency_type": "native", "value": "50000000000",
                      "decimals": 9, "token_name": "Gram", "verification": "whitelist"}},
    },
    {   # Другая площадка: Marketapp Marketplace, 555 TON
        "address": "0:8f5e4206d3995f1fd595e738ec334ec1cc3f039758b5db362051bf08e0572dfc",
        "collection": {"address": "0:b6d76763aead208254178bc312157d5b730e0b1f0dc4b3ada52afb75959cf3b1",
                       "name": "Timeless Books"},
        "metadata": {"name": "Timeless Book #13398", "attributes": []},
        "sale": {
            "address": "0:626e7a4210c8240b42ec86cbd9380eb521d01bac71d1fd4fa98c52bfbaedf5f5",
            "market": {"address": "0:9a9cb80adfbd1662f5108766d73355ac2c03304fda1d25a479670e34efcd72b3",
                       "name": "Marketapp Marketplace", "is_scam": False, "is_wallet": True},
            "price": {"currency_type": "native", "value": "555000000000",
                      "decimals": 9, "token_name": "Gram", "verification": "whitelist"}},
    },
    {   # Getgems Sales, 150 TON
        "address": "0:89fd8449a17ae4f763652e3fb404b294a104ec217c9c6361c5e199c038416f8a",
        "collection": {"address": "0:b6d76763aead208254178bc312157d5b730e0b1f0dc4b3ada52afb75959cf3b1",
                       "name": "Timeless Books"},
        "metadata": {"name": "Timeless Book #21935", "attributes": []},
        "sale": {
            "address": "0:6b10025d989a8f18ededc97c0cd333386be9dcf54c42608b70b94c0160ffafe9",
            "market": {"address": "0:584ee61b2dff0837116d0fcb5078d93964bcbe9c05fd6a141b1bfca5d6a43e18",
                       "name": "Getgems Sales", "is_scam": False, "is_wallet": False},
            "price": {"currency_type": "native", "value": "150000000000",
                      "decimals": 9, "token_name": "Gram", "verification": "whitelist"}},
    },
    {   # Не выставлен: поля sale нет вовсе.
        "address": "0:e8ff70dd4fe2a1c3de574bef08678f0a7de9c27c763dd844e975b6670f8011c7",
        "collection": {"address": "0:b6d76763aead208254178bc312157d5b730e0b1f0dc4b3ada52afb75959cf3b1",
                       "name": "Timeless Books"},
        "metadata": {"name": "Timeless Book #16450", "attributes": []},
    },
]}


class _FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.headers = {}
        self.text = ""
        # `ok` нужен notify(): он проверяет статус ответа, потому что
        # requests.post на HTTP-ошибку исключения не бросает.
        self.ok = 200 <= status_code < 300

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _FakeRequests:
    """Подменяет только requests.get: сети в тестах нет и быть не должно."""

    def __init__(self, payload):
        self.payload = payload

    def get(self, *args, **kwargs):
        return _FakeResponse(self.payload)


_real_requests = gs.requests
_real_interval = gs.TONAPI_MIN_INTERVAL
gs.requests = _FakeRequests(REAL_SALE_RESPONSE)
gs.TONAPI_MIN_INTERVAL = Decimal("0")     # в тестах ждать нечего и некого
try:
    parsed = gs.fetch_items_tonapi("EQC212djrq0gglQXi8MSFX1bcw4LHw3Es62lKvt1lZzzsYuF", 50)
finally:
    gs.requests = _real_requests
    gs.TONAPI_MIN_INTERVAL = _real_interval

check("разобраны все 4 предмета", len(parsed) == 4, f"got={len(parsed)}")

on_sale = [i for i in parsed if i["is_on_sale"]]
check("выставленными считаются ровно 3", len(on_sale) == 3, f"got={len(on_sale)}")

check("нанотоны переводятся в TON",
      [i["sale_price_ton"] for i in on_sale] == [Decimal("50"), Decimal("555"), Decimal("150")],
      str([str(i["sale_price_ton"]) for i in on_sale]))

# Регрессия на главный пробел: платёж уходит на контракт продажи, а не на NFT.
check("адрес контракта продажи сохранён",
      on_sale[0]["sale_address"] ==
      "0:972df524a3aafd933ea65c6554f26915be8f26625dfac58406045af350aaa440",
      on_sale[0]["sale_address"])
check("адрес продажи НЕ совпадает с адресом предмета",
      all(i["sale_address"] != i["address"] for i in on_sale))

# Площадки разные, и протокол покупки у них может отличаться. Пока это только
# фиксируется в данных, но без этого поля различить их будет нечем.
check("площадка сохранена",
      [i["sale_market"] for i in on_sale] ==
      ["Getgems Sales", "Marketapp Marketplace", "Getgems Sales"],
      str([i["sale_market"] for i in on_sale]))

not_on_sale = [i for i in parsed if not i["is_on_sale"]][0]
check("у невыставленного лота адрес продажи пуст",
      not_on_sale["sale_address"] == "" and not_on_sale["sale_market"] == "")

check("номер минта разобран и у выставленных лотов",
      [i["mint_index"] for i in on_sale] == [45129, 13398, 21935],
      str([i["mint_index"] for i in on_sale]))

# --- Без адреса контракта продажи покупка невозможна в принципе -------------
# Проверка стоит ДО ветки DRY_RUN, поэтому симуляция тоже обязана отказать:
# "успешная" симуляция покупки, которую в живом режиме совершить нельзя,
# создаёт ложную уверенность в готовности бота.
_odry = gs.DRY_RUN
gs.DRY_RUN = True
try:
    check("покупка без адреса продажи отклоняется даже в DRY_RUN",
          gs.execute_blockchain_buy("0:item", Decimal("1"), "") is False)
    check("покупка с адресом продажи в DRY_RUN проходит",
          gs.execute_blockchain_buy("0:item", Decimal("1"), "0:sale",
                                    "Getgems Sales") is True)
finally:
    gs.DRY_RUN = _odry


# =============================================================================
print("\n[21] Лимит частоты TonAPI: пауза и ретраи на 429")
# =============================================================================

# Живой прогон 17.09.2026 поймал 429 на страницах 11-15 из 15: интервал цикла
# разносит ПАЧКИ запросов, а не запросы внутри пачки, а TonAPI без ключа
# лимитирует по секундам. Потерянная страница опасна не сама по себе —
# она молча прореживает выборку, а floor по тонкой выборке завышается.

class _SeqRequests:
    """Отдаёт заранее заданную последовательность ответов."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    def get(self, *args, **kwargs):
        self.calls += 1
        return self.responses.pop(0)


class _TooManyRequests(_FakeResponse):
    def __init__(self, retry_after=None, text="rate limit: too many requests"):
        super().__init__({}, status_code=429)
        self.text = text
        if retry_after is not None:
            self.headers = {"Retry-After": str(retry_after)}

    def raise_for_status(self):
        raise RuntimeError("429 Client Error: Too Many Requests")


_real_requests = gs.requests
_real_interval = gs.TONAPI_MIN_INTERVAL
_real_retries = gs.TONAPI_MAX_RETRIES
gs.TONAPI_MIN_INTERVAL = Decimal("0")
gs.TONAPI_MAX_RETRIES = 3
try:
    # 429 с Retry-After, затем успех: страница обязана прийти, а не потеряться.
    seq = _SeqRequests([_TooManyRequests(retry_after=0),
                        _FakeResponse(REAL_SALE_RESPONSE)])
    gs.requests = seq
    recovered = gs.fetch_items_tonapi("EQC212djrq0gglQXi8MSFX1bcw4LHw3Es62lKvt1lZzzsYuF", 50)
    check("после 429 запрос повторяется и страница приходит",
          len(recovered) == 4 and seq.calls == 2, f"calls={seq.calls}")

    # Лимит попыток конечен: бесконечно долбить API нельзя.
    seq = _SeqRequests([_TooManyRequests(retry_after=0) for _ in range(3)])
    gs.requests = seq
    try:
        gs.fetch_items_tonapi("EQC212djrq0gglQXi8MSFX1bcw4LHw3Es62lKvt1lZzzsYuF", 50)
        raised = _msg = False
    except gs.RateLimited as e:
        raised, _msg = True, str(e)
    check("непрерывный 429 в итоге бросает исключение, а не молчит", raised)
    # Сообщение обязано называть лечение: без ключа квота анонимного доступа
    # так мала, что «подождать» не помогает — нужен TONAPI_KEY.
    check("в сообщении названо лечение (ключ TonAPI)",
          _msg and "TONAPI_KEY" in _msg and "tonconsole" in _msg, str(_msg))
    check("попыток ровно TONAPI_MAX_RETRIES", seq.calls == 3, f"calls={seq.calls}")

    # Пауза между запросами реально выдерживается.
    gs.TONAPI_MIN_INTERVAL = Decimal("0.2")
    gs._tonapi_last_call = 0.0
    gs.requests = _SeqRequests([_FakeResponse(REAL_SALE_RESPONSE) for _ in range(3)])
    _t0 = time.monotonic()
    for _ in range(3):
        gs.fetch_items_tonapi("EQC212djrq0gglQXi8MSFX1bcw4LHw3Es62lKvt1lZzzsYuF", 50)
    _elapsed = time.monotonic() - _t0
    check("между запросами выдерживается пауза",
          _elapsed >= 0.4, f"elapsed={_elapsed:.2f}s")
finally:
    gs.requests = _real_requests
    gs.TONAPI_MIN_INTERVAL = _real_interval
    gs.TONAPI_MAX_RETRIES = _real_retries
    gs._tonapi_last_call = 0.0


# =============================================================================
print("\n[22] Прерывистая запись: дыра != закрытая позиция")
# =============================================================================

# Сценарий пользователя: ПК работает с утра до вечера, ночью выключен.
# Покупка вечером ищет floor через 24 часа — а ближайший снапшот находится
# только следующим утром. Без проверки зазора бэктест назвал бы результат
# 33-часового удержания результатом суточного, и шапка отчёта врала бы.
_snaps_gap = [
    {"ts": 1000.0, "floor": Decimal("5")},                    # покупка здесь
    {"ts": 1000.0 + 24 * 3600 + 3600, "floor": Decimal("6")},  # +25ч: зазор 1ч
]
_floor, _why = gs._future_floor(_snaps_gap, 1000.0 + 24 * 3600, max_slack_sec=6 * 3600)
check("снапшот в пределах допуска закрывает позицию",
      _floor == Decimal("6") and _why is None, f"{_floor} {_why}")

_snaps_hole = [
    {"ts": 1000.0, "floor": Decimal("5")},
    {"ts": 1000.0 + 33 * 3600, "floor": Decimal("6")},         # +33ч: зазор 9ч
]
_floor, _why = gs._future_floor(_snaps_hole, 1000.0 + 24 * 3600, max_slack_sec=6 * 3600)
check("слишком поздний снапшот НЕ закрывает позицию",
      _floor is None and _why == "gap", f"{_floor} {_why}")

# Конец записи и дыра — разные вещи: первое лечится ожиданием, второе нет.
_floor, _why = gs._future_floor([{"ts": 1000.0, "floor": Decimal("5")}],
                                1000.0 + 24 * 3600, max_slack_sec=6 * 3600)
check("конец записи отличается от дыры",
      _floor is None and _why == "end", f"{_floor} {_why}")

# Регрессия на подгонку: дыра не должна тихо превратиться в прибыль.
# Floor вырос с 5 до 6 — засчитав такую позицию, бэктест показал бы плюс,
# которого не было.
check("дыра не даёт бэктесту засчитать выгодный исход",
      gs._future_floor(_snaps_hole, 1000.0 + 24 * 3600,
                       max_slack_sec=6 * 3600)[0] is None)


# =============================================================================
print("\n[23] Доступность коллекции банку")
# =============================================================================

_ob, _or_, _op = gs.BANKROLL_TON, gs.RESERVE_TON, gs.MAX_POSITION_PCT
gs.BANKROLL_TON = Decimal("10")
gs.RESERVE_TON = Decimal("5")
gs.MAX_POSITION_PCT = Decimal("10")
try:
    # Граница выводится из формулы прибыли, а не подбирается: покупка ровно
    # по Buy_max даёт ноль, на копейку дороже — убыток.
    _floor = Decimal("4.72")
    _bmax = gs.max_profitable_buy(_floor)
    check("покупка по Buy_max даёт около нуля",
          abs(gs.compute_net_profit(_floor, _bmax)) < Decimal("0.000001"),
          str(gs.compute_net_profit(_floor, _bmax)))
    check("на копейку дороже Buy_max — уже убыток",
          gs.compute_net_profit(_floor, _bmax + Decimal("0.01")) < 0)

    # Тот самый случай пользователя: банк 10 TON против floor 4.72.
    _aff = gs.affordability(_floor)
    check("потолок сделки при банке 10 TON равен 1 TON",
          _aff["cap"] == Decimal("1"), str(_aff["cap"]))
    check("коллекция с floor 4.72 банку недоступна",
          _aff["verdict"] == "нет", _aff["verdict"])
    check("требуемая скидка честно огромная",
          _aff["discount_pct"] > 75, f"{_aff['discount_pct']:.0f}%")

    # Дешёвая коллекция доступна, но газ поднимает требуемую скидку —
    # «чем дешевле, тем лучше» неверно.
    check("floor 1.0 банку доступен",
          gs.affordability(Decimal("1.0"))["verdict"] == "да")
    check("газ делает совсем дешёвую коллекцию хуже средней",
          gs.affordability(Decimal("0.3"))["discount_pct"] >
          gs.affordability(Decimal("1.0"))["discount_pct"])

    # Без банка вердикта нет: молчаливое "да" отправило бы торговать вслепую.
    gs.BANKROLL_TON = Decimal("0")
    check("без заданного банка вердикт не выносится",
          gs.affordability(_floor)["verdict"] == "банк не задан")
    check("--afford без банка ничего не печатает",
          gs.show_affordability() is None)
finally:
    gs.BANKROLL_TON, gs.RESERVE_TON, gs.MAX_POSITION_PCT = _ob, _or_, _op


# =============================================================================
print("\n[24] Повышенный лимит для исключительно выгодных сделок")
# =============================================================================

# Читаем ЗНАЧЕНИЕ ПО УМОЛЧАНИЮ из исходника: у пользователя, включившего
# механизм своим `set`, глобальная переменная другая — и тест падал бы на
# совершенно верном коде.
# Решение: по умолчанию повышенный лимит РАВЕН обычному, то есть выключен.
# В исходнике это записано как os.getenv(..., str(MAX_POSITION_PCT)), поэтому
# проверяем именно это, а не значение глобальной переменной: у пользователя,
# включившего механизм своим `set`, она другая — и тест падал бы на верном коде.
_src_high_roi = open("gift_sniper.py", encoding="utf-8").read()
check("по умолчанию механизм ВЫКЛЮЧЕН (равен обычному лимиту)",
      'os.getenv("MAX_POSITION_PCT_HIGH_ROI", str(MAX_POSITION_PCT))' in _src_high_roi)

# И семантика: равные проценты обязаны означать "механизм не действует".
_op_hi, _oh_hi = gs.MAX_POSITION_PCT, gs.MAX_POSITION_PCT_HIGH_ROI
gs.MAX_POSITION_PCT = gs.MAX_POSITION_PCT_HIGH_ROI = Decimal("10")
check("при равных процентах высокий ROI потолок не поднимает",
      gs.position_pct_for(Decimal("999")) == gs.position_pct_for(None))
gs.MAX_POSITION_PCT, gs.MAX_POSITION_PCT_HIGH_ROI = _op_hi, _oh_hi

_f = Decimal("3.0")
check("граница ROI=0 совпадает с границей безубыточности",
      gs.max_buy_at_roi(_f, Decimal("0")) == gs.max_profitable_buy(_f))

# Цена, выведенная для ROI=100%, обязана давать ровно 100% при обратном счёте.
_b100 = gs.max_buy_at_roi(_f, Decimal("100"))
_roi_back = gs.compute_roi_pct(gs.compute_net_profit(_f, _b100), _b100)
check("цена для ROI=100% действительно даёт 100%",
      abs(_roi_back - Decimal("100")) < Decimal("0.05"), str(_roi_back))

# Регрессия на ошибку, которую я сам допустил: нельзя считать повышенный
# потолок против границы БЕЗУБЫТОЧНОСТИ. Требование ROI>=100% режет цену
# вдвое, и смешение этих двух порогов обещало бы доступность там, где её нет.
check("требование высокого ROI ужимает цену сильнее безубыточности",
      _b100 < gs.max_profitable_buy(_f) / Decimal("1.9"),
      f"{_b100} vs {gs.max_profitable_buy(_f)}")

_ob, _or_, _op, _oh, _ohr = (gs.BANKROLL_TON, gs.RESERVE_TON, gs.MAX_POSITION_PCT,
                             gs.HIGH_ROI_PCT, gs.MAX_POSITION_PCT_HIGH_ROI)
gs.BANKROLL_TON = Decimal("10")
gs.RESERVE_TON = Decimal("5")
gs.MAX_POSITION_PCT = Decimal("10")
gs.HIGH_ROI_PCT = Decimal("100")
gs.MAX_POSITION_PCT_HIGH_ROI = Decimal("30")

# Предыдущие секции оставили позиции в тестовой БД. Здесь проверяются
# ИМЕННО лимиты по цене, поэтому состояние БД подменяем на чистое —
# иначе тест падал бы из-за чужих данных, а не из-за логики.
_db_stubs = {name: getattr(gs, name) for name in
             ("deployed_capital", "consecutive_losses",
              "open_positions_count", "spend_since")}
gs.deployed_capital = lambda: Decimal("0")
gs.consecutive_losses = lambda: 0
gs.open_positions_count = lambda: 0
gs.spend_since = lambda _sec: Decimal("0")
try:
    check("обычная сделка ограничена обычным потолком",
          gs.max_position_size(Decimal("20")) == Decimal("1"),
          str(gs.max_position_size(Decimal("20"))))
    check("сделка с ROI выше порога получает повышенный потолок",
          gs.max_position_size(Decimal("150")) == Decimal("3"),
          str(gs.max_position_size(Decimal("150"))))
    check("ровно на пороге повышенный лимит уже действует",
          gs.max_position_size(Decimal("100")) == Decimal("3"))
    check("без указания ROI потолок остаётся обычным",
          gs.max_position_size() == Decimal("1"))

    # Главное: повышенный потолок НЕ отменяет остальные лимиты. Свободно
    # 5 TON (банк 10 минус резерв 5), и 6 TON не пройдут ни при каком ROI.
    _ok, _why = gs.check_risk_limits(Decimal("2.5"), Decimal("150"))
    check("дорогая сделка с высоким ROI проходит потолок", _ok, _why)
    _ok, _why = gs.check_risk_limits(Decimal("2.5"), Decimal("20"))
    check("та же цена при обычном ROI отсекается", not _ok, _why)

    gs.MAX_POSITION_PCT_HIGH_ROI = Decimal("90")
    _ok, _why = gs.check_risk_limits(Decimal("6"), Decimal("500"))
    check("резерв не обходится даже при исключительном ROI", not _ok, _why)
    check("причина отказа указывает на банк, а не на потолок",
          "резерв" in _why, _why)
finally:
    (gs.BANKROLL_TON, gs.RESERVE_TON, gs.MAX_POSITION_PCT,
     gs.HIGH_ROI_PCT, gs.MAX_POSITION_PCT_HIGH_ROI) = _ob, _or_, _op, _oh, _ohr
    for _name, _fn in _db_stubs.items():
        setattr(gs, _name, _fn)


# =============================================================================
print("\n[25] Оценка по похожим лотам: ошибка продавца или ловушка")
# =============================================================================

# Сегменты: Cookbook дешёвый, Bible дорогой. Floor коллекции определяется
# дешёвым сегментом, и именно поэтому оценивать по нему лот из дорогого
# сегмента — ошибка, а из дешёвого — самообман.
def mk_peer_item(model, price, addr="0:p"):
    return {"address": addr, "sale_price_ton": Decimal(str(price)),
            "is_on_sale": True, "mint_index": 5000,
            "traits": {"model": model, "backdrop": "Grey"}}


_pool = ([mk_peer_item("Cookbook", p) for p in ("1.0", "1.1", "1.2", "1.3", "1.5")] +
         [mk_peer_item("Bible", p) for p in ("8.0", "8.5", "9.0", "9.5", "10.0")])
_peers = gs.build_peer_prices(_pool)

check("сегменты разделены по ключевому трейту",
      sorted(_peers) == ["Bible", "Cookbook"], str(sorted(_peers)))
_pf, _pn = gs.peer_floor(mk_peer_item("Bible", "9"), _peers)
check("floor дорогого сегмента выше floor коллекции",
      _pf is not None and _pf > Decimal("1.5"), str(_pf))
check("размер выборки сегмента возвращается", _pn == 5, str(_pn))

# Меньше MIN_PEER_SAMPLE — сравнивать не с чем, и это НЕ "всё хорошо".
_thin = gs.build_peer_prices([mk_peer_item("Rare", "3.0")])
_pf, _pn = gs.peer_floor(mk_peer_item("Rare", "1.0"), _thin)
check("тонкий сегмент не даёт оценки", _pf is None and _pn == 1, f"{_pf} {_pn}")
_pf, _pn = gs.peer_floor({"traits": {}}, _peers)
check("лот без ключевого трейта не сравнивается", _pf is None and _pn == 0)

# --- Развилка целиком -------------------------------------------------------
_snap_peers = {"floor": Decimal("1.0"), "floor_reliable": True, "competition": 0,
               "trait_index": {}, "trait_total": 0, "peer_prices": _peers}

# Лот из ДОРОГОГО сегмента по цене дешёвого сегмента — ошибка продавца.
# Оценка берёт минимум, то есть floor коллекции: прибыль не завышается.
_ev = gs.evaluate_trade(mk_peer_item("Bible", "0.3", "0:mistake"), _snap_peers, None)
check("дешёвый лот из дорогого сегмента считается по floor коллекции",
      _ev["eff_floor"] == Decimal("1.0"), str(_ev["eff_floor"]))
check("оценка по сегменту НЕ завышает прибыль",
      _ev["eff_floor"] <= _snap_peers["floor"])

# Лот из дешёвого сегмента: floor сегмента выше floor коллекции, берём floor.
_ev = gs.evaluate_trade(mk_peer_item("Cookbook", "0.5", "0:cheapseg"), _snap_peers, None)
check("лот дешёвого сегмента оценивается не выше floor коллекции",
      _ev["eff_floor"] <= Decimal("1.0"), str(_ev["eff_floor"]))

# Главное: глубокая скидка БЕЗ данных о похожих — отказ, а не покупка.
_snap_blind = dict(_snap_peers, floor=Decimal("10"), peer_prices={})
_ev = gs.evaluate_trade(mk_peer_item("Unknown", "1.0", "0:blind"), _snap_blind, None)
check("скидка 90% без похожих лотов отклоняется",
      not _ev["allowed"] and "сравнить не с чем" in _ev["reason"], _ev["reason"])
check("скидка посчитана верно",
      _ev["discount_pct"] == Decimal("90.0"), str(_ev["discount_pct"]))

# Та же скидка, но похожие лоты есть и подтверждают её — сделка проходит.
_confirm = gs.build_peer_prices([mk_peer_item("Solid", p) for p in
                                 ("9.0", "9.5", "10.0", "10.5", "11.0")])
_snap_ok = dict(_snap_peers, floor=Decimal("10"), peer_prices=_confirm)
_ev = gs.evaluate_trade(mk_peer_item("Solid", "1.0", "0:real"), _snap_ok, None)
check("та же скидка с подтверждением по похожим разрешена",
      _ev["allowed"], _ev["reason"])

# Умеренная скидка без данных о похожих проходит как раньше: ворота
# ставились именно на ГЛУБОКУЮ скидку, а не на любую.
_snap_mid = dict(_snap_peers, floor=Decimal("10"), peer_prices={})
_ev = gs.evaluate_trade(mk_peer_item("Unknown", "5.0", "0:mid"), _snap_mid, None)
check("умеренная скидка без похожих не блокируется",
      _ev["allowed"], _ev["reason"])


# =============================================================================
print("\n[26] Выход из позиции: держим до floor, стоп при обвале")
# =============================================================================

_buy = Decimal("10")

# Основной сценарий: ждём покупателя, пока не вышел срок.
_d = gs.decide_exit(_buy, Decimal("12"), held_hours=5, hold_hours=24)
check("пока floor держится — держим позицию", _d["action"] == "hold", _d["reason"])
check("при удержании цена продажи не назначается", _d["sell_price"] is None)

_d = gs.decide_exit(_buy, Decimal("12"), held_hours=24, hold_hours=24)
check("по истечении срока продаём", _d["action"] == "sell", _d["reason"])
check("продаём ниже floor (undercut)",
      _d["sell_price"] < Decimal("12"), str(_d["sell_price"]))

# Стоп-лосс: floor уехал ниже цены ПОКУПКИ более чем на порог.
_d = gs.decide_exit(_buy, Decimal("7"), held_hours=10, hold_hours=24)
check("обвал floor включает стоп-лосс", _d["action"] == "stop", _d["reason"])
check("в причине названы цифры, а не просто 'стоп'",
      "30.0%" in _d["reason"] and "порог" in _d["reason"], _d["reason"])

# Выдержка: та же просадка, но позиция слишком молодая. На тонком рынке floor
# скачет от одного снятого лота, и мгновенный стоп фиксировал бы убыток там,
# где floor вернулся бы сам.
_d = gs.decide_exit(_buy, Decimal("7"), held_hours=1, hold_hours=24)
check("ранняя просадка не фиксируется", _d["action"] == "hold", _d["reason"])
check("но причина объясняет, что стоп отложен",
      "стоп с" in _d["reason"], _d["reason"])

# Стоп проверяется РАНЬШЕ срока удержания: досиживать в убытке бессмысленно.
_d = gs.decide_exit(_buy, Decimal("5"), held_hours=48, hold_hours=24)
check("при обвале выход помечается стопом, а не плановой продажей",
      _d["action"] == "stop", _d["reason"])

# Порог считается от цены ПОКУПКИ, а не от floor на входе, и он точный:
# при покупке за 4 стоп начинается ровно с floor 3.0 (падение 25%).
_d = gs.decide_exit(Decimal("4"), Decimal("3.0"), held_hours=10, hold_hours=24)
check("падение ровно на порог включает стоп", _d["action"] == "stop", _d["reason"])
_d = gs.decide_exit(Decimal("4"), Decimal("2.9"), held_hours=10, hold_hours=24)
check("падение глубже порога тоже включает стоп", _d["action"] == "stop")
_d = gs.decide_exit(Decimal("4"), Decimal("3.1"), held_hours=10, hold_hours=24)
check("падение чуть меньше порога стоп НЕ включает",
      _d["action"] == "hold", _d["reason"])

# Выключенный стоп-лосс возвращает поведение "держать до срока".
_oen = gs.ENABLE_STOP_LOSS
gs.ENABLE_STOP_LOSS = False
try:
    _d = gs.decide_exit(_buy, Decimal("5"), held_hours=10, hold_hours=24)
    check("с выключенным стопом позиция держится", _d["action"] == "hold", _d["reason"])
finally:
    gs.ENABLE_STOP_LOSS = _oen

# --- Бэктест обязан вызывать ЭТУ ЖЕ функцию --------------------------------
_exit_snaps = [{"ts": 0.0, "floor": Decimal("10")},
               {"ts": 10 * 3600, "floor": Decimal("5")}]     # обвал через 10ч
_res = gs._simulate_exit(_exit_snaps, 0.0, Decimal("10"), 24, False)
check("бэктест закрывает позицию по стопу, а не ждёт срока",
      _res["status"] == "closed" and _res["exit"] == "stop", str(_res))
check("стоп фиксирует убыток, а не рисует прибыль",
      _res["pnl"] < 0, str(_res.get("pnl")))

_ok_snaps = [{"ts": 0.0, "floor": Decimal("10")},
             {"ts": 25 * 3600, "floor": Decimal("11")}]
_res = gs._simulate_exit(_ok_snaps, 0.0, Decimal("5"), 24, False)
check("нормальный выход помечается как плановая продажа",
      _res["status"] == "closed" and _res["exit"] == "sell", str(_res))

# Дыра обесценивает выход ПО СРОКУ, но не должна маскировать стоп.
_gap_snaps = [{"ts": 0.0, "floor": Decimal("10")},
              {"ts": 70 * 3600, "floor": Decimal("11")}]
_res = gs._simulate_exit(_gap_snaps, 0.0, Decimal("5"), 24, False)
check("плановый выход через дыру не засчитывается",
      _res["status"] == "open" and _res["missing"] == "gap", str(_res))


# =============================================================================
print("\n[27] Уведомления в Telegram")
# =============================================================================

_sent = []


class _CaptureRequests:
    """Перехватывает исходящие сообщения вместо похода в сеть."""

    def post(self, url, json=None, timeout=None):
        _sent.append((url, json))
        return _FakeResponse({"ok": True})


_otok, _ochat, _ohb = (gs.TELEGRAM_BOT_TOKEN, gs.TELEGRAM_CHAT_ID, gs.HEARTBEAT_MIN)
_oreq = gs.requests
try:
    # Без токена — полная тишина, и это не ошибка: уведомления необязательны.
    gs.TELEGRAM_BOT_TOKEN = ""
    gs.requests = _CaptureRequests()
    check("без токена сводка не шлётся",
          gs.notify_heartbeat([], force=True) is False and not _sent)
    check("без токена стартовое сообщение не шлётся",
          gs.notify_startup(["0:a"]) is False and not _sent)

    gs.TELEGRAM_BOT_TOKEN = "123:test"
    gs.TELEGRAM_CHAT_ID = "42"
    gs.HEARTBEAT_MIN = 60
    gs._last_heartbeat = 0.0

    _snap = {"collection": "0:abcdef0123456789", "floor": Decimal("4.72"),
             "sample_size": 120, "floor_reliable": True}
    check("сводка уходит при force", gs.notify_heartbeat([_snap], force=True) is True)
    _body = _sent[-1][1]["text"]
    check("в сводке есть floor и размер выборки",
          "4.72" in _body and "120" in _body, _body)

    # Второй вызов подряд должен промолчать: уведомление каждую минуту
    # перестают читать, и тогда теряется то единственное, ради чего оно есть.
    _before = len(_sent)
    check("сводка не повторяется до истечения интервала",
          gs.notify_heartbeat([_snap]) is False and len(_sent) == _before)

    # Недостоверный floor обязан быть виден в телефоне, а не только в логе.
    gs._last_heartbeat = 0.0
    gs.notify_heartbeat([dict(_snap, floor_reliable=False)], force=True)
    check("мала выборка отмечается в сводке",
          "выборка мала" in _sent[-1][1]["text"], _sent[-1][1]["text"])

    gs._last_heartbeat = 0.0
    gs.notify_heartbeat([{"collection": "0:dead", "sample_size": 0}], force=True)
    check("коллекция без данных отмечается отдельно",
          "данных нет" in _sent[-1][1]["text"], _sent[-1][1]["text"])

    # Стартовое сообщение должно называть режим и предупреждать про whitelist:
    # настройки на Windows теряются при перезапуске, и тихий старт с чужими
    # значениями — самый дешёвый способ испортить запись.
    _owl, _odry = gs.COLLECTION_WHITELIST, gs.DRY_RUN
    gs.COLLECTION_WHITELIST, gs.DRY_RUN = [], True
    gs.notify_startup(["0:a", "0:b"])
    _body = _sent[-1][1]["text"]
    check("в старте указан режим симуляции", "СИМУЛЯЦИЯ" in _body, _body)
    check("в старте указано число коллекций", "Коллекций: 2" in _body, _body)
    check("пустой whitelist попадает в уведомление",
          "whitelist пуст" in _body, _body)
    gs.COLLECTION_WHITELIST, gs.DRY_RUN = _owl, _odry

    # Токен не должен утечь в текст сообщения — только в URL запроса.
    check("токен не попадает в тело сообщения",
          all("123:test" not in (j or {}).get("text", "") for _, j in _sent))
finally:
    (gs.TELEGRAM_BOT_TOKEN, gs.TELEGRAM_CHAT_ID,
     gs.HEARTBEAT_MIN) = _otok, _ochat, _ohb
    gs.requests = _oreq
    gs._last_heartbeat = 0.0


# =============================================================================
print("\n[28] Поиск коллекций под банк (--discover)")
# =============================================================================

_ob = gs.BANKROLL_TON
_oreq = gs.requests
_osnap = gs.get_market_snapshot
_oint = gs.TONAPI_MIN_INTERVAL
gs.TONAPI_MIN_INTERVAL = Decimal("0")
try:
    gs.BANKROLL_TON = Decimal("0")
    check("без банка поиск не запускается", gs.discover_collections() is None)

    gs.BANKROLL_TON = Decimal("10")
    gs.RESERVE_TON = Decimal("5")
    gs.MAX_POSITION_PCT = Decimal("10")

    # Форма ответа этого эндпоинта НЕ проверена на живых данных. Главное
    # требование: при неожиданном ответе честно сказать "не разобрал", а не
    # вернуть пустой список — пустой список читается как "подходящих нет",
    # и это увело бы поиск не в ту сторону.
    gs.requests = _FakeRequests({"unexpected_key": [1, 2, 3]})
    check("неразобранный ответ возвращает None, а не пустой список",
          gs.discover_collections() is None)

    _api = {"nft_collections": [
        {"address": "0:cheap", "metadata": {"name": "Cheap Gifts"}},
        {"address": "0:rich", "metadata": {"name": "Expensive Gifts"}},
        {"address": "0:thin", "metadata": {"name": "No Data"}},
    ]}
    _floors = {"0:cheap": Decimal("1.0"), "0:rich": Decimal("50.0"),
               "0:thin": Decimal("0")}

    def _fake_snapshot(addr):
        floor = _floors[addr]
        return {"floor": floor, "sample_size": 100 if floor > 0 else 0,
                "floor_reliable": floor > 0}

    gs.requests = _FakeRequests(_api)
    gs.get_market_snapshot = _fake_snapshot
    _found = gs.discover_collections()

    check("найдена только доступная банку коллекция",
          [c["address"] for c in _found] == ["0:cheap"],
          str([c["address"] for c in _found]))
    check("дорогая коллекция отсеяна по банку",
          all(c["address"] != "0:rich" for c in _found))
    check("коллекция без достоверного floor отсеяна",
          all(c["address"] != "0:thin" for c in _found))
    check("имя коллекции взято из metadata",
          _found[0]["name"] == "Cheap Gifts", _found[0]["name"])

    # Ни одной подходящей — это [] (искали и не нашли), а не None
    # (не смогли разобрать). Разница определяет, что делать дальше.
    _floors["0:cheap"] = Decimal("50.0")
    gs.requests = _FakeRequests(_api)
    check("'не нашлось' отличается от 'не разобрал'",
          gs.discover_collections() == [])
finally:
    gs.BANKROLL_TON = _ob
    gs.requests = _oreq
    gs.get_market_snapshot = _osnap
    gs.TONAPI_MIN_INTERVAL = _oint


# =============================================================================
print("\n[29] Площадки: floor по каждой и арбитраж между ними")
# =============================================================================

def mk_market_item(market, price, addr="0:m"):
    return {"address": addr, "sale_price_ton": Decimal(str(price)),
            "is_on_sale": True, "sale_market": market, "traits": {}}


# TonAPI читает блокчейн, поэтому лоты разных площадок приходят вперемешку
# в одном ответе. Общий floor их усредняет — и прячет разницу.
_mixed = ([mk_market_item("Getgems Sales", p) for p in
           ("10.0", "10.5", "11.0", "11.5", "12.0")] +
          [mk_market_item("Marketapp Marketplace", p) for p in
           ("6.0", "6.5", "7.0", "7.5", "8.0")])
_mf = gs.build_market_floors(_mixed)

check("площадки разделены", sorted(_mf) == ["Getgems Sales", "Marketapp Marketplace"],
      str(sorted(_mf)))
check("у каждой площадки свой floor",
      _mf["Marketapp Marketplace"]["floor"] < _mf["Getgems Sales"]["floor"],
      f"{_mf['Marketapp Marketplace']['floor']} vs {_mf['Getgems Sales']['floor']}")
check("размер выборки по площадке сохранён",
      _mf["Getgems Sales"]["n"] == 5, str(_mf["Getgems Sales"]["n"]))

# Общий floor лежит МЕЖДУ floor площадок — это и есть усреднение, из-за
# которого разница была не видна.
_all_prices = sorted(Decimal(str(i["sale_price_ton"])) for i in _mixed)
_common = gs._percentile(_all_prices, gs.FLOOR_PERCENTILE)
check("общий floor маскирует разницу между площадками",
      _mf["Marketapp Marketplace"]["floor"] <= _common <= _mf["Getgems Sales"]["floor"],
      str(_common))

# Тонкая выборка по площадке -> floor не считается. Одна цена не floor.
_thin = gs.build_market_floors([mk_market_item("Rare Market", "1.0")])
check("по одному лоту floor площадки не считается",
      _thin["Rare Market"]["floor"] is None and _thin["Rare Market"]["n"] == 1)

# Лот без названия площадки не теряется: он попадает в отдельную корзину,
# а не исчезает из выборки молча.
_noname = gs.build_market_floors([mk_market_item("", p) for p in
                                  ("1.0", "1.1", "1.2", "1.3")])
check("лоты без площадки не теряются",
      "(площадка неизвестна)" in _noname, str(list(_noname)))

# --- Отчёт по записи --------------------------------------------------------
import tempfile as _tf
_mdir = _tf.mkdtemp()
_mpath = os.path.join(_mdir, "markets.jsonl")


def _mk_market_snap(ts, cheap_floor, rich_floor):
    return {"ts": ts, "collection": "0:coll", "floor": str(cheap_floor),
            "sample_size": 100, "competition": 0, "floor_reliable": True,
            "trait_index": {}, "trait_total": 0, "peer_prices": {},
            "candidates": [],
            "market_floors": {
                "Marketapp Marketplace": {"n": 20, "floor": str(cheap_floor)},
                "Getgems Sales": {"n": 20, "floor": str(rich_floor)}}}


with open(_mpath, "w", encoding="utf-8") as f:
    for i in range(10):
        f.write(_json.dumps(_mk_market_snap(i * 3600, "6.0", "10.0")) + "\n")

_rep = gs.market_report(_mpath)
check("отчёт по площадкам построен", _rep and len(_rep) == 1, str(_rep))
check("дешёвая площадка определена верно",
      _rep[0]["cheapest"] == "Marketapp Marketplace", _rep[0]["cheapest"])
check("дорогая площадка определена верно",
      _rep[0]["richest"] == "Getgems Sales", _rep[0]["richest"])
check("разброс посчитан",
      abs(_rep[0]["spread_pct"] - Decimal("66.67")) < Decimal("0.1"),
      str(_rep[0]["spread_pct"]))
check("арбитраж считается ТОЙ ЖЕ экономикой, что и обычная сделка",
      _rep[0]["profit"] == gs.compute_net_profit(Decimal("10.0"), Decimal("6.0")),
      str(_rep[0]["profit"]))

# Разброс меньше комиссий — это не арбитраж, и отчёт обязан так и сказать.
_narrow = os.path.join(_mdir, "narrow.jsonl")
with open(_narrow, "w", encoding="utf-8") as f:
    for i in range(10):
        f.write(_json.dumps(_mk_market_snap(i * 3600, "10.0", "10.2")) + "\n")
_rep = gs.market_report(_narrow)
check("узкий разброс не выдаётся за прибыль", _rep[0]["profit"] < 0,
      str(_rep[0]["profit"]))

# Старая запись без market_floors: честный отказ, а не пустой отчёт.
_old = os.path.join(_mdir, "old.jsonl")
with open(_old, "w", encoding="utf-8") as f:
    f.write(_json.dumps({"ts": 0, "collection": "0:c", "floor": "1.0",
                        "sample_size": 10, "competition": 0,
                        "floor_reliable": True, "candidates": []}) + "\n")
check("запись без данных о площадках распознаётся",
      gs.market_report(_old) is None)


# =============================================================================
print("\n[32] Лимит квоты прекращает обход страниц, а не грызёт их дальше")
# =============================================================================

# Живой прогон 17.09.2026: 429 на ПЕРВОЙ же странице, все три попытки. Это не
# темп запросов, а исчерпанная квота анонимного доступа. Продолжать обход
# оставшихся 14 страниц бессмысленно — при пяти коллекциях это 70 заведомо
# мусорных запросов за цикл, которые только углубляют блокировку.

class _AlwaysLimited:
    def __init__(self):
        self.calls = 0

    def get(self, *a, **k):
        self.calls += 1
        return _TooManyRequests(retry_after=0)


_oreq32 = gs.requests
_oint32 = gs.TONAPI_MIN_INTERVAL
_opages = gs.FLOOR_SAMPLE_PAGES
gs.TONAPI_MIN_INTERVAL = Decimal("0")
gs.FLOOR_SAMPLE_PAGES = 15
try:
    _lim = _AlwaysLimited()
    gs.requests = _lim
    _items, _source = gs._collect_sample("EQC212djrq0gglQXi8MSFX1bcw4LHw3Es62lKvt1lZzzsYuF")

    # 3 попытки на первую страницу + 1 запрос к Getgems-фолбэку = 4.
    # Если бы обход продолжался, было бы 15 x 3 = 45 запросов.
    check("после лимита остальные страницы НЕ запрашиваются",
          _lim.calls <= 4, f"запросов={_lim.calls}")
    check("выборка пуста, а не наполовину собрана", _items == [])
    check("источник помечен как недоступный", _source == "none", _source)
finally:
    gs.requests = _oreq32
    gs.TONAPI_MIN_INTERVAL = _oint32
    gs.FLOOR_SAMPLE_PAGES = _opages


# =============================================================================
print("\n[33] Суточная квота: ждать надо часы, а не секунды")
# =============================================================================

# Дословный ответ TonAPI, снятый пользователем в браузере 17.09.2026:
#   {"error":"rate limit: anonymous tier daily traffic is spent,
#             resets at UTC midnight"}
# Это НЕ «слишком часто» — это «на сегодня всё». Ретраить бессмысленно, а
# продолжать слать запросы до полуночи UTC — жечь исчерпанный лимит и
# засорять лог.
_DAILY_BODY = ('{"error":"rate limit: anonymous tier daily traffic is spent, '
               'resets at UTC midnight"}')

_oreq33, _oint33 = gs.requests, gs.TONAPI_MIN_INTERVAL
_oquota = gs._tonapi_quota_until
gs.TONAPI_MIN_INTERVAL = Decimal("0")
gs._tonapi_quota_until = 0.0
try:
    _daily = _SeqRequests([_TooManyRequests(retry_after=0, text=_DAILY_BODY)])
    gs.requests = _daily
    try:
        gs.fetch_items_tonapi("EQC212djrq0gglQXi8MSFX1bcw4LHw3Es62lKvt1lZzzsYuF", 50)
        _raised, _why = False, ""
    except gs.RateLimited as e:
        _raised, _why = True, str(e)

    check("суточная квота распознана", _raised, _why)
    check("на суточную квоту НЕ тратятся ретраи",
          _daily.calls == 1, f"запросов={_daily.calls}")
    check("в сообщении назван срок сброса",
          "полночь UTC" in _why, _why)
    check("в сообщении названо лечение",
          "TONAPI_KEY" in _why, _why)

    # Метка выставлена — следующий запрос не должен даже уйти в сеть.
    check("после суточной квоты запросы не уходят вовсе",
          gs._tonapi_quota_until > time.time())
    _after = _SeqRequests([_FakeResponse(REAL_SALE_RESPONSE)])
    gs.requests = _after
    try:
        gs.fetch_items_tonapi("EQC212djrq0gglQXi8MSFX1bcw4LHw3Es62lKvt1lZzzsYuF", 50)
        _blocked = False
    except gs.RateLimited:
        _blocked = True
    check("запрос до сброса квоты даже не отправляется",
          _blocked and _after.calls == 0, f"запросов={_after.calls}")

    # Сброс квоты возвращает бота к работе сам, без перезапуска.
    gs._tonapi_quota_until = 0.0
    gs.requests = _SeqRequests([_FakeResponse(REAL_SALE_RESPONSE)])
    check("после сброса квоты работа возобновляется",
          len(gs.fetch_items_tonapi("EQC212djrq0gglQXi8MSFX1bcw4LHw3Es62lKvt1lZzzsYuF", 50)) == 4)

    # Обычный 429 («слишком часто») ретраится как раньше — путать нельзя.
    gs._tonapi_quota_until = 0.0
    _burst = _SeqRequests([_TooManyRequests(retry_after=0),
                           _FakeResponse(REAL_SALE_RESPONSE)])
    gs.requests = _burst
    check("обычный 429 по-прежнему ретраится",
          len(gs.fetch_items_tonapi("EQC212djrq0gglQXi8MSFX1bcw4LHw3Es62lKvt1lZzzsYuF", 50)) == 4
          and _burst.calls == 2, f"запросов={_burst.calls}")
    check("обычный 429 НЕ выставляет суточную метку",
          gs._tonapi_quota_until == 0.0)
finally:
    gs.requests = _oreq33
    gs.TONAPI_MIN_INTERVAL = _oint33
    gs._tonapi_quota_until = _oquota


# =============================================================================
print("\n[34] Покупка только на проверенных площадках")
# =============================================================================

# Карточки Getgems, снятые 18.09.2026, дали два наблюдения:
#   * Creator Fee = 0 GRAM на трёх лотах из разных коллекций подарков,
#     комиссия площадки 0.11/5.5, 1.48/74 и 0.09/4.89 — везде ~2%;
#   * лот на площадке "Other": Creator Fee 0.45 GRAM, комиссия площадки 0,
#     и адрес контракта продажи СОВПАДАЛ с адресом самого предмета.
# Второе означает, что у другой площадки другой протокол. Платить туда по
# нашей схеме — отправлять деньги вслепую, и потерять можно всю сумму.

check("роялти по умолчанию 0 (Creator Fee на карточках Getgems = 0)",
      source_default("ROYALTY_PCT") == "0", source_default("ROYALTY_PCT"))
check("по умолчанию разрешена только проверенная площадка",
      source_default("ALLOWED_MARKETS") == "Getgems Sales",
      source_default("ALLOWED_MARKETS"))

_odry34, _oexec34, _omk = gs.DRY_RUN, gs.REAL_EXECUTOR_AVAILABLE, gs.ALLOWED_MARKETS
try:
    gs.DRY_RUN = True
    gs.ALLOWED_MARKETS = ["Getgems Sales"]

    check("покупка на проверенной площадке проходит",
          gs.execute_blockchain_buy("0:i", Decimal("1"), "0:sale",
                                    "Getgems Sales") is True)
    check("покупка на непроверенной площадке отклоняется",
          gs.execute_blockchain_buy("0:i", Decimal("1"), "0:sale",
                                    "Marketapp Marketplace") is False)
    check("пустая площадка тоже отклоняется",
          gs.execute_blockchain_buy("0:i", Decimal("1"), "0:sale", "") is False)

    # Проверка стоит ДО ветки DRY_RUN: симуляция, рапортующая успех там, где
    # живая покупка ушла бы по непроверенному протоколу, врёт о готовности.
    gs.DRY_RUN = False
    gs.REAL_EXECUTOR_AVAILABLE = True
    check("в живом режиме непроверенная площадка тоже отклоняется",
          gs.execute_blockchain_buy("0:i", Decimal("1"), "0:sale", "Other") is False)

    # Список расширяем осознанно — значит он должен реально расширяться.
    gs.DRY_RUN = True
    gs.ALLOWED_MARKETS = ["Getgems Sales", "Marketapp Marketplace"]
    check("добавленная в список площадка разрешается",
          gs.execute_blockchain_buy("0:i", Decimal("1"), "0:sale",
                                    "Marketapp Marketplace") is True)

    # Пустой список = проверка отключена. Это осознанный выбор оператора,
    # а не случайность, поэтому поведение фиксируем.
    gs.ALLOWED_MARKETS = []
    check("пустой список отключает проверку площадки",
          gs.execute_blockchain_buy("0:i", Decimal("1"), "0:sale", "Что угодно") is True)
finally:
    gs.DRY_RUN, gs.REAL_EXECUTOR_AVAILABLE, gs.ALLOWED_MARKETS = _odry34, _oexec34, _omk


# =============================================================================
print("\n[35] Суточный бюджет запросов растягивает интервал")
# =============================================================================

# 5 коллекций x 15 страниц каждые 120с = 54 000 запросов в сутки. Живой прогон
# 18.09.2026 сжёг квоту даже С КЛЮЧОМ и ушёл в слепоту до полуночи. Дыра в
# записи хуже редкого шага: она обесценивает сделки в бэктесте, а редкий шаг
# всего лишь огрубляет наблюдение.

_ob35 = (gs.TONAPI_DAILY_BUDGET, gs.POLL_INTERVAL_SEC, gs.FLOOR_SAMPLE_PAGES,
         gs._tonapi_used_today, gs._tonapi_budget_day)
try:
    gs.POLL_INTERVAL_SEC = 120
    gs.FLOOR_SAMPLE_PAGES = 15
    gs.TONAPI_DAILY_BUDGET = 10000
    gs._tonapi_used_today = 0

    # Результат зависит от того, сколько осталось до полуночи UTC, поэтому
    # момент фиксируем. Иначе тест проходил бы утром и падал вечером — а
    # тест, зависящий от часа запуска, ничего не проверяет.
    _real_midnight = gs._next_utc_midnight
    SECONDS_LEFT = 12 * 3600          # ровно полсуток до сброса квоты
    gs._next_utc_midnight = lambda: time.time() + SECONDS_LEFT
    try:
        iv = gs.budget_paced_interval(5)
        check("при большой нагрузке интервал растягивается",
              iv > gs.POLL_INTERVAL_SEC, f"{iv:.0f}с при пороге {gs.POLL_INTERVAL_SEC}")

        # Бюджета должно хватить ровно до полуночи, не меньше и не сильно больше.
        cycles = SECONDS_LEFT / iv
        check("запросов при таком интервале не больше бюджета",
              cycles * 5 * 15 <= gs.TONAPI_DAILY_BUDGET * 1.01,
              f"{cycles*75:.0f} против {gs.TONAPI_DAILY_BUDGET}")

        # Уже потраченное учитывается: остаток бюджета меньше — интервал больше.
        gs._tonapi_used_today = 9000
        check("потраченный бюджет удлиняет интервал",
              gs.budget_paced_interval(5) > iv,
              f"{gs.budget_paced_interval(5):.0f} против {iv:.0f}")
        gs._tonapi_used_today = 0
    finally:
        gs._next_utc_midnight = _real_midnight

    # Механизм умеет только ЗАМЕДЛЯТЬ: разгонять бота он не должен.
    gs.FLOOR_SAMPLE_PAGES = 1
    check("при малой нагрузке интервал НЕ становится меньше заданного",
          gs.budget_paced_interval(1) == gs.POLL_INTERVAL_SEC,
          str(gs.budget_paced_interval(1)))

    # Нулевой бюджет = ограничение выключено.
    gs.TONAPI_DAILY_BUDGET = 0
    gs.FLOOR_SAMPLE_PAGES = 15
    check("нулевой бюджет отключает растягивание",
          gs.budget_paced_interval(5) == gs.POLL_INTERVAL_SEC)

    # Счётчик привязан к суткам UTC и обнуляется вместе с квотой.
    gs.TONAPI_DAILY_BUDGET = 10000
    gs._tonapi_used_today = 0
    gs._tonapi_budget_day = None
    gs._count_tonapi_request()
    check("запросы считаются", gs._tonapi_used_today == 1)
    gs._tonapi_budget_day = None            # имитируем наступление новых суток
    gs._count_tonapi_request()
    check("в новые сутки счётчик обнуляется", gs._tonapi_used_today == 1)
finally:
    (gs.TONAPI_DAILY_BUDGET, gs.POLL_INTERVAL_SEC, gs.FLOOR_SAMPLE_PAGES,
     gs._tonapi_used_today, gs._tonapi_budget_day) = _ob35


# =============================================================================
print("\n[36] Расход бюджета переживает перезапуск")
# =============================================================================

from datetime import datetime, timezone, timedelta

# Квота живёт на стороне TonAPI, а счётчик — в памяти процесса. Бот,
# перезапущенный в обед, без сохранения считает бюджет нетронутым, разгоняется
# до POLL_INTERVAL_SEC и добивает остаток квоты — ровно та слепота, ради
# которой бюджет и вводился. Правило файла: лимит, обнуляющийся при рестарте,
# — не лимит.

_ob36 = (gs.DB_PATH, gs._tonapi_used_today, gs._tonapi_budget_day,
         gs.TONAPI_DAILY_BUDGET)
try:
    gs.DB_PATH = os.path.join(_tmpdir, "budget.db")
    gs.TONAPI_DAILY_BUDGET = 10000
    gs.db_init()

    _today = datetime.now(timezone.utc).date()
    gs._tonapi_budget_day, gs._tonapi_used_today = _today, 4321
    gs.budget_flush()

    # «Перезапуск»: память обнулили, из БД расход обязан вернуться.
    gs._tonapi_used_today, gs._tonapi_budget_day = 0, None
    gs.budget_restore()
    check("расход суток восстанавливается после перезапуска",
          gs._tonapi_used_today == 4321, str(gs._tonapi_used_today))

    # Вчерашний расход к сегодняшнему бюджету отношения не имеет: квота
    # сбрасывается в полночь UTC. Подставить его = зря замедлить бота на сутки.
    with gs.db_connect() as _c:
        _c.execute("DELETE FROM api_budget")
        _c.execute("INSERT INTO api_budget (day, used) VALUES (?, ?)",
                   ((_today - timedelta(days=1)).isoformat(), 9999))
    gs._tonapi_used_today, gs._tonapi_budget_day = 0, None
    gs.budget_restore()
    check("вчерашний расход НЕ переносится на сегодня",
          gs._tonapi_used_today == 0, str(gs._tonapi_used_today))

    # Пустая БД (первый запуск) — не ошибка и не «бюджет потрачен».
    with gs.db_connect() as _c:
        _c.execute("DELETE FROM api_budget")
    gs._tonapi_used_today, gs._tonapi_budget_day = 0, None
    gs.budget_restore()
    check("первый запуск начинает сутки с нуля", gs._tonapi_used_today == 0)

    # Счётчик сам сбрасывается на диск, а не только в конце цикла: иначе
    # падение посреди цикла теряет весь его расход.
    gs._tonapi_budget_day = _today
    gs._tonapi_used_today = gs._BUDGET_FLUSH_EVERY - 1
    gs._count_tonapi_request()
    with gs.db_connect() as _c:
        _row = _c.execute("SELECT used FROM api_budget WHERE day = ?",
                          (_today.isoformat(),)).fetchone()
    check("счётчик сбрасывается на диск сам, без конца цикла",
          _row is not None and _row["used"] == gs._BUDGET_FLUSH_EVERY,
          str(_row["used"]) if _row else "нет записи")

    # Сбой записи не роняет наблюдение: данные рынка важнее учёта запросов.
    gs.DB_PATH = os.path.join(_tmpdir, "нет-такой-папки", "budget.db")
    try:
        gs.budget_flush()
        _survived = True
    except Exception:
        _survived = False
    check("недоступная БД не роняет бота на сохранении счётчика", _survived)
finally:
    (gs.DB_PATH, gs._tonapi_used_today, gs._tonapi_budget_day,
     gs.TONAPI_DAILY_BUDGET) = _ob36


# =============================================================================
print("\n[37] Живой режим закрыт, пока нет продажи")
# =============================================================================

# Покупка открывает позицию в БД, а закрыть её некому: decide_exit() вызывает
# только бэктест, close_position() не вызывает никто. Пущенный вживую бот
# скупал бы лоты и не продал бы ни одного — деньги в одну сторону.
#
# Остальные ворота смотрят на ключ, сеть и подтверждение риска, то есть на
# способность ПОТРАТИТЬ. Ни одни из них не спрашивают, сможем ли мы вернуть
# потраченное, поэтому проверка нужна отдельная.

check("по умолчанию продажа НЕ считается реализованной",
      source_default_const("SELLING_IMPLEMENTED") == "False",
      source_default_const("SELLING_IMPLEMENTED"))

_ob37 = (gs.DRY_RUN, gs.SELLING_IMPLEMENTED, gs.CONFIRM_LIVE_TRADING,
         gs.REAL_EXECUTOR_AVAILABLE, gs.COLLECTION_WHITELIST, gs.TARGET_COLLECTIONS,
         gs.WALLET_KEY_FILE, gs.BANKROLL_TON, gs.RESERVE_TON, gs.TRADING_NETWORK,
         gs.ANTHROPIC_AVAILABLE, gs.ANTHROPIC_API_KEY)
try:
    # Всё остальное намеренно приводим в «готовое к бою» состояние, чтобы
    # единственной причиной отказа осталась именно продажа.
    _good = "EQBlBJ4n01pmYez5VPd8Wo598s8agbQCyVOjucXKxLDAi9r7"
    gs.DRY_RUN = False
    gs.CONFIRM_LIVE_TRADING = "I_UNDERSTAND_THE_RISK"
    gs.REAL_EXECUTOR_AVAILABLE = True
    gs.TARGET_COLLECTIONS = [_good]
    gs.COLLECTION_WHITELIST = [_good]
    gs.BANKROLL_TON = Decimal("10")
    gs.RESERVE_TON = Decimal("1")
    gs.TRADING_NETWORK = "testnet"
    gs.ANTHROPIC_AVAILABLE = True
    gs.ANTHROPIC_API_KEY = "sk-test-not-a-real-key"

    _keyfile = os.path.join(_tmpdir, "wallet37.key")
    with open(_keyfile, "w", encoding="utf-8") as fh:
        fh.write("word " * 24)
    if os.name != "nt":
        os.chmod(_keyfile, 0o600)
    gs.WALLET_KEY_FILE = _keyfile

    # Сначала убеждаемся, что причина отказа — ИМЕННО продажа: с ней всё
    # проходит. Иначе тест доказывал бы лишь то, что preflight всегда против.
    gs.SELLING_IMPLEMENTED = True
    _passes_with_selling = gs.preflight_checks(require_ai=True)

    gs.SELLING_IMPLEMENTED = False
    check("без продажи живой режим НЕ стартует",
          gs.preflight_checks(require_ai=True) is False)

    check("с реализованной продажей те же настройки проходят",
          _passes_with_selling is True, str(_passes_with_selling))
finally:
    (gs.DRY_RUN, gs.SELLING_IMPLEMENTED, gs.CONFIRM_LIVE_TRADING,
     gs.REAL_EXECUTOR_AVAILABLE, gs.COLLECTION_WHITELIST, gs.TARGET_COLLECTIONS,
     gs.WALLET_KEY_FILE, gs.BANKROLL_TON, gs.RESERVE_TON, gs.TRADING_NETWORK,
     gs.ANTHROPIC_AVAILABLE, gs.ANTHROPIC_API_KEY) = _ob37


# =============================================================================
print("\n[38] Смена цены: сверка с живой транзакцией")
# =============================================================================

# Раскладка сообщения не реконструируется по документации — её у контракта
# нет. Она проверяется СХОДИМОСТЬЮ: собранная ячейка обязана совпасть байт в
# байт с телом операции, которая реально прошла в блокчейне (владелец
# поставил 77 TON). Тот же критерий, из-за которого op 0xfd135f7b сначала
# опознали неверно как «снятие с продажи».
#
# Дословное тело, снятое кнопкой «Copy Raw body» (18.09.2026):
LIVE_SET_PRICE_BOC = "b5ee9c72010101010014000023fd135f7b76a486ee80061f24511ed8ec2004"
LIVE_QUERY_ID = 8549106351564267300
LIVE_PRICE = Decimal("77")

if gs.REAL_EXECUTOR_AVAILABLE:
    _cell = gs.build_set_price_body(LIVE_PRICE, LIVE_QUERY_ID)
    check("сборка цены совпадает с живой транзакцией побайтово",
          _cell.to_boc().hex() == LIVE_SET_PRICE_BOC,
          _cell.to_boc().hex())

    # Цена обязана попадать в тело: ячейка, одинаковая при разных ценах,
    # означала бы, что цена никуда не записалась.
    _other = gs.build_set_price_body(Decimal("5"), LIVE_QUERY_ID)
    check("другая цена даёт другое тело",
          _other.to_boc().hex() != LIVE_SET_PRICE_BOC)
else:
    print("  --   tonutils не установлен: сверка байтов пропущена")
    try:
        gs.build_set_price_body(Decimal("5"))
        _clear38 = False
    except RuntimeError as e:
        _clear38 = "tonutils" in str(e) and "pip install" in str(e)
    except NameError:
        _clear38 = False
    check("без библиотеки сборка даёт понятную ошибку, а не NameError", _clear38)

# Отказы. Смена цены тратит газ, поэтому «успех» без отправки так же вреден,
# как и у покупки: он сказал бы, что лот перевыставлен, когда он не тронут.
_ob38 = (gs.DRY_RUN, gs.REAL_EXECUTOR_AVAILABLE)
try:
    gs.DRY_RUN = True          # даже в симуляции мусорный адрес — не успех
    check("нераспознанный адрес контракта = False",
          gs.execute_set_price("не-адрес", "5") is False)
    check("пустой адрес контракта = False",
          gs.execute_set_price("", "5") is False)
    check("нулевая цена = False",
          gs.execute_set_price(_GOOD_ADDR38 := "EQBlBJ4n01pmYez5VPd8Wo598s8agbQCyVOjucXKxLDAi9r7",
                               "0") is False)
    check("отрицательная цена = False",
          gs.execute_set_price(_GOOD_ADDR38, "-5") is False)
    check("в симуляции корректный вызов проходит",
          gs.execute_set_price(_GOOD_ADDR38, "5") is True)

    # Без библиотеки подписи живой режим обязан отказать, а не притвориться.
    gs.DRY_RUN = False
    gs.REAL_EXECUTOR_AVAILABLE = False
    check("без исполнителя живая смена цены = False",
          gs.execute_set_price(_GOOD_ADDR38, "5") is False)
finally:
    (gs.DRY_RUN, gs.REAL_EXECUTOR_AVAILABLE) = _ob38

# Газ на смену цены — величина, отличная от газа покупки, и НЕ измеренная.
# Тест фиксирует, что её не приравняли к покупочной «чтобы было единообразно».
check("газ смены цены задан отдельно от газа покупки",
      source_default("SET_PRICE_GAS_TON") != source_default("PURCHASE_GAS_TON"),
      f'{source_default("SET_PRICE_GAS_TON")} / {source_default("PURCHASE_GAS_TON")}')


# =============================================================================
print("\n[39] Хранилище контракта продажи: сверка с задеплоенным контрактом")
# =============================================================================

# Раскладка v4 нигде не документирована, поэтому проверяется СХОДИМОСТЬЮ:
# собранная из полей живого листинга ячейка обязана дать хеш того хранилища,
# которое реально лежит в блокчейне. Хеш data входит в хеш StateInit, а тот
# И ЕСТЬ адрес контракта — ошибка хоть в одном бите дала бы другой адрес.
#
# Поля сняты с листинга Xmas Stocking #91332 (18.09.2026), контракт
# EQCcWUC7KPl_MXXsDHGt5GxJyP9Eo_54C88xBK89kSKKFSO1.
LIVE_NFT      = "0:48de39a63d627d30d2d62e7da41f793da94c5b3f3893864fe93c0617b36b708d"
LIVE_OWNER    = "0:05ea962de15b8115bdbd5eda1be44986d1eaa9d8527dbc6fcd249fd2a08651e3"
LIVE_ROYALTY  = "0:68f3a076d3451a18fd41e05c71b4c020545d46b2757064e65825ded0c49bf02c"
LIVE_PUBKEY   = 0xb1b12b8f4eaa103fa8b05b17bb5fd96362fda6dd43a258c4f656976a5391f388
LIVE_CREATED  = 1789755986
LIVE_PRICE    = Decimal("5")
LIVE_DATA_HASH = "7b2f33f8de550b5639ea9494ba3ee282e6b5ea760bc05b7a0aff2f35bca042eb"

if gs.REAL_EXECUTOR_AVAILABLE:
    _data = gs.build_sale_contract_data(
        nft_address=LIVE_NFT, owner_address=LIVE_OWNER, price=LIVE_PRICE,
        royalty_address=LIVE_ROYALTY, created_at=LIVE_CREATED,
        public_key=LIVE_PUBKEY)
    check("хранилище контракта совпадает с задеплоенным побитово",
          _data.hash.hex() == LIVE_DATA_HASH, _data.hash.hex())

    # Цена обязана влиять на хранилище: одинаковая ячейка при разных ценах
    # означала бы контракт, продающий не за то, что мы просили.
    _cheap = gs.build_sale_contract_data(
        nft_address=LIVE_NFT, owner_address=LIVE_OWNER, price=Decimal("3.33"),
        royalty_address=LIVE_ROYALTY, created_at=LIVE_CREATED,
        public_key=LIVE_PUBKEY)
    check("другая цена даёт другое хранилище",
          _cheap.hash.hex() != LIVE_DATA_HASH)

    # И адрес продавца тоже: контракт с чужим адресом выручки заплатит не нам.
    _other_owner = gs.build_sale_contract_data(
        nft_address=LIVE_NFT, owner_address=LIVE_ROYALTY, price=LIVE_PRICE,
        royalty_address=LIVE_ROYALTY, created_at=LIVE_CREATED,
        public_key=LIVE_PUBKEY)
    check("другой продавец даёт другое хранилище",
          _other_owner.hash.hex() != LIVE_DATA_HASH)
else:
    print("  --   tonutils не установлен: сверка хранилища пропущена")
    try:
        gs.build_sale_contract_data(LIVE_NFT, LIVE_OWNER, LIVE_PRICE,
                                    LIVE_ROYALTY, LIVE_CREATED, LIVE_PUBKEY)
        _clear39 = False
    except RuntimeError as e:
        _clear39 = "tonutils" in str(e) and "pip install" in str(e)
    except NameError:
        _clear39 = False
    check("без библиотеки сборка даёт понятную ошибку, а не NameError", _clear39)

# Адреса Getgems — не настройки. Подставить туда своё значение значит
# задеплоить контракт, который площадка не узнает или который платит не туда.
check("адрес деплойера Getgems зафиксирован в коде, а не читается из окружения",
      "GETGEMS_DEPLOYER" not in open("gift_sniper.py", encoding="utf-8").read()
      .split("GETGEMS_DEPLOYER =")[1].split("\n")[0] and
      "getenv" not in open("gift_sniper.py", encoding="utf-8").read()
      .split("GETGEMS_DEPLOYER =")[1].split("\n")[0])


# =============================================================================
print("\n[40] Уведомления о находках")
# =============================================================================

# Пока бот в DRY_RUN — а он в нём надолго, живой режим закрыт воротами —
# покупок не существует. Без уведомления о находке владелец не видит работы
# бота вообще: смотреть лог на VPS с телефона никто не станет.

_sent40 = []
_ob40 = (gs.notify, gs.TELEGRAM_BOT_TOKEN, gs.TELEGRAM_CHAT_ID,
         gs.FIND_NOTIFY_MAX_PER_HOUR, gs.DRY_RUN)
try:
    gs.notify = lambda text: _sent40.append(text)
    gs.TELEGRAM_BOT_TOKEN = "тест"
    gs.TELEGRAM_CHAT_ID = "42"
    gs.FIND_NOTIFY_MAX_PER_HOUR = 3
    gs.DRY_RUN = True
    gs._find_sent_ts.clear()
    gs._finds_suppressed = 0

    _item40 = {"address": "0:" + "ab" * 32, "sale_price_ton": Decimal("1.2")}
    _snap40 = {"floor": Decimal("1.5"), "floor_reliable": True}
    _ev40 = {"discount_pct": Decimal("20.0"), "buy_price": Decimal("1.2"),
             "net_profit": Decimal("0.12"), "roi_pct": Decimal("10"),
             "peer_floor": None, "eff_floor": Decimal("1.5"), "peer_n": 0,
             "rarity_pct": None, "rarest_trait": None}

    check("находка отправляется", gs.notify_find(_item40, _snap40, _ev40) is True)
    _txt = _sent40[-1]
    check("в сообщении есть цена и floor", "1.2" in _txt and "1.5" in _txt, _txt[:60])
    check("в сообщении есть ссылка на площадку", "getgems.io" in _txt)
    # Ссылка на площадку может не открыться: её форма не проверена живьём, да
    # и лот мог уйти с продажи. Обозреватель резолвит адрес всегда, а сам
    # адрес можно вставить в поиск руками.
    check("есть ссылка на обозреватель", "tonviewer.com" in _txt)
    check("есть сам адрес лота", _item40["address"] in _txt)
    check("в симуляции честно сказано, что покупки не будет",
          "симуляц" in _txt.lower(), _txt)

    # Потолок частоты. Телефон, звонящий десять раз подряд, выключают — и
    # тогда пропускают ту находку, ради которой всё затевалось.
    _sent40.clear()
    gs._find_sent_ts.clear()
    gs._finds_suppressed = 0
    _ok = [gs.notify_find(_item40, _snap40, _ev40) for _ in range(5)]
    check("сверх лимита за час не шлём", _ok == [True, True, True, False, False],
          str(_ok))
    check("подавленные посчитаны", gs._finds_suppressed == 2,
          str(gs._finds_suppressed))

    # Подавленные обязаны всплыть в сводке: «тихо не отправили» и «находок не
    # было» — разные вещи, и перепутать их значит решить, что рынок мёртв.
    _sent40.clear()
    gs.notify_heartbeat([], force=True)
    check("сводка называет число подавленных находок",
          any("подавлен" in t.lower() or "не отправлено" in t.lower() for t in _sent40),
          str(_sent40))
    check("счётчик подавленных сбрасывается после сводки",
          gs._finds_suppressed == 0)

    # Нулевой лимит = механизм выключен целиком.
    gs.FIND_NOTIFY_MAX_PER_HOUR = 0
    gs._find_sent_ts.clear()
    check("нулевой лимит отключает уведомления о находках",
          gs.notify_find(_item40, _snap40, _ev40) is False)

    # Без токена Telegram — молча ничего, а не падение торгового цикла.
    gs.FIND_NOTIFY_MAX_PER_HOUR = 10
    gs.TELEGRAM_BOT_TOKEN = ""
    check("без токена находка не шлётся и не падает",
          gs.notify_find(_item40, _snap40, _ev40) is False)
finally:
    (gs.notify, gs.TELEGRAM_BOT_TOKEN, gs.TELEGRAM_CHAT_ID,
     gs.FIND_NOTIFY_MAX_PER_HOUR, gs.DRY_RUN) = _ob40
    gs._find_sent_ts.clear()
    gs._finds_suppressed = 0

# scan_finds() обязана молчать при недостоверном floor: находка, посчитанная
# от floor по тонкой выборке, — это приглашение купить по завышенной оценке.
check("при недостоверном floor находок нет",
      gs.scan_finds({"floor": Decimal("1.5"), "floor_reliable": False,
                     "candidates": [{"address": "0:" + "cd" * 32,
                                     "sale_price_ton": Decimal("0.1")}]},
                    None) == 0)


# =============================================================================
print("\n[41] Telegram: отказ сервера НЕ выглядит успехом")
# =============================================================================

# requests.post бросает исключение только на СЕТЕВОЙ ошибке. Неверный токен
# это HTTP 401, неизвестный chat_id — 400, и оба раза ответ обычный. Код без
# проверки статуса считал их успехом: владелец видел ровный зелёный лог и
# пустой чат, а причины в логе не было вовсе. Поймано живым прогоном 21.09.2026.

class _Resp41:
    def __init__(self, ok, code=200, body=None):
        self.ok, self.status_code, self._body = ok, code, (body or {})

    def json(self):
        return self._body


_ob41 = (gs.requests.post, gs.TELEGRAM_BOT_TOKEN, gs.TELEGRAM_CHAT_ID)
_logged41 = []
_olog41 = gs.log.warning
try:
    gs.TELEGRAM_BOT_TOKEN = "123:FAKE"
    gs.TELEGRAM_CHAT_ID = "42"
    gs.log.warning = lambda m, *a, **k: _logged41.append(str(m))

    gs.requests.post = lambda *a, **k: _Resp41(True)
    check("успешная отправка = True", gs.notify("привет") is True)

    _logged41.clear()
    gs.requests.post = lambda *a, **k: _Resp41(
        False, 401, {"description": "Unauthorized"})
    check("неверный токен = False, а не тихий успех", gs.notify("привет") is False)
    check("в логе назван токен как причина",
          any("TELEGRAM_BOT_TOKEN" in m for m in _logged41), str(_logged41))

    _logged41.clear()
    gs.requests.post = lambda *a, **k: _Resp41(
        False, 400, {"description": "Bad Request: chat not found"})
    check("неизвестный chat_id = False", gs.notify("привет") is False)
    check("в логе совет написать боту /start",
          any("/start" in m for m in _logged41), str(_logged41))

    # Токен лежит прямо в URL, поэтому в лог он попасть не должен НИКОГДА.
    check("токен не утёк в лог",
          not any("FAKE" in m for m in _logged41), str(_logged41))

    # Сетевая ошибка — тоже не успех.
    _logged41.clear()
    def _boom(*a, **k):
        raise ConnectionError("сеть упала")
    gs.requests.post = _boom
    check("сетевая ошибка = False", gs.notify("привет") is False)

    # Без токена — тихо и False, торговый цикл не страдает.
    gs.TELEGRAM_BOT_TOKEN = ""
    check("без токена = False без падения", gs.notify("привет") is False)
finally:
    (gs.requests.post, gs.TELEGRAM_BOT_TOKEN, gs.TELEGRAM_CHAT_ID) = _ob41
    gs.log.warning = _olog41


# =============================================================================
print("\n[42] Бэктест говорит, когда ему НЕЛЬЗЯ верить")
# =============================================================================

# Живой прогон 21.09.2026 дал «Winrate: 100.0% (1/1)» и «расхождение 0.0000».
# Обе строки читаются как «модель работает», а означают другое: сделка была
# ОДНА, и floor за сутки не сдвинулся. Отчёт, из которого можно с уверенным
# видом сделать неверный вывод, опаснее отсутствующего — именно по такой
# строке решают включить живую торговлю.

_warn42 = []
_info42 = []
_ow42 = (gs.log.warning, gs.log.info)
try:
    gs.log.warning = lambda m, *a, **k: _warn42.append(str(m))
    gs.log.info = lambda m, *a, **k: _info42.append(str(m))

    _one = [{"status": "closed", "buy": Decimal("5"), "expected": Decimal("0.55"),
             "pnl": Decimal("0.55"), "roi": Decimal("11"),
             "entry_floor": Decimal("6"), "exit_floor": Decimal("6")}]
    _res = gs._report_backtest(_one, 24, span_h=96.9, collections=6)

    check("малая выборка названа малой",
          any("ВЫБОРКА МАЛА" in m for m in _warn42), str(_warn42)[:120])
    check("сказано, что floor не двигался",
          any("floor на выходе совпал" in m for m in _warn42), str(_warn42)[:120])
    check("посчитана частота сделок",
          any("Частота:" in m for m in _info42), str(_info42)[:120])
    check("сказано, сколько суток нужно на осмысленную выборку",
          any("суток записи" in m for m in _info42), str(_info42)[:120])
    check("все выходы отмечены как плоские", _res["flat_exits"] == 1)

    # Обратная сторона: когда floor ДВИГАЛСЯ, предупреждения про плоский
    # рынок быть не должно — иначе оно обесценится и его перестанут читать.
    _warn42.clear()
    _moved = [{"status": "closed", "buy": Decimal("5"), "expected": Decimal("0.55"),
               "pnl": Decimal("0.20"), "roi": Decimal("11"),
               "entry_floor": Decimal("6"), "exit_floor": Decimal("5.5")}]
    _res2 = gs._report_backtest(_moved, 24, span_h=96.9, collections=6)
    check("при сдвинувшемся floor про плоский рынок не пишем",
          not any("floor на выходе совпал" in m for m in _warn42), str(_warn42)[:120])
    check("плоских выходов ноль", _res2["flat_exits"] == 0)

    # А предупреждение о малой выборке остаётся: оно про число сделок.
    check("малая выборка названа и здесь",
          any("ВЫБОРКА МАЛА" in m for m in _warn42))
finally:
    (gs.log.warning, gs.log.info) = _ow42

# Порог — проектное решение: winrate на единицах сделок это шум.
check("порог осмысленной выборки не меньше 20",
      int(source_default("MIN_BACKTEST_TRADES")) >= 20,
      source_default("MIN_BACKTEST_TRADES"))


# =============================================================================
print("\n[43] Реальный лимит TonAPI измеряется, а не угадывается")
# =============================================================================

# TONAPI_DAILY_BUDGET — это догадка. У анонимного доступа лимит один, у
# бесплатного ключа другой, у платного третий. Занижённая догадка делает
# наблюдение реже, чем позволено; завышенная сжигает квоту к обеду. Отказ
# сервера — единственный ИЗМЕРЕННЫЙ факт о лимите, и выбрасывать его глупо.

_ob43 = (gs.DB_PATH, gs.TONAPI_DAILY_BUDGET, gs._tonapi_used_today,
         gs._tonapi_budget_day)
try:
    gs.DB_PATH = os.path.join(_tmpdir, "learn.db")
    gs.db_init()
    gs.TONAPI_DAILY_BUDGET = 10000

    check("пока в стену не упирались — лимит не выучен",
          gs.budget_learned_limit() is None)
    check("бюджет равен настройке", gs.effective_daily_budget() == 10000)

    # Отказ при смешном счётчике — не измерение: счётчик считает только НАШИ
    # запросы, а квоту могли сжечь до старта или другим процессом. Выученные
    # «20 запросов в сутки» замедлили бы бота навсегда.
    gs.budget_learn_limit(20)
    check("отказ при низком счётчике лимитом не считается",
          gs.budget_learned_limit() is None)

    # Сервер отказал после 3000 запросов — значит столько он и даёт.
    gs.budget_learn_limit(3000)
    check("лимит запомнен", gs.budget_learned_limit() == 3000)
    check("бюджет считается по измеренному, с запасом",
          gs.effective_daily_budget() == int(3000 * gs._LEARNED_MARGIN),
          str(gs.effective_daily_budget()))
    check("запас оставлен, а не потрачен весь",
          gs.effective_daily_budget() < 3000)

    # Выученный лимит должен ВЛИЯТЬ на интервал, иначе он бесполезен.
    _real_mid = gs._next_utc_midnight
    gs._next_utc_midnight = lambda: time.time() + 12 * 3600
    try:
        gs._tonapi_used_today = 0
        _with = gs.budget_paced_interval(5)
        with gs.db_connect() as _c:
            _c.execute("DELETE FROM api_budget WHERE day='_learned_limit'")
        _without = gs.budget_paced_interval(5)
        check("меньший измеренный лимит растягивает интервал сильнее",
              _with > _without, f"{_with:.0f} против {_without:.0f}")
    finally:
        gs._next_utc_midnight = _real_mid

    # Оценка не должна ПАДАТЬ: лимит на сервере постоянен, а низкий счётчик
    # бывает от потерянной истории. Берём максимум из виденного.
    # (проверка интервала выше стёрла запись — восстанавливаем)
    gs.budget_learn_limit(3000)
    gs.budget_learn_limit(900)
    check("более низкое измерение не понижает оценку",
          gs.budget_learned_limit() == 3000, str(gs.budget_learned_limit()))
    gs.budget_learn_limit(6000)
    check("более высокое измерение поднимает оценку",
          gs.budget_learned_limit() == 6000, str(gs.budget_learned_limit()))

    # Мусор не запоминаем: ноль запросов лимитом быть не может.
    with gs.db_connect() as _c:
        _c.execute("DELETE FROM api_budget WHERE day='_learned_limit'")
    gs.budget_learn_limit(0)
    check("нулевой лимит не запоминается", gs.budget_learned_limit() is None)

    # Выученный лимит НЕ должен путаться с расходом за сутки: обе записи
    # лежат в одной таблице, и перепутать их значит спланировать по чужому
    # числу.
    _today = datetime.now(timezone.utc).date()
    gs._tonapi_budget_day, gs._tonapi_used_today = _today, 777
    gs.budget_flush()
    gs.budget_learn_limit(4200)
    gs._tonapi_used_today, gs._tonapi_budget_day = 0, None
    gs.budget_restore()
    check("расход суток не перепутан с выученным лимитом",
          gs._tonapi_used_today == 777, str(gs._tonapi_used_today))
    check("выученный лимит не перепутан с расходом",
          gs.budget_learned_limit() == 4200, str(gs.budget_learned_limit()))
finally:
    (gs.DB_PATH, gs.TONAPI_DAILY_BUDGET, gs._tonapi_used_today,
     gs._tonapi_budget_day) = _ob43


# =============================================================================
print("\n[30] Ставка комиссии площадки")
# =============================================================================

# Справка Getgems (18.09.2026): 5% с продажи вообще, 1% для Anonymous Telegram
# Numbers и Usernames, и 2% ДЛЯ TELEGRAM-ПОДАРКОВ. Бот работает именно с
# подарками, поэтому 2%. Сходится с карточкой лота: 0.09 GRAM на 4.75 = 1.9%.
#
# Тест стоит здесь потому, что этот параметр уже дважды ставили неверно, и
# оба раза правка проходила незаметно: завышенная комиссия просто тихо
# отклоняет сделки, заниженная — тихо завышает прибыль.
check("комиссия площадки по умолчанию = 2% (ставка Telegram-подарков)",
      source_default("MARKETPLACE_FEE_PCT") == "0.02",
      source_default("MARKETPLACE_FEE_PCT"))

# Порядок величины прибыли при этой ставке. Если кто-то поставит 5%,
# сделка на floor 1.5 перестанет проходить порог ROI — и это будет выглядеть
# как "рынок плохой", а не как ошибка в конфиге.
# Порог ROI тоже приходит из окружения — фиксируем и его, иначе проверка
# мерит не комиссию, а чужую настройку.
_of, _or2, _omin = gs.MARKETPLACE_FEE_PCT, gs.ROYALTY_PCT, gs.MIN_ROI_PCT
try:
    gs.MARKETPLACE_FEE_PCT, gs.ROYALTY_PCT = Decimal("0.02"), Decimal("0.05")
    gs.MIN_ROI_PCT = Decimal("5")
    _p = gs.compute_net_profit(Decimal("1.5"), Decimal("1.12"))
    check("при 2% сделка на floor 1.5 проходит порог ROI",
          gs.compute_roi_pct(_p, Decimal("1.12")) >= gs.MIN_ROI_PCT,
          str(gs.compute_roi_pct(_p, Decimal("1.12"))))

    gs.MARKETPLACE_FEE_PCT = Decimal("0.05")
    _p5 = gs.compute_net_profit(Decimal("1.5"), Decimal("1.12"))
    check("при 5% та же сделка порог НЕ проходит",
          gs.compute_roi_pct(_p5, Decimal("1.12")) < gs.MIN_ROI_PCT,
          str(gs.compute_roi_pct(_p5, Decimal("1.12"))))
    check("разница между 2% и 5% — это 3% от цены продажи",
          abs((_p - _p5) - gs.target_sale_price(Decimal("1.5")) * Decimal("0.03"))
          < Decimal("0.0001"), str(_p - _p5))
finally:
    gs.MARKETPLACE_FEE_PCT, gs.ROYALTY_PCT, gs.MIN_ROI_PCT = _of, _or2, _omin


# =============================================================================
print("\n[31] Исполнитель покупки: True только при реальной отправке")
# =============================================================================

# Главное правило файла: execute_blockchain_buy() НИКОГДА не возвращает True,
# не совершив сделку. Иначе в БД появится позиция, которой нет, и учёт PnL
# станет фикцией. Здесь это проверяется на всех путях отказа.

_odry2 = gs.DRY_RUN
_oexec2 = gs.REAL_EXECUTOR_AVAILABLE
_okey = gs.WALLET_KEY_FILE
_onet = gs.TRADING_NETWORK
try:
    gs.DRY_RUN = False

    gs.REAL_EXECUTOR_AVAILABLE = False
    check("без библиотеки подписи покупка возвращает False",
          gs.execute_blockchain_buy("0:item", Decimal("1"), "0:sale") is False)

    gs.REAL_EXECUTOR_AVAILABLE = True
    gs.WALLET_KEY_FILE = ""
    check("без файла ключа покупка возвращает False",
          gs.execute_blockchain_buy("0:item", Decimal("1"), "0:sale") is False)

    # Адрес продажи проверяется раньше всего: платить некуда.
    check("без адреса контракта продажи покупка возвращает False",
          gs.execute_blockchain_buy("0:item", Decimal("1"), "") is False)

    # Любое исключение внутри отправки = сделки не было. Возврат True здесь
    # был бы худшей из возможных ошибок: бот записал бы несуществующую позицию.
    _osend = gs._send_purchase

    async def _boom(*a, **k):
        raise RuntimeError("сеть недоступна")

    gs._send_purchase = _boom
    gs.WALLET_KEY_FILE = key_path
    check("исключение при отправке НЕ выглядит покупкой",
          gs.execute_blockchain_buy("0:item", Decimal("1"), "0:sale") is False)
    gs._send_purchase = _osend

    # Выбор сети проверяется только если библиотека установлена. Без неё
    # ЭТИ проверки бессмысленны, но обязана быть другая: понятное сообщение
    # вместо NameError. Тесты должны проходить и на машине без tonutils —
    # иначе набор ломается там, где ломаться нечему.
    if gs._NetworkGlobalID is not None:
        gs.TRADING_NETWORK = "testnet"
        check("по умолчанию торгуем в testnet",
              gs._trading_network() == gs._NetworkGlobalID.TESTNET)
        gs.TRADING_NETWORK = "mainnet"
        check("mainnet выбирается только явно",
              gs._trading_network() == gs._NetworkGlobalID.MAINNET)
        gs.TRADING_NETWORK = "чепуха"
        check("нераспознанная сеть НЕ уводит в mainnet",
              gs._trading_network() == gs._NetworkGlobalID.TESTNET)
    else:
        print("  --   tonutils не установлен: проверки выбора сети пропущены")
        try:
            gs._trading_network()
            _clear = False
        except RuntimeError as e:
            _clear = "tonutils" in str(e) and "pip install" in str(e)
        except NameError:
            _clear = False
        check("без библиотеки сеть даёт понятную ошибку, а не NameError",
              _clear)

    # Газ поверх цены — отдельная величина от газа в экономике сделки.
    check("газ на исполнение контракта задан отдельно от экономики",
          gs.PURCHASE_GAS_TON > 0 and gs.PURCHASE_GAS_TON != gs.GAS_FEE_TON,
          f"{gs.PURCHASE_GAS_TON} / {gs.GAS_FEE_TON}")

    # 0.3 — столько прикладывает сам интерфейс Getgems (диалог покупки,
    # 18.09.2026). Недостача газа роняет транзакцию, а излишек возвращается,
    # поэтому опускать это значение нельзя.
    check("газ на покупку не ниже того, что прикладывает Getgems",
          Decimal(source_default("PURCHASE_GAS_TON")) >= Decimal("0.3"),
          source_default("PURCHASE_GAS_TON"))
finally:
    gs.DRY_RUN = _odry2
    gs.REAL_EXECUTOR_AVAILABLE = _oexec2
    gs.WALLET_KEY_FILE = _okey
    gs.TRADING_NETWORK = _onet


# =============================================================================
print("\n" + "=" * 60)
if _failures:
    print(f"ПРОВАЛЕНО: {len(_failures)} проверок -> {_failures}")
    sys.exit(1)
print("ВСЕ ПРОВЕРКИ ПРОЙДЕНЫ")
sys.exit(0)
