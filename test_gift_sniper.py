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

# Ключевой кейс безопасности: читаемый другими ключ обязан быть отвергнут.
gs.WALLET_KEY_FILE = key_path
os.chmod(key_path, 0o644)
key, err = gs.load_wallet_key()
check("ключ с правами 0644 отвергается", key is None, f"err={err}")
check("в ошибке названа причина и лечение",
      err and "chmod 600" in err, err)
check("сам ключ в текст ошибки НЕ попал",
      err and "SECRET_KEY_MATERIAL" not in err, err)

os.chmod(key_path, 0o600)
key, err = gs.load_wallet_key()
check("ключ с правами 0600 читается", key == "SECRET_KEY_MATERIAL_DO_NOT_LEAK", f"err={err}")

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

# Живой режим при нереализованном исполнителе обязан быть заблокирован —
# даже если пользователь выставил всё остальное правильно.
gs.DRY_RUN = False
gs.CONFIRM_LIVE_TRADING = "I_UNDERSTAND_THE_RISK"
gs.BANKROLL_TON = Decimal("100")
gs.COLLECTION_WHITELIST = [friendly]
gs.WALLET_KEY_FILE = key_path
check("живой режим заблокирован без реального исполнителя",
      gs.preflight_checks(require_ai=True) is False)

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
      gs.execute_blockchain_buy("0:x", Decimal("1"), "0:sale") is False)
gs.DRY_RUN = True
check("в симуляции покупка возвращает True",
      gs.execute_blockchain_buy("0:x", Decimal("1"), "0:sale") is True)

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
          gs.execute_blockchain_buy("0:item", Decimal("1"), "0:sale") is True)
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
    def __init__(self, retry_after=None):
        super().__init__({}, status_code=429)
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
        raised = False
    except RuntimeError:
        raised = True
    check("непрерывный 429 в итоге бросает исключение, а не молчит", raised)
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

check("по умолчанию механизм ВЫКЛЮЧЕН",
      gs.MAX_POSITION_PCT_HIGH_ROI == gs.MAX_POSITION_PCT,
      f"{gs.MAX_POSITION_PCT_HIGH_ROI} vs {gs.MAX_POSITION_PCT}")

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
print("\n" + "=" * 60)
if _failures:
    print(f"ПРОВАЛЕНО: {len(_failures)} проверок -> {_failures}")
    sys.exit(1)
print("ВСЕ ПРОВЕРКИ ПРОЙДЕНЫ")
sys.exit(0)
