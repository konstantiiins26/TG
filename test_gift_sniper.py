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
gs.TARGET_COLLECTION = friendly

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
      gs.execute_blockchain_buy("0:x", Decimal("1")) is False)
gs.DRY_RUN = True
check("в симуляции покупка возвращает True",
      gs.execute_blockchain_buy("0:x", Decimal("1")) is True)

(gs.DRY_RUN, gs.CONFIRM_LIVE_TRADING, gs.REAL_EXECUTOR_AVAILABLE) = (_odry, _oconf, _oexec)
(gs.WALLET_KEY_FILE, gs.BANKROLL_TON, gs.COLLECTION_WHITELIST) = (_okey, _obank, _owl)
gs.ANTHROPIC_API_KEY = _oapi


# =============================================================================
print("\n" + "=" * 60)
if _failures:
    print(f"ПРОВАЛЕНО: {len(_failures)} проверок -> {_failures}")
    sys.exit(1)
print("ВСЕ ПРОВЕРКИ ПРОЙДЕНЫ")
sys.exit(0)
