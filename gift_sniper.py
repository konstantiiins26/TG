#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
=============================================================================
 TELEGRAM GIFTS NFT SNIPER  ·  Автоматический снайпер и ИИ-аналитик рынка
=============================================================================

Монолитное приложение, которое:

  1. Собирает актуальные лоты NFT-подарков Telegram с публичного рынка
     (TonAPI.io как основной источник + Getgems GraphQL как запасной).
  2. Отдаёт собранный JSON-пакет "ИИ-мозгу" (Claude API), который считает
     чистую прибыль, отсекает хайп/переплату за некрасивый минт и выдаёт
     строгий вердикт BUY / SKIP.
  3. Крутится в бесконечном цикле мониторинга (10–15 сек) и логирует
     каждую проверку и вердикт по каждому подарку.
  4. При команде BUY вызывает ЗАГЛУШКУ покупки (реальная подпись транзакции
     НЕ выполняется — средства в безопасности до ваших тестов).

-----------------------------------------------------------------------------
 БЫСТРЫЙ СТАРТ
-----------------------------------------------------------------------------
   pip install anthropic requests
   export ANTHROPIC_API_KEY="sk-ant-..."
   export TONAPI_KEY="..."                # опционально, но повышает лимиты
   export TARGET_COLLECTION="EQ...."      # адрес коллекции подарков
   python gift_sniper.py

Все ключи и параметры можно вписать прямо в блок CONFIG ниже — тогда
переменные окружения не нужны, достаточно нажать "запустить".
=============================================================================
"""

import os
import re
import sys
import json
import time
import base64
import sqlite3
import logging
import argparse
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

# --- Сторонние зависимости -------------------------------------------------
# requests   -> HTTP-запросы к рыночным API
# anthropic  -> официальный SDK для Claude API (ИИ-мозг)
try:
    import requests
except ImportError:
    sys.exit("[FATAL] Нет модуля 'requests'. Установите:  pip install requests")

# anthropic нужен ТОЛЬКО для торговли. Диагностика (--probe), ранжирование
# (--rank) и бэктест обязаны работать без него: падать на импорте при запуске
# --probe значит требовать ключ Claude ради проверки адреса.
try:
    from anthropic import Anthropic
    # Типизированные исключения SDK — ловим их отдельно от сетевых ошибок.
    from anthropic import (
        APIStatusError,
        RateLimitError,
        APIConnectionError,
    )
    ANTHROPIC_AVAILABLE = True
except ImportError:
    ANTHROPIC_AVAILABLE = False
    Anthropic = None

    # Заглушки, чтобы except-блоки ниже оставались валидными без SDK.
    class APIStatusError(Exception):
        pass

    class RateLimitError(Exception):
        pass

    class APIConnectionError(Exception):
        pass


# =============================================================================
# 1. CONFIG — ВСЕ ЧУВСТВИТЕЛЬНЫЕ ДАННЫЕ И ПАРАМЕТРЫ В ОДНОМ МЕСТЕ
# =============================================================================
# Правило безопасности: секреты берём из переменных окружения. Значение
# по умолчанию (второй аргумент os.getenv) можно временно вписать прямо сюда
# для локального теста — но НИКОГДА не коммитьте реальные ключи в git.
# -----------------------------------------------------------------------------

# --- Ключи / секреты ---------------------------------------------------------
ANTHROPIC_API_KEY   = os.getenv("ANTHROPIC_API_KEY", "")          # ключ Claude API (обязателен)
TONAPI_KEY          = os.getenv("TONAPI_KEY", "")                 # ключ TonAPI.io (опционален, но желателен)
WALLET_PRIVATE_KEY  = os.getenv("WALLET_PRIVATE_KEY", "")         # приватный ключ кошелька — используется ТОЛЬКО заглушкой

# --- Рыночные параметры ------------------------------------------------------
# Адрес коллекции подарков, которую мониторим (raw или user-friendly формат).
# Пример коллекции Telegram-подарков задайте свой:
# Несколько коллекций через запятую. У Telegram Gifts каждый тип подарка —
# отдельный контракт со своим floor, поэтому "смотреть все подарки" означает
# "перечислить нужные коллекции". Одного адреса на все подарки не существует.
#
# ВНИМАНИЕ про нагрузку: за цикл уходит FLOOR_SAMPLE_PAGES запросов НА КАЖДУЮ
# коллекцию. 10 коллекций x 5 страниц при интервале 12 сек = 250 запросов/мин,
# это гарантированный 429. Считайте: collections x pages x (60/interval).
TARGET_COLLECTIONS = [a.strip() for a in os.getenv(
    "TARGET_COLLECTIONS",
    os.getenv("TARGET_COLLECTION", "")).split(",") if a.strip()]

# Обратная совместимость: одиночная переменная всё ещё работает.
TARGET_COLLECTION   = TARGET_COLLECTIONS[0] if TARGET_COLLECTIONS else ""

# --- Модель ИИ ---------------------------------------------------------------
# ВНИМАНИЕ: 'claude-3-5-sonnet' — устаревший (legacy) идентификатор.
# По умолчанию используем актуальную быструю и недорогую модель, идеально
# подходящую для цикла опроса раз в 10–15 сек. Переопределяется одной
# переменной окружения CLAUDE_MODEL при необходимости.
CLAUDE_MODEL        = os.getenv("CLAUDE_MODEL", "claude-sonnet-5")

# --- Экономика сделки -------------------------------------------------------
# ВАЖНО: комиссия площадки и роялти берутся С ЦЕНЫ ПРОДАЖИ, а не с цены покупки.
# (В первоначальном ТЗ было `Buy * 0.05` — это занижало издержки и завышало
#  прибыль, т.к. в прибыльной сделке цена продажи выше цены покупки.)
MARKETPLACE_FEE_PCT = Decimal(os.getenv("MARKETPLACE_FEE_PCT", "0.05"))   # комиссия площадки (с цены ПРОДАЖИ)
ROYALTY_PCT         = Decimal(os.getenv("ROYALTY_PCT", "0.05"))           # роялти создателю коллекции (с цены ПРОДАЖИ)
UNDERCUT_PCT        = Decimal(os.getenv("UNDERCUT_PCT", "0.03"))          # насколько встаём НИЖЕ floor, чтобы реально продать
GAS_FEE_TON         = Decimal(os.getenv("GAS_FEE_TON", "0.15"))           # газ за круг (покупка + продажа), TON
MIN_ROI_PCT         = Decimal(os.getenv("MIN_ROI_PCT", "5"))              # ниже этого ROI бот не покупает

# Премия за редкость: во сколько раз редкий лот продаётся дороже floor.
# ПО УМОЛЧАНИЮ 1.0 — премии НЕТ, и это осознанно. Любое значение выше 1.0
# УВЕЛИЧИВАЕТ расчётную прибыль, то есть двигает бота в сторону "покупай" —
# ровно туда, где теряют деньги. Поднимать это число можно только после того,
# как вы откалибруете его по СВОЕЙ записи рынка (--record + --backtest),
# а не потому, что "редкое вроде дороже стоит".
# Применяется к лотам с красивым номером ИЛИ редкими трейтами.
PREMIUM_MULT        = Decimal(os.getenv("PREMIUM_MULT", "1.0"))

# --- Расчёт Floor Price -----------------------------------------------------
# У TonAPI-эндпоинта /items НЕТ сортировки по цене, поэтому честный floor
# получается только выборкой: тянем несколько страниц и берём перцентиль.
#
# ЦЕНА ВОПРОСА — нагрузка на API: FLOOR_SAMPLE_PAGES запросов КАЖДЫЙ цикл.
# При 5 страницах и интервале 12 сек это ~25 запросов/мин. Без ключа TONAPI_KEY
# вы упрётесь в лимиты. Если ловите 429 — поднимите POLL_INTERVAL_SEC
# или уменьшите FLOOR_SAMPLE_PAGES (ценой точности floor).
FLOOR_PAGE_SIZE     = int(os.getenv("FLOOR_PAGE_SIZE", "100"))            # размер одной страницы выборки
FLOOR_SAMPLE_PAGES  = int(os.getenv("FLOOR_SAMPLE_PAGES", "5"))           # сколько страниц тянуть (5 x 100 = 500 лотов)
FLOOR_PERCENTILE    = Decimal(os.getenv("FLOOR_PERCENTILE", "5"))         # 5-й перцентиль вместо голого min()
MIN_FLOOR_SAMPLE    = int(os.getenv("MIN_FLOOR_SAMPLE", "8"))             # меньше этого — floor недостоверен, не торгуем
FLOOR_CACHE_TTL_SEC = int(os.getenv("FLOOR_CACHE_TTL_SEC", "60"))         # кэш floor, чтобы не сканировать рынок каждые 12 сек
CANDIDATES_TO_ANALYZE = int(os.getenv("CANDIDATES_TO_ANALYZE", "5"))      # сколько самых дешёвых лотов отдавать ИИ

# --- Верификация коллекции (защита от скам-коллекций) -----------------------
# Скам-коллекции копируют имя и картинки один в один; доверять можно ТОЛЬКО адресу.
# Список адресов через запятую. Пустой список = проверка выключена (бот предупредит).
COLLECTION_WHITELIST = [a.strip() for a in os.getenv("COLLECTION_WHITELIST", "").split(",") if a.strip()]

# --- Параметры цикла мониторинга --------------------------------------------
POLL_INTERVAL_SEC   = int(os.getenv("POLL_INTERVAL_SEC", "12"))           # задержка между проверками (10–15 сек)
HTTP_TIMEOUT_SEC    = int(os.getenv("HTTP_TIMEOUT_SEC", "15"))           # таймаут HTTP-запросов

# --- Редкость трейтов (Ярус 2) ----------------------------------------------
# Для Telegram Gifts редкость модели/фона/символа влияет на цену СИЛЬНЕЕ, чем
# номер минта. Официального API редкости нет, поэтому частоты трейтов считаются
# по той же выборке, что и floor. Это ОЦЕНКА по выборке, а не истина по всей
# коллекции — чем больше FLOOR_SAMPLE_PAGES, тем она точнее.
RARE_TRAIT_THRESHOLD_PCT = Decimal(os.getenv("RARE_TRAIT_THRESHOLD_PCT", "5"))  # <= N% носителей = редкий
MIN_TRAIT_SAMPLE    = int(os.getenv("MIN_TRAIT_SAMPLE", "50"))            # меньше — редкость не оцениваем

# --- Ликвидность (Ярус 2) ---------------------------------------------------
# Floor ничего не говорит о том, ПРОДАСТСЯ ли лот. Если у floor стоит толпа
# продавцов, ваш undercut встанет в конец очереди и флип не закроется.
COMPETITION_BAND_PCT = Decimal(os.getenv("COMPETITION_BAND_PCT", "10"))    # полоса вокруг floor, %
MAX_COMPETITION     = int(os.getenv("MAX_COMPETITION", "15"))             # больше лотов в полосе — не лезем

# История продаж: эндпоинт истории TonAPI НЕ ПРОВЕРЕН из этой среды (сеть
# закрыта), поэтому по умолчанию ВЫКЛЮЧЕН. Включайте только после того, как
# убедитесь, что ответ парсится — иначе фильтр будет врать.
ENABLE_SALES_HISTORY = os.getenv("ENABLE_SALES_HISTORY", "0") == "1"
LIQUIDITY_WINDOW_HOURS = int(os.getenv("LIQUIDITY_WINDOW_HOURS", "72"))   # окно наблюдения продаж
MIN_SALES_IN_WINDOW = int(os.getenv("MIN_SALES_IN_WINDOW", "1"))          # меньше сделок — рынок мёртв

# --- Дедупликация (Ярус 2) --------------------------------------------------
# Без неё один и тот же неизменившийся лот уходит в Claude каждые 12 секунд.
# Это прямые деньги за API и мусор в логах.
SEEN_TTL_SEC        = int(os.getenv("SEEN_TTL_SEC", "300"))               # не переспрашивать ИИ N секунд

# --- "Красивые" номера минта, за которые наценка оправдана -------------------
# Топ-100 (номер <= 100) + классические reepeat-digit / lucky номера.
PRETTY_MINTS        = {7, 77, 777, 7777, 111, 1111, 11111,
                       555, 5555, 55555, 888, 8888, 999, 9999,
                       123, 1234, 12345, 100, 1000, 10000, 100000}

# --- Константы TON -----------------------------------------------------------
NANO_PER_TON        = Decimal("1000000000")   # 1 TON = 1e9 нанотонов

# --- Риск-лимиты (Ярус 3) ----------------------------------------------------
# Главная защита от "бот сошёл с ума и слил депозит". Лимиты хранятся в БД,
# поэтому ПЕРЕЗАПУСК ИХ НЕ СБРАСЫВАЕТ — иначе они не лимиты, а декорация.
MAX_SPEND_PER_TRADE_TON = Decimal(os.getenv("MAX_SPEND_PER_TRADE_TON", "50"))    # потолок одной сделки
MAX_SPEND_PER_HOUR_TON  = Decimal(os.getenv("MAX_SPEND_PER_HOUR_TON", "200"))    # потолок трат за час
MAX_SPEND_PER_DAY_TON   = Decimal(os.getenv("MAX_SPEND_PER_DAY_TON", "1000"))    # потолок трат за сутки
MAX_OPEN_POSITIONS      = int(os.getenv("MAX_OPEN_POSITIONS", "10"))             # сколько лотов держим одновременно
STOP_AFTER_LOSSES       = int(os.getenv("STOP_AFTER_LOSSES", "3"))               # N убытков подряд -> стоп торговли

# --- БАНКРОЛЛ (Ярус 4) -------------------------------------------------------
# "Банк" — сумма, которой боту разрешено оперировать. Задаётся ВАМИ, деньги
# лежат на ВАШЕМ кошельке; бот только считает, сколько из них уже в позициях.
BANKROLL_TON        = Decimal(os.getenv("BANKROLL_TON", "0"))             # 0 = банк не задан, торговля запрещена
RESERVE_TON         = Decimal(os.getenv("RESERVE_TON", "5"))              # неснижаемый остаток (газ, комиссии)
MAX_POSITION_PCT    = Decimal(os.getenv("MAX_POSITION_PCT", "10"))        # максимум % банка в одной сделке

# --- КОШЕЛЁК (Ярус 4) --------------------------------------------------------
# Ключ читается ТОЛЬКО из файла с правами 0600, не из переменной окружения:
# env видно в `ps`, в логах оркестратора и в дампах контейнера.
# Ключ НИКОГДА не логируется и не попадает в БД, уведомления или git.
WALLET_KEY_FILE     = os.getenv("WALLET_KEY_FILE", "")
TRADING_NETWORK     = os.getenv("TRADING_NETWORK", "testnet").lower()     # testnet | mainnet
# Осознанный speed bump: без этой строки живая торговля не запустится.
CONFIRM_LIVE_TRADING = os.getenv("CONFIRM_LIVE_TRADING", "")

# Реальная подпись транзакций НЕ реализована (см. SETUP.md, раздел "Что ещё
# не сделано"). Флаг существует, чтобы preflight мог ЗАПРЕТИТЬ живой режим,
# а не чтобы бот думал, будто купил, ничего не купив.
REAL_EXECUTOR_AVAILABLE = False

# --- Хранилище состояния (Ярус 3) --------------------------------------------
# SQLite: позиции, траты, PnL. Нужен именно файл, а не память в процессе —
# лимиты и учёт позиций обязаны переживать рестарт.
DB_PATH             = os.getenv("DB_PATH", "sniper_state.db")

# --- Уведомления в Telegram (Ярус 3) -----------------------------------------
# Необязательны: без токена бот просто не шлёт уведомления и работает дальше.
TELEGRAM_BOT_TOKEN  = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID    = os.getenv("TELEGRAM_CHAT_ID", "")

# --- Запись рынка и бэктест (Ярус 3) -----------------------------------------
# Исторического API у нас нет, поэтому бэктест гоняется по СОБСТВЕННОЙ записи
# рынка: сначала --record несколько дней, потом --backtest по этому файлу.
RECORD_PATH         = os.getenv("RECORD_PATH", "market_history.jsonl")
BACKTEST_HOLD_HOURS = int(os.getenv("BACKTEST_HOLD_HOURS", "24"))   # через сколько часов "продаём"

# --- Служебное ---------------------------------------------------------------
DRY_RUN             = os.getenv("DRY_RUN", "1") == "1"   # 1 = только заглушка покупки (безопасно)


# =============================================================================
# 2. ЛОГИРОВАНИЕ (цветной вывод в консоль)
# =============================================================================

class _Color:
    """ANSI-коды для цветного логирования в терминале."""
    GREEN  = "\033[92m"
    RED    = "\033[91m"
    YELLOW = "\033[93m"
    CYAN   = "\033[96m"
    GREY   = "\033[90m"
    BOLD   = "\033[1m"
    RESET  = "\033[0m"


class _ColorFormatter(logging.Formatter):
    """Раскрашивает уровни логов, чтобы вердикты были видны с первого взгляда."""
    _LEVEL_COLORS = {
        logging.DEBUG:    _Color.GREY,
        logging.INFO:     _Color.CYAN,
        logging.WARNING:  _Color.YELLOW,
        logging.ERROR:    _Color.RED,
        logging.CRITICAL: _Color.RED + _Color.BOLD,
    }

    def format(self, record):
        color = self._LEVEL_COLORS.get(record.levelno, _Color.RESET)
        ts = datetime.now().strftime("%H:%M:%S")
        msg = record.getMessage()
        return f"{_Color.GREY}{ts}{_Color.RESET} {color}{record.levelname:<7}{_Color.RESET} {msg}"


log = logging.getLogger("gift_sniper")
log.setLevel(logging.INFO)
_handler = logging.StreamHandler(sys.stdout)
_handler.setFormatter(_ColorFormatter())
log.addHandler(_handler)


# =============================================================================
# 3. АДРЕСА TON — НОРМАЛИЗАЦИЯ И ВЕРИФИКАЦИЯ КОЛЛЕКЦИИ
# =============================================================================
# Один и тот же адрес TON существует в двух видах:
#   raw:            0:9f8a...e21c   (workchain:hex-хэш)
#   user-friendly:  EQCfio...IeLp   (base64url, 36 байт, с CRC16)
# Наивное сравнение строк их не сматчит, поэтому whitelist без нормализации
# бесполезен. Приводим оба вида к канонической форме "wc:hex".

_RAW_ADDR_RE = re.compile(r"^(-?\d+):([0-9a-fA-F]{64})$")


def _crc16_xmodem(data: bytes) -> int:
    """CRC16/XMODEM — контрольная сумма в user-friendly адресах TON."""
    crc = 0
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ 0x1021) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    return crc


def normalize_ton_address(addr: str):
    """
    Приводит адрес TON к канонической форме "workchain:hex" (нижний регистр).

    Принимает и raw ("0:abc..."), и user-friendly ("EQ...", "UQ...", в любом
    из двух base64-алфавитов). Проверяет длину и CRC16 — битый адрес
    отвергается. Возвращает None, если адрес нераспознан.
    """
    if not addr or not isinstance(addr, str):
        return None
    addr = addr.strip()

    # --- Вариант 1: уже raw-форма -------------------------------------------
    m = _RAW_ADDR_RE.match(addr)
    if m:
        return f"{int(m.group(1))}:{m.group(2).lower()}"

    # --- Вариант 2: user-friendly base64(url) --------------------------------
    if len(addr) != 48:
        return None
    try:
        # Адрес могут записать и в url-safe (-_), и в обычном (+/) алфавите.
        decoded = base64.b64decode(addr.replace("-", "+").replace("_", "/"))
    except Exception:  # noqa: BLE001 — любой мусор просто не адрес
        return None

    if len(decoded) != 36:
        return None
    # Последние 2 байта — CRC16 первых 34. Не сошлось => адрес повреждён.
    if _crc16_xmodem(decoded[:34]) != int.from_bytes(decoded[34:], "big"):
        return None

    workchain = -1 if decoded[1] == 0xFF else decoded[1]
    return f"{workchain}:{decoded[2:34].hex()}"


def is_collection_trusted(collection_addr: str) -> bool:
    """
    Проверяет адрес коллекции по whitelist.

    Скам-коллекции копируют название и картинки байт в байт, поэтому
    доверять `collection_name` нельзя — только адресу контракта.
    Пустой whitelist = проверка отключена (main() об этом предупреждает).
    """
    if not COLLECTION_WHITELIST:
        return True
    target = normalize_ton_address(collection_addr)
    if target is None:
        return False
    return any(normalize_ton_address(a) == target for a in COLLECTION_WHITELIST)


# =============================================================================
# 4. РЫНОК: СБОР ДАННЫХ, РЕДКОСТЬ, ЛИКВИДНОСТЬ, ДЕДУПЛИКАЦИЯ
# =============================================================================

def _nano_to_ton(nano_value) -> Decimal:
    """Конвертирует цену из нанотонов в TON. Безопасно обрабатывает None/мусор."""
    if nano_value in (None, "", "0", 0):
        return Decimal("0")
    try:
        return (Decimal(str(nano_value)) / NANO_PER_TON).quantize(Decimal("0.000000001"))
    except (InvalidOperation, ValueError):
        return Decimal("0")


def fetch_items_tonapi(collection: str, limit: int, offset: int = 0):
    """
    ОСНОВНОЙ ИСТОЧНИК: TonAPI.io
    GET /v2/nfts/collections/{account_address}/items

    Возвращает список нормализованных лотов (см. _normalize_item).
    Работает и без ключа (низкие лимиты), с ключом TONAPI_KEY — выше квота.

    `offset` нужен для постраничной выборки при расчёте честного floor:
    у этого эндпоинта нет сортировки по цене, поэтому floor можно получить
    только достаточно широкой выборкой.
    """
    url = f"https://tonapi.io/v2/nfts/collections/{collection}/items"
    params = {"limit": limit, "offset": offset}
    headers = {"Accept": "application/json"}
    if TONAPI_KEY:
        headers["Authorization"] = f"Bearer {TONAPI_KEY}"

    resp = requests.get(url, params=params, headers=headers, timeout=HTTP_TIMEOUT_SEC)
    resp.raise_for_status()
    data = resp.json()

    items = []
    for nft in data.get("nft_items", []):
        # --- Цена продажи. У TonAPI данные о продаже лежат в nft["sale"]. ---
        sale = nft.get("sale") or {}
        price_block = sale.get("price") or {}
        sale_price_nano = price_block.get("value")          # строка нанотонов, если лот выставлен
        sale_price_ton = _nano_to_ton(sale_price_nano)

        # --- Номер минта. Ищем в метаданных / атрибутах разными способами. ---
        meta = nft.get("metadata") or {}
        mint_index = _extract_mint_index(nft, meta)

        coll = nft.get("collection") or {}
        items.append(_normalize_item(
            traits=extract_traits(meta),
            explicit_rarity_pct=_explicit_rarity_pct(meta),
            address=nft.get("address", ""),
            collection_name=coll.get("name") or meta.get("name", "Unknown"),
            # Адрес коллекции — единственное, чему можно доверять при проверке
            # на скам (имя подделывается тривиально).
            collection_address=coll.get("address", ""),
            mint_index=mint_index,
            sale_price_ton=sale_price_ton,
            is_on_sale=bool(sale_price_nano and sale_price_ton > 0),
        ))
    return items


def fetch_items_getgems(collection: str, limit: int):
    """
    ЗАПАСНОЙ ИСТОЧНИК: Getgems GraphQL.
    Вызывается, если TonAPI недоступен. Схема Getgems периодически меняется,
    поэтому запрос максимально консервативный; при неудаче — бросаем исключение.
    """
    url = "https://api.getgems.io/graphql"
    query = """
    query CollectionItems($address: String!, $first: Int!) {
      alphaNftItemSale(collectionAddress: $address, first: $first) {
        edges {
          node {
            address
            name
            index
            sale { ... on NftSaleFixPrice { fullPrice } }
          }
        }
      }
    }
    """
    payload = {"query": query, "variables": {"address": collection, "first": limit}}
    resp = requests.post(url, json=payload, timeout=HTTP_TIMEOUT_SEC)
    resp.raise_for_status()
    data = resp.json()

    edges = (((data.get("data") or {}).get("alphaNftItemSale") or {}).get("edges")) or []
    items = []
    for edge in edges:
        node = edge.get("node") or {}
        sale = node.get("sale") or {}
        price_nano = sale.get("fullPrice")
        price_ton = _nano_to_ton(price_nano)
        items.append(_normalize_item(
            address=node.get("address", ""),
            collection_name=node.get("name", "Unknown"),
            # Getgems-запрос не возвращает адрес коллекции, поэтому подставляем
            # тот, который сами запрашивали — для whitelist этого достаточно.
            collection_address=collection,
            mint_index=node.get("index"),
            sale_price_ton=price_ton,
            is_on_sale=bool(price_nano and price_ton > 0),
        ))
    return items


def _extract_mint_index(nft: dict, meta: dict):
    """
    Пытается достать номер минта из разных мест метаданных TonAPI:
      - meta["mint_index"] / meta["index"]
      - атрибут с trait_type ~ 'number'/'mint'/'index'
      - число в конце названия ("Gift #777" -> 777)
    Возвращает int или None, если номер не найден.
    """
    for key in ("mint_index", "index", "number"):
        if isinstance(meta.get(key), (int, str)) and str(meta.get(key)).isdigit():
            return int(meta[key])

    for attr in meta.get("attributes", []) or []:
        trait = str(attr.get("trait_type", "")).lower()
        val = attr.get("value")
        if any(k in trait for k in ("number", "mint", "index")) and str(val).isdigit():
            return int(val)

    # Фолбэк: число после '#' в названии.
    name = str(meta.get("name", ""))
    if "#" in name:
        tail = name.split("#")[-1].strip()
        digits = "".join(ch for ch in tail if ch.isdigit())
        if digits:
            return int(digits)
    return None


def _normalize_item(address, collection_name, collection_address,
                    mint_index, sale_price_ton, is_on_sale,
                    traits=None, explicit_rarity_pct=None):
    """Единый формат лота для всего приложения (и для отправки в ИИ)."""
    return {
        "address": address,
        "collection_name": collection_name,
        "collection_address": collection_address,
        "mint_index": mint_index,
        "sale_price_ton": float(sale_price_ton),   # float для JSON-сериализации в ИИ
        "is_on_sale": is_on_sale,
        # Трейты нужны для оценки редкости — она влияет на цену Telegram Gifts
        # сильнее номера минта.
        "traits": traits or {},
        "explicit_rarity_pct": explicit_rarity_pct,
    }


# --- Трейты и редкость -------------------------------------------------------
# Служебные атрибуты, которые НЕ являются признаком редкости (это номер, а не
# характеристика). Номер минта оценивается отдельно, в is_pretty_mint().
# Сравнение идёт ПО СЛОВАМ, а не по подстроке: подстрочный матч выбрасывал
# легитимные трейты ("Sidekick" и "Rider" содержат "id").
_SKIP_TRAITS = {"number", "mint", "index", "serial", "id", "rarity", "no", "num"}


def extract_traits(meta: dict) -> dict:
    """
    Достаёт характеристики подарка из metadata.attributes:
    {"model": "Plush Pepe", "backdrop": "Onyx Black", "symbol": "Skull"}.

    Служебные числовые атрибуты (номер/индекс) пропускаем — они про
    нумерацию, а не про редкость.
    """
    traits = {}
    for attr in meta.get("attributes", []) or []:
        name = str(attr.get("trait_type", "")).strip().lower()
        value = attr.get("value")
        if not name or value is None:
            continue
        if set(re.split(r"[^a-z0-9]+", name)) & _SKIP_TRAITS:
            continue
        traits[name] = str(value).strip()
    return traits


def _explicit_rarity_pct(meta: dict):
    """
    Если площадка сама отдала процент редкости — доверяем ему больше, чем
    нашей оценке по выборке. Ищем атрибут с 'rarity' в названии.
    Возвращает Decimal (проценты) или None.
    """
    for attr in meta.get("attributes", []) or []:
        name = str(attr.get("trait_type", "")).strip().lower()
        if "rarity" not in name:
            continue
        raw = str(attr.get("value", "")).replace("%", "").strip()
        try:
            val = Decimal(raw)
        except (InvalidOperation, ValueError):
            continue
        if Decimal("0") < val <= Decimal("100"):
            return val
    return None


def build_trait_index(items):
    """
    Считает частоты значений трейтов по выборке:
        {"backdrop": {"Onyx Black": 3, "Sky Blue": 140}, ...}

    Возвращает (index, total_items). Это ОЦЕНКА по выборке — официального
    API редкости нет, поэтому точность растёт с размером выборки.
    """
    index, total = {}, 0
    for item in items:
        traits = item.get("traits") or {}
        if not traits:
            continue
        total += 1
        for name, value in traits.items():
            index.setdefault(name, {})
            index[name][value] = index[name].get(value, 0) + 1
    return index, total


def compute_rarity_pct(item: dict, index: dict, total: int):
    """
    Оценивает редкость лота как долю (в %) носителей его САМОГО РЕДКОГО трейта.
    Меньше процент — реже подарок.

    Приоритет: явный процент от площадки > оценка по выборке.
    Возвращает (rarity_pct | None, имя_редчайшего_трейта | None).
    None означает "оценить не удалось" — это НЕ то же самое, что "обычный".
    """
    explicit = item.get("explicit_rarity_pct")
    if explicit is not None:
        return explicit, "explicit"

    traits = item.get("traits") or {}
    if not traits or total < MIN_TRAIT_SAMPLE:
        return None, None

    rarest_pct, rarest_name = None, None
    for name, value in traits.items():
        counts = index.get(name)
        if not counts:
            continue
        seen = counts.get(value)
        if not seen:
            continue
        pct = (Decimal(seen) / Decimal(total) * Decimal("100"))
        if rarest_pct is None or pct < rarest_pct:
            rarest_pct, rarest_name = pct, name

    if rarest_pct is None:
        return None, None
    return rarest_pct.quantize(Decimal("0.01")), rarest_name


def is_rare(rarity_pct) -> bool:
    """Редкий = носителей не больше RARE_TRAIT_THRESHOLD_PCT процентов."""
    return rarity_pct is not None and rarity_pct <= RARE_TRAIT_THRESHOLD_PCT


# --- Ликвидность -------------------------------------------------------------

def compute_competition(prices, floor: Decimal) -> int:
    """
    Сколько лотов стоит в пределах COMPETITION_BAND_PCT процентов от floor.

    Зачем: floor говорит "по какой цене висит самый дешёвый", но ничего не
    говорит о том, продастся ли ваш лот. Если у floor стоит толпа, ваш
    undercut окажется в очереди и флип не закроется — прибыль на бумаге
    так и останется на бумаге.
    """
    if floor <= 0:
        return 0
    band = floor * (Decimal("1") + COMPETITION_BAND_PCT / Decimal("100"))
    return sum(1 for pr in prices if pr <= band)


def fetch_recent_sales_count(collection: str):
    """
    Считает продажи коллекции за LIQUIDITY_WINDOW_HOURS часов.

    ВНИМАНИЕ: схема этого эндпоинта TonAPI НЕ ПРОВЕРЕНА (из среды разработки
    не было сетевого доступа). Поэтому функция выключена по умолчанию
    (ENABLE_SALES_HISTORY=0) и при любой неожиданности возвращает None
    вместо выдуманного числа.

    Возвращает int (число сделок) или None, если данные недоступны.
    """
    if not ENABLE_SALES_HISTORY:
        return None

    url = f"https://tonapi.io/v2/nfts/collections/{collection}/history"
    headers = {"Accept": "application/json"}
    if TONAPI_KEY:
        headers["Authorization"] = f"Bearer {TONAPI_KEY}"

    cutoff = time.time() - LIQUIDITY_WINDOW_HOURS * 3600
    try:
        resp = requests.get(url, params={"limit": 100}, headers=headers,
                            timeout=HTTP_TIMEOUT_SEC)
        resp.raise_for_status()
        events = resp.json().get("events", [])
    except Exception as e:  # noqa: BLE001 — нет данных лучше, чем выдуманные
        log.warning(f"История продаж недоступна ({e}). Фильтр ликвидности пропущен.")
        return None

    sales = 0
    for ev in events:
        if ev.get("timestamp", 0) < cutoff:
            continue
        # Продажей считаем событие, в котором участвовал контракт продажи.
        for action in ev.get("actions", []) or []:
            if "Sale" in str(action.get("type", "")) or action.get("NftPurchase"):
                sales += 1
                break
    return sales


# --- Дедупликация ------------------------------------------------------------
# {"<address>:<price>": timestamp}. Один и тот же неизменившийся лот не должен
# уходить в Claude каждые 12 секунд — это прямые деньги за API.
_seen_cache = {}


def _floor_bucket(floor: Decimal) -> str:
    """
    Огрубляет floor до 3 значащих цифр (~1% шаг).

    Нужно для ключа дедупликации: floor всё время немного дрожит, и без
    огрубления кэш сбрасывался бы каждый цикл. Но при РЕАЛЬНОМ сдвиге floor
    вердикт обязан пересчитаться — лот, который был SKIP при floor=10,
    вполне может стать BUY при floor=15.
    """
    try:
        return f"{float(floor):.3g}"
    except (ValueError, OverflowError):
        return "0"


def already_analyzed(item: dict, floor: Decimal) -> bool:
    """
    True, если этот лот по ЭТОЙ ЖЕ цене и при ТОМ ЖЕ floor уже анализировался
    недавно.

    Ключ включает цену и floor: и то и другое — новое торговое событие,
    которое нужно пересчитать, даже если лот тот же.
    """
    key = f"{item['address']}:{item['sale_price_ton']}:{_floor_bucket(floor)}"
    now = time.monotonic()

    # Попутно чистим протухшие записи, чтобы словарь не рос бесконечно.
    for k in [k for k, ts in _seen_cache.items() if now - ts > SEEN_TTL_SEC]:
        _seen_cache.pop(k, None)

    if now - _seen_cache.get(key, -1e9) < SEEN_TTL_SEC:
        return True
    _seen_cache[key] = now
    return False


def _percentile(sorted_vals, pct: Decimal) -> Decimal:
    """
    Линейно интерполированный перцентиль по ОТСОРТИРОВАННОМУ списку Decimal.

    Зачем не min(): один случайный "пылевой" лот (например, выставленный
    по ошибке за 0.01 TON) утащил бы floor вниз и сделал бы все расчёты
    прибыли бессмысленными. Перцентиль устойчив к таким выбросам.
    """
    if not sorted_vals:
        return Decimal("0")
    if len(sorted_vals) == 1:
        return sorted_vals[0]

    k = (Decimal(len(sorted_vals) - 1) * pct) / Decimal("100")
    lo = int(k)
    hi = min(lo + 1, len(sorted_vals) - 1)
    if lo == hi:
        return sorted_vals[lo]
    frac = k - Decimal(lo)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * frac


# Кэш floor: {collection_addr: (timestamp, floor, sample_size)}.
# Внимание: это НЕ экономия запросов. Выборку приходится собирать каждый цикл,
# иначе не увидишь новые дешёвые листинги — а именно ради них бот и работает.
# Кэш нужен для другого: пережить цикл, в котором рынок отдался частично
# (часть страниц не пришла), не подменив рабочий floor случайным огрызком.
_floor_cache = {}


def _empty_snapshot(source="none"):
    """Пустой снапшот — чтобы вызывающий код не разбирал особые случаи."""
    return {"collection": "", "candidates": [], "floor": Decimal("0"),
            "sample_size": 0, "source": source, "trait_index": {}, "trait_total": 0,
            "competition": 0, "floor_reliable": False}


def get_market_snapshot(collection: str) -> dict:
    """
    Снимок рынка одним словарём:

      candidates     — самые дешёвые лоты на продажу (кандидаты на покупку)
      floor          — ЧЕСТНЫЙ floor: перцентиль по широкой выборке, не min()
      sample_size    — сколько лотов на продажу попало в выборку
      floor_reliable — достаточна ли выборка, чтобы floor чему-то соответствовал
      trait_index    — частоты трейтов по выборке (основа оценки редкости)
      trait_total    — сколько лотов с трейтами попало в индекс
      competition    — сколько продавцов стоит вплотную к floor (ликвидность)

    Почему выборка: у TonAPI /items нет сортировки по цене, он отдаёт
    элементы по индексу. Поэтому тянем FLOOR_SAMPLE_PAGES страниц и считаем
    перцентиль по собранным ценам.
    """
    cache_key = normalize_ton_address(collection) or collection
    cached = _floor_cache.get(cache_key)
    now = time.monotonic()

    all_items, source = _collect_sample(collection)
    if not all_items:
        return _empty_snapshot()

    on_sale = [it for it in all_items if it["is_on_sale"] and it["sale_price_ton"] > 0]
    prices = sorted(Decimal(str(it["sale_price_ton"])) for it in on_sale)

    if cached and (now - cached[0]) < FLOOR_CACHE_TTL_SEC and len(prices) < MIN_FLOOR_SAMPLE:
        # Выборка в этот раз вышла бедной — доверяем недавнему floor.
        floor, sample_size = cached[1], cached[2]
    else:
        floor = _percentile(prices, FLOOR_PERCENTILE).quantize(Decimal("0.000000001"))
        sample_size = len(prices)
        if sample_size >= MIN_FLOOR_SAMPLE:
            _floor_cache[cache_key] = (now, floor, sample_size)

    # Индекс редкости строим по ВСЕЙ выборке, а не только по лотам на продаже:
    # редкость — свойство коллекции, а не текущих листингов.
    trait_index, trait_total = build_trait_index(all_items)

    return {
        # Без этого поля снапшоты разных коллекций в записи неразличимы,
        # и бэктест подставил бы floor чужой коллекции.
        "collection": cache_key,
        "candidates": sorted(on_sale, key=lambda it: it["sale_price_ton"])[:CANDIDATES_TO_ANALYZE],
        "floor": floor,
        "sample_size": sample_size,
        "source": source,
        "trait_index": trait_index,
        "trait_total": trait_total,
        "competition": compute_competition(prices, floor),
        "floor_reliable": sample_size >= MIN_FLOOR_SAMPLE and floor > 0,
    }


def _collect_sample(collection: str):
    """
    Тянет FLOOR_SAMPLE_PAGES страниц с TonAPI; при полном провале — один
    запрос к Getgems. Возвращает (items, source).

    Частичный успех допустим: если 3 страницы из 5 пришли, работаем с ними
    и пишем предупреждение — это лучше, чем потерять весь цикл.
    """
    items, failures = [], 0
    for page in range(FLOOR_SAMPLE_PAGES):
        try:
            batch = fetch_items_tonapi(collection, FLOOR_PAGE_SIZE, offset=page * FLOOR_PAGE_SIZE)
        except Exception as e:  # noqa: BLE001 — страница могла не прийти, это не фатально
            failures += 1
            log.warning(f"TonAPI: страница {page + 1}/{FLOOR_SAMPLE_PAGES} не получена ({e}).")
            continue
        if not batch:
            break           # коллекция закончилась — дальше тянуть нечего
        items.extend(batch)

    if items:
        if failures:
            log.warning(f"Выборка неполная: {failures} страниц потеряно. Floor менее точен.")
        return items, "TonAPI"

    # --- Полный провал TonAPI: пробуем Getgems -----------------------------
    log.warning("TonAPI недоступен полностью. Переключаюсь на Getgems...")
    try:
        return fetch_items_getgems(collection, FLOOR_PAGE_SIZE), "Getgems"
    except Exception as e:  # noqa: BLE001
        log.error(f"Оба источника недоступны. Getgems: {e}")
        return [], "none"


# =============================================================================
# 5. ЛОКАЛЬНАЯ ЭКОНОМИКА (быстрая проверка до вызова ИИ)
# =============================================================================

def target_sale_price(floor_price: Decimal, premium: bool = False) -> Decimal:
    """
    Цена, по которой мы реально сможем продать.

    Продать ПО floor нельзя: чтобы уйти первым, надо встать ниже текущего
    минимума. Поэтому целевая цена = floor минус undercut.

    `premium` (красивый номер или редкие трейты) умножает оценку на
    PREMIUM_MULT. По умолчанию он равен 1.0 — то есть красота и редкость
    НЕ повышают оценку и на решение не влияют. Это сознательно: премия —
    предположение, а не факт, и калибровать её нужно по своей записи рынка.
    """
    base = floor_price * PREMIUM_MULT if premium else floor_price
    return base * (Decimal("1") - UNDERCUT_PCT)


def compute_net_profit(floor_price: Decimal, buy_price: Decimal,
                       premium: bool = False) -> Decimal:
    """
    Честная формула чистой прибыли:

        Sale     = Floor * (1 - undercut)
        Proceeds = Sale - Sale*fee - Sale*royalty
        Profit   = Proceeds - Buy - gas

    Отличия от первоначального ТЗ (`(Floor-Buy) - Buy*0.05 - gas`):
      1. комиссия берётся с цены ПРОДАЖИ, а не покупки (так работают площадки);
      2. добавлено роялти создателю коллекции;
      3. продаём с undercut'ом, а не ровно по floor.
    Все три правки смещают оценку в консервативную сторону — раньше бот
    систематически завышал прибыль.
    """
    sale = target_sale_price(floor_price, premium)
    proceeds = sale * (Decimal("1") - MARKETPLACE_FEE_PCT - ROYALTY_PCT)
    return proceeds - buy_price - GAS_FEE_TON


def compute_roi_pct(net_profit: Decimal, buy_price: Decimal) -> Decimal:
    """ROI в процентах относительно вложенной суммы."""
    if buy_price <= 0:
        return Decimal("0")
    return (net_profit / buy_price * Decimal("100")).quantize(Decimal("0.01"))


def is_pretty_mint(mint_index) -> bool:
    """
    "Красивый" ли номер минта:
      - входит в топ-100 (<= 100), ИЛИ
      - в списке классических красивых номеров (777, 1111, 5555 и т.п.).
    """
    if mint_index is None:
        return False
    return mint_index <= 100 or mint_index in PRETTY_MINTS


# =============================================================================
# 6. ХРАНИЛИЩЕ, РИСК-ЛИМИТЫ И УВЕДОМЛЕНИЯ
# =============================================================================
# Почему SQLite, а не переменные в памяти: лимиты, которые обнуляются при
# рестарте, — это не лимиты. Упавший и перезапущенный бот обязан помнить,
# что он уже потратил и сколько убытков подряд поймал.

def db_connect():
    """Соединение с БД состояния. Таймаут — на случай параллельного доступа."""
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def db_init():
    """Создаёт схему, если её ещё нет. Безопасно вызывать при каждом запуске."""
    with db_connect() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS positions (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                address           TEXT NOT NULL,
                collection        TEXT,
                buy_price_ton     TEXT NOT NULL,   -- TEXT: Decimal без потерь float
                buy_ts            REAL NOT NULL,
                floor_at_buy_ton  TEXT,
                status            TEXT NOT NULL DEFAULT 'open',
                sell_price_ton    TEXT,
                sell_ts           REAL,
                pnl_ton           TEXT
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_buy_ts ON positions(buy_ts)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_status ON positions(status)")


def record_purchase(item: dict, buy_price: Decimal, floor: Decimal) -> int:
    """Фиксирует открытую позицию. Возвращает её id."""
    with db_connect() as conn:
        cur = conn.execute(
            "INSERT INTO positions (address, collection, buy_price_ton, buy_ts, "
            "floor_at_buy_ton, status) VALUES (?, ?, ?, ?, ?, 'open')",
            (item["address"], item.get("collection_address", ""),
             str(buy_price), time.time(), str(floor)))
        return cur.lastrowid


def close_position(position_id: int, sell_price: Decimal):
    """
    Закрывает позицию и считает реальный PnL по той же экономике, что и прогноз.

    Вызывается вручную/интеграцией продажи: автоматической продажи в этой
    версии нет, поэтому метод существует, но бот его сам не дёргает.
    """
    with db_connect() as conn:
        row = conn.execute("SELECT buy_price_ton FROM positions WHERE id = ?",
                           (position_id,)).fetchone()
        if row is None:
            return None
        buy = Decimal(row["buy_price_ton"])
        proceeds = sell_price * (Decimal("1") - MARKETPLACE_FEE_PCT - ROYALTY_PCT)
        pnl = proceeds - buy - GAS_FEE_TON
        conn.execute(
            "UPDATE positions SET status='closed', sell_price_ton=?, sell_ts=?, "
            "pnl_ton=? WHERE id = ?",
            (str(sell_price), time.time(), str(pnl), position_id))
        return pnl


def spend_since(seconds: float) -> Decimal:
    """Сколько TON потрачено за последние `seconds` секунд."""
    cutoff = time.time() - seconds
    with db_connect() as conn:
        rows = conn.execute(
            "SELECT buy_price_ton FROM positions WHERE buy_ts >= ?", (cutoff,)).fetchall()
    return sum((Decimal(r["buy_price_ton"]) for r in rows), Decimal("0"))


def open_positions_count() -> int:
    """Сколько лотов сейчас на руках (куплено, но не продано)."""
    with db_connect() as conn:
        return conn.execute(
            "SELECT COUNT(*) AS c FROM positions WHERE status='open'").fetchone()["c"]


def consecutive_losses() -> int:
    """
    Сколько убыточных сделок подряд закрыто последними.

    Это детектор "стратегия перестала работать": три убытка подряд означают,
    что предпосылки модели больше не выполняются, и продолжать — значит
    методично терять деньги.
    """
    with db_connect() as conn:
        rows = conn.execute(
            "SELECT pnl_ton FROM positions WHERE status='closed' AND pnl_ton IS NOT NULL "
            "ORDER BY sell_ts DESC LIMIT ?", (STOP_AFTER_LOSSES,)).fetchall()
    streak = 0
    for r in rows:
        if Decimal(r["pnl_ton"]) < 0:
            streak += 1
        else:
            break
    return streak


def deployed_capital() -> Decimal:
    """Сколько денег банка сейчас лежит в открытых позициях."""
    with db_connect() as conn:
        rows = conn.execute(
            "SELECT buy_price_ton FROM positions WHERE status='open'").fetchall()
    return sum((Decimal(r["buy_price_ton"]) for r in rows), Decimal("0"))


def available_bankroll() -> Decimal:
    """
    Свободные средства банка: банк минус то, что уже в позициях, минус резерв.

    Резерв не трогаем никогда: если потратить всё до копейки, не останется
    на газ, и вы не сможете даже продать купленное.
    """
    if BANKROLL_TON <= 0:
        return Decimal("0")
    return BANKROLL_TON - deployed_capital() - RESERVE_TON


def max_position_size() -> Decimal:
    """
    Потолок одной сделки: минимум из абсолютного лимита и доли банка.

    Доля банка важнее абсолютного числа: она масштабируется вместе с
    депозитом и не даёт одной сделке унести весь банк.
    """
    by_pct = BANKROLL_TON * MAX_POSITION_PCT / Decimal("100")
    if BANKROLL_TON <= 0:
        return MAX_SPEND_PER_TRADE_TON
    return min(MAX_SPEND_PER_TRADE_TON, by_pct)


def load_wallet_key():
    """
    Читает приватный ключ из файла, проверяя права доступа.

    Возвращает (key | None, error | None). Ключ НИКОГДА не логируется,
    не пишется в БД и не уходит в уведомления.

    Права строже 0600 — обязательны: ключ, читаемый группой или всеми,
    считается скомпрометированным.
    """
    if not WALLET_KEY_FILE:
        return None, "WALLET_KEY_FILE не задан"
    if not os.path.isfile(WALLET_KEY_FILE):
        return None, f"файл ключа не найден: {WALLET_KEY_FILE}"

    mode = os.stat(WALLET_KEY_FILE).st_mode
    if mode & 0o077:
        return None, (f"НЕБЕЗОПАСНЫЕ ПРАВА на {WALLET_KEY_FILE} "
                      f"({oct(mode & 0o777)}). Выполните: chmod 600 {WALLET_KEY_FILE}")

    try:
        with open(WALLET_KEY_FILE, encoding="utf-8") as f:
            key = f.read().strip()
    except OSError as e:
        return None, f"не удалось прочитать файл ключа: {e}"

    if not key:
        return None, "файл ключа пуст"
    return key, None


def check_risk_limits(buy_price: Decimal):
    """
    Последний контур защиты ПЕРЕД тратой денег.

    Возвращает (ok: bool, reason: str). Проверяется в порядке "от самого
    дешёвого к самому дорогому запросу".
    """
    cap = max_position_size()
    if buy_price > cap:
        return False, (f"цена {buy_price} TON выше потолка сделки {cap} TON "
                       f"({MAX_POSITION_PCT}% банка / абсолютный лимит)")

    # Банк — жёсткая граница: за её пределами денег просто нет.
    if BANKROLL_TON > 0:
        free = available_bankroll()
        if buy_price > free:
            return False, (f"свободно {free} TON (банк {BANKROLL_TON} − в позициях "
                           f"{deployed_capital()} − резерв {RESERVE_TON}), "
                           f"нужно {buy_price} TON")

    losses = consecutive_losses()
    if losses >= STOP_AFTER_LOSSES:
        return False, (f"{losses} убыточных сделок подряд (порог {STOP_AFTER_LOSSES}) "
                       f"— торговля остановлена")

    open_count = open_positions_count()
    if open_count >= MAX_OPEN_POSITIONS:
        return False, f"открыто {open_count} позиций (лимит {MAX_OPEN_POSITIONS})"

    hour_spend = spend_since(3600)
    if hour_spend + buy_price > MAX_SPEND_PER_HOUR_TON:
        return False, (f"часовой лимит: потрачено {hour_spend} + {buy_price} > "
                       f"{MAX_SPEND_PER_HOUR_TON} TON")

    day_spend = spend_since(86400)
    if day_spend + buy_price > MAX_SPEND_PER_DAY_TON:
        return False, (f"суточный лимит: потрачено {day_spend} + {buy_price} > "
                       f"{MAX_SPEND_PER_DAY_TON} TON")

    return True, "лимиты в норме"


def notify(text: str):
    """
    Шлёт уведомление в Telegram. Полностью необязательно: без токена — тихо
    ничего не делает. Никогда не роняет торговый цикл и не логирует токен.
    """
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": text},
            timeout=HTTP_TIMEOUT_SEC)
    except Exception as e:  # noqa: BLE001 — уведомление не стоит торговли
        log.warning(f"Не удалось отправить уведомление в Telegram: {type(e).__name__}")


# =============================================================================
# 7. ИИ-МОЗГ — АНАЛИЗ РЫНКА ЧЕРЕЗ CLAUDE API
# =============================================================================

# Системный промпт с ЖЁСТКИМИ правилами. ИИ обязан вернуть строгий JSON.
AI_SYSTEM_PROMPT = f"""\
Ты — беспощадный риск-аналитик рынка NFT-подарков Telegram (сеть TON).
Твоя единственная цель — не дать боту переплатить и купить только то, что
даёт реальную чистую прибыль при перепродаже по Floor Price.

ПРАВИЛА РАСЧЁТА (соблюдай буквально):

1) ЦЕНА РЕАЛЬНОЙ ПРОДАЖИ:
   SALE = Floor_Price * (1 - {UNDERCUT_PCT})
   Продать РОВНО по floor нельзя — чтобы уйти первым, надо встать ниже.

2) ЧИСТАЯ ПРИБЫЛЬ (в TON):
   PROCEEDS   = SALE - SALE*{MARKETPLACE_FEE_PCT} - SALE*{ROYALTY_PCT}
   NET_PROFIT = PROCEEDS - Buy_Price - {GAS_FEE_TON}
   где {MARKETPLACE_FEE_PCT} — комиссия площадки, {ROYALTY_PCT} — роялти
   создателю коллекции (ОБЕ берутся с цены ПРОДАЖИ, не с цены покупки),
   {GAS_FEE_TON} TON — газ за круг покупка+продажа.

3) ROI в процентах:
   ROI_PERCENT = NET_PROFIT / Buy_Price * 100

4) ОТСЕЧЕНИЕ ХАЙПА / ПЕРЕПЛАТЫ (критично!):
   Наценка над Floor_Price оправдана ТОЛЬКО двумя причинами:
     а) is_top_100_or_pretty = true  (красивый или топ-100 номер минта);
     б) is_rare = true               (редкие трейты: модель/фон/символ).
   Оба поля вычислены детерминированно — доверяй им, не пересчитывай сам.
   Если ОБА false, то ЛЮБАЯ цена покупки ВЫШЕ Floor_Price — это ПЕРЕПЛАТА
   за хайп, и ты немедленно выдаёшь ACTION = "SKIP", даже если формально
   прибыль кажется возможной.

   ВАЖНО про rarity_pct: это доля носителей самого редкого трейта в процентах
   (меньше = реже). Значение null означает "редкость оценить не удалось" —
   это НЕ синоним "редкий". При null наценку над floor считай переплатой.

5) ДОСТОВЕРНОСТЬ FLOOR:
   Если floor_is_reliable = false, выборка рынка слишком мала и floor
   недостоверен. В этом случае ВСЕГДА возвращай ACTION = "SKIP".

6) ЛИКВИДНОСТЬ:
   competition — сколько продавцов уже стоит вплотную к floor. Если
   competition > max_competition, ваш лот встанет в конец очереди и флип
   не закроется: бумажная прибыль не равна реальной. Возвращай "SKIP".
   Если recent_sales = 0 (и оно не null), рынок мёртв — тоже "SKIP".

7) РЕШЕНИЕ:
   ACTION = "BUY"  только если ОДНОВРЕМЕННО:
       - NET_PROFIT > 0,
       - ROI_PERCENT >= {MIN_ROI_PCT},
       - нет переплаты по правилу (4),
       - floor_is_reliable = true,
       - ликвидность проходит по правилу (6).
   Иначе ACTION = "SKIP". В спорных случаях всегда выбирай SKIP:
   пропущенная сделка стоит ноль, ошибочная покупка стоит денег.

ФОРМАТ ОТВЕТА — СТРОГО ОДИН JSON-ОБЪЕКТ, без markdown, без пояснений вокруг:
{{
  "ACTION": "BUY" | "SKIP",
  "ROI_PERCENT": <число>,
  "NET_PROFIT_TON": <число>,
  "REASON": "<краткое объяснение на русском>"
}}
"""


def ai_analyze(client: Anthropic, item: dict, floor_price: Decimal,
               floor_reliable: bool, rarity_pct, rarest_trait,
               competition: int, recent_sales) -> dict:
    """
    Отправляет один лот + Floor Price в Claude API и возвращает распарсенный
    вердикт (dict с ключами ACTION, ROI_PERCENT, NET_PROFIT_TON, REASON).

    При любой ошибке возвращает безопасный SKIP — бот никогда не покупает
    "вслепую" из-за сбоя ИИ.
    """
    # Пакет данных для ИИ (тот самый JSON-пакет из ТЗ).
    payload = {
        "address": item["address"],
        "collection_name": item["collection_name"],
        "mint_index": item["mint_index"],
        "buy_price_ton": item["sale_price_ton"],
        "floor_price_ton": float(floor_price),
        "target_sale_price_ton": float(target_sale_price(floor_price)),
        "marketplace_fee_pct": float(MARKETPLACE_FEE_PCT),
        "royalty_pct": float(ROYALTY_PCT),
        "undercut_pct": float(UNDERCUT_PCT),
        "gas_fee_ton": float(GAS_FEE_TON),
        "min_roi_pct": float(MIN_ROI_PCT),
        "is_top_100_or_pretty": is_pretty_mint(item["mint_index"]),
        "floor_is_reliable": floor_reliable,
        # --- Редкость (Ярус 2) ---
        "traits": item.get("traits", {}),
        "rarity_pct": float(rarity_pct) if rarity_pct is not None else None,
        "rarest_trait": rarest_trait,
        "is_rare": is_rare(rarity_pct),
        "rare_threshold_pct": float(RARE_TRAIT_THRESHOLD_PCT),
        # --- Ликвидность (Ярус 2) ---
        "competition": competition,
        "max_competition": MAX_COMPETITION,
        "recent_sales": recent_sales,
        "liquidity_window_hours": LIQUIDITY_WINDOW_HOURS,
    }

    user_message = (
        "Проанализируй этот лот и верни строго JSON согласно правилам.\n\n"
        + json.dumps(payload, ensure_ascii=False, indent=2)
    )

    try:
        # Небольшой max_tokens — ответ короткий (один JSON). Используем
        # structured outputs, чтобы гарантированно получить валидный JSON.
        resp = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=512,
            system=AI_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_message}],
            output_config={
                "format": {
                    "type": "json_schema",
                    "schema": {
                        "type": "object",
                        "properties": {
                            "ACTION": {"type": "string", "enum": ["BUY", "SKIP"]},
                            "ROI_PERCENT": {"type": "number"},
                            "NET_PROFIT_TON": {"type": "number"},
                            "REASON": {"type": "string"},
                        },
                        "required": ["ACTION", "ROI_PERCENT", "NET_PROFIT_TON", "REASON"],
                        "additionalProperties": False,
                    },
                }
            },
        )
        raw = _extract_text(resp)
        return _parse_ai_json(raw)

    except RateLimitError:
        log.warning("ИИ: превышен лимит запросов (429). Вердикт по умолчанию: SKIP.")
    except APIStatusError as e:
        # Модель/параметр не поддержаны (например, старый ID) — покажем причину.
        log.error(f"ИИ: ошибка API {getattr(e, 'status_code', '?')}: {e}. Вердикт: SKIP.")
    except APIConnectionError as e:
        log.warning(f"ИИ: сетевая ошибка соединения ({e}). Вердикт: SKIP.")
    except Exception as e:  # noqa: BLE001 — не даём боту упасть из-за ИИ
        log.error(f"ИИ: непредвиденная ошибка ({e}). Вердикт: SKIP.")

    return {"ACTION": "SKIP", "ROI_PERCENT": 0, "NET_PROFIT_TON": 0,
            "REASON": "Сбой ИИ — безопасный отказ от покупки."}


def _extract_text(resp) -> str:
    """Собирает текст из блоков ответа Claude (устойчиво к нескольким блокам)."""
    parts = []
    for block in resp.content:
        if getattr(block, "type", None) == "text":
            parts.append(block.text)
    return "".join(parts).strip()


def _parse_ai_json(raw: str) -> dict:
    """
    Парсит JSON-ответ ИИ. Если модель вернула лишний текст вокруг — вырезаем
    первый {...} блок. Никогда не доверяем raw-строке напрямую.
    """
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        start, end = raw.find("{"), raw.rfind("}")
        if start != -1 and end != -1 and end > start:
            try:
                return json.loads(raw[start:end + 1])
            except json.JSONDecodeError:
                pass
    log.error(f"ИИ вернул невалидный JSON: {raw[:200]!r}")
    return {"ACTION": "SKIP", "ROI_PERCENT": 0, "NET_PROFIT_TON": 0,
            "REASON": "Невалидный ответ ИИ."}


# =============================================================================
# 8. БЛОКЧЕЙН — ЗАГЛУШКА ПОКУПКИ (реальная подпись НЕ выполняется)
# =============================================================================

def execute_blockchain_buy(item_id: str, price) -> bool:
    """
    Покупка лота. Возвращает True только если сделка ДЕЙСТВИТЕЛЬНО совершена.

    Два режима:
      DRY_RUN=1 — симуляция: зелёный лог, никаких транзакций, средства целы.
      DRY_RUN=0 — реальная подпись... которая пока НЕ РЕАЛИЗОВАНА.

    Почему здесь нет "почти рабочей" реализации: подписать транзакцию TON
    можно только через внешнюю библиотеку, API которой я не смог проверить
    в среде разработки. Код траты денег, написанный по памяти и ни разу не
    исполнявшийся, — это не автоматизация, а способ потерять банк.

    Важно: функция НИКОГДА не возвращает True, не совершив сделку. Иначе бот
    записал бы в БД позицию, которой нет, и весь учёт PnL стал бы фикцией.
    """
    if DRY_RUN:
        log.info(f"{_Color.GREEN}[SUCCESS] (СИМУЛЯЦИЯ) Покупка лота "
                 f"{item_id} за {price} TON{_Color.RESET}")
        return True

    if not REAL_EXECUTOR_AVAILABLE:
        # До сюда дойти нельзя: preflight_checks() не пускает в живой режим.
        # Проверка продублирована намеренно — на случай, если кто-то вызовет
        # функцию в обход main().
        log.error(f"{_Color.RED}Реальная покупка не реализована. Сделка НЕ совершена. "
                  f"См. SETUP.md.{_Color.RESET}")
        return False

    raise NotImplementedError("подключите исполнителя сделок — см. SETUP.md")


# =============================================================================
# 9. ОБРАБОТКА ОДНОГО ЛОТА (сбор -> ИИ -> действие)
# =============================================================================

def evaluate_trade(item: dict, snapshot: dict, recent_sales) -> dict:
    """
    ДЕТЕРМИНИРОВАННОЕ решение по лоту: ни ИИ, ни сети, ни побочных эффектов.

    Это единственный источник истины о том, стоит ли покупать. Бэктест гоняет
    ЭТУ ЖЕ функцию — иначе он проверял бы код, который в бою не исполняется,
    и его результаты ничего не значили бы.

    Возвращает словарь с полем allowed и причиной отказа.
    """
    floor = snapshot["floor"]
    buy_price = Decimal(str(item["sale_price_ton"]))
    pretty = is_pretty_mint(item.get("mint_index"))
    rarity_pct, rarest_trait = compute_rarity_pct(
        item, snapshot.get("trait_index", {}), snapshot.get("trait_total", 0))
    rare = is_rare(rarity_pct)

    # Красивый номер и редкость влияют на решение ТОЛЬКО через оценку
    # (PREMIUM_MULT), а не как отдельные ворота. Отдельной проверки
    # "переплата" здесь нет намеренно: расчёт прибыли уже её содержит —
    # покупка выше floor без премии всегда убыточна и отсекается ниже.
    premium = pretty or rare
    profit = compute_net_profit(floor, buy_price, premium)
    roi = compute_roi_pct(profit, buy_price)

    verdict = {
        "allowed": False, "reason": "", "net_profit": profit, "roi_pct": roi,
        "buy_price": buy_price, "rarity_pct": rarity_pct,
        "rarest_trait": rarest_trait, "is_rare": rare, "is_pretty": pretty,
        "premium_applied": premium and PREMIUM_MULT != Decimal("1"),
    }

    # Порядок проверок — от самой фундаментальной к частной.
    if not snapshot.get("floor_reliable"):
        verdict["reason"] = "floor недостоверен (мала выборка рынка)"
    elif profit <= 0:
        verdict["reason"] = f"убыточно: профит {profit:.4f} TON"
    elif roi < MIN_ROI_PCT:
        verdict["reason"] = f"ROI {roi}% ниже порога {MIN_ROI_PCT}%"
    elif snapshot.get("competition", 0) > MAX_COMPETITION:
        verdict["reason"] = (f"у floor {snapshot['competition']} продавцов "
                             f"(порог {MAX_COMPETITION}) — флип не закроется")
    elif recent_sales is not None and recent_sales < MIN_SALES_IN_WINDOW:
        verdict["reason"] = (f"за {LIQUIDITY_WINDOW_HOURS}ч продаж {recent_sales} "
                             f"(нужно >= {MIN_SALES_IN_WINDOW}) — рынок неликвиден")
    else:
        verdict["allowed"] = True
        verdict["reason"] = f"профит {profit:.4f} TON, ROI {roi}%"

    return verdict


def process_item(client: Anthropic, item: dict, snapshot: dict, recent_sales):
    """Прогоняет один лот через фильтры, ИИ и риск-лимиты; при согласии — покупает."""
    floor_price = snapshot["floor"]
    buy_price = Decimal(str(item["sale_price_ton"]))

    # --- ЗАЩИТА ОТ СКАМА: адрес коллекции важнее любого названия -----------
    if not is_collection_trusted(item.get("collection_address", "")):
        log.warning(
            f"{_Color.RED}ОТКЛОНЕНО: лот {_short(item['address'])} "
            f"из коллекции вне whitelist ({_short(item.get('collection_address', ''))}). "
            f"Возможен скам-клон.{_Color.RESET}"
        )
        return

    # --- ДЕДУПЛИКАЦИЯ: не переспрашиваем про тот же лот по той же цене ------
    if already_analyzed(item, floor_price):
        log.debug(f"Пропуск (уже анализировали): {_short(item['address'])} @ {buy_price} TON")
        return

    # --- ДЕТЕРМИНИРОВАННЫЙ ФИЛЬТР ПЕРЕД ИИ ---------------------------------
    # Считается ДО Claude сознательно: ИИ всё равно не может отменить этот
    # отказ, поэтому спрашивать его про заведомо отбракованный лот — значит
    # платить за ответ, который ни на что не влияет.
    ev = evaluate_trade(item, snapshot, recent_sales)

    rarity_note = (f"редкость {ev['rarity_pct']}% по '{ev['rarest_trait']}'"
                   if ev["rarity_pct"] is not None else "редкость н/д")
    log.info(
        f"Лот {_short(item['address'])} | mint #{item.get('mint_index')} "
        f"{'★красивый' if ev['is_pretty'] else 'обычный'} | "
        f"{'♦РЕДКИЙ' if ev['is_rare'] else rarity_note} | "
        f"цена {buy_price} TON | floor {floor_price} TON | ROI {ev['roi_pct']}%"
    )

    if not ev["allowed"]:
        log.info(f"{_Color.YELLOW}SKIP (фильтр){_Color.RESET} | {ev['reason']}")
        return

    # --- ИИ как ВТОРОЕ мнение по прошедшим фильтр лотам --------------------
    verdict = ai_analyze(client, item, floor_price, snapshot["floor_reliable"],
                         ev["rarity_pct"], ev["rarest_trait"],
                         snapshot["competition"], recent_sales)
    action = str(verdict.get("ACTION", "SKIP")).upper()
    reason = verdict.get("REASON", "")

    if action != "BUY":
        log.info(f"{_Color.YELLOW}ВЕРДИКТ ИИ: SKIP{_Color.RESET} | {reason}")
        return

    log.info(f"{_Color.GREEN}{_Color.BOLD}ВЕРДИКТ ИИ: BUY{_Color.RESET} "
             f"| ROI {verdict.get('ROI_PERCENT', 0)}% | {reason}")

    # --- РИСК-ЛИМИТЫ: последний контур перед тратой денег ------------------
    ok, limit_reason = check_risk_limits(buy_price)
    if not ok:
        log.warning(f"{_Color.RED}ПОКУПКА ЗАБЛОКИРОВАНА РИСК-ЛИМИТОМ: "
                    f"{limit_reason}{_Color.RESET}")
        notify(f"⛔️ Покупка заблокирована лимитом\n{_short(item['address'])}\n{limit_reason}")
        return

    if execute_blockchain_buy(item["address"], buy_price):
        pos_id = record_purchase(item, buy_price, floor_price)
        log.info(f"Позиция #{pos_id} записана в {DB_PATH}")
        notify(f"✅ Куплено #{pos_id}\n{_short(item['address'])}\n"
               f"цена {buy_price} TON | floor {floor_price} TON\n"
               f"ожидаемый профит {ev['net_profit']:.4f} TON (ROI {ev['roi_pct']}%)")


def _short(addr: str) -> str:
    """Укорачивает длинный адрес для читаемого лога."""
    return f"{addr[:6]}...{addr[-4:]}" if addr and len(addr) > 12 else (addr or "?")


# =============================================================================
# 10. ЗАПИСЬ РЫНКА И БЭКТЕСТ
# =============================================================================
# Исторического API продаж у нас нет, поэтому бэктест честно гоняется по
# СОБСТВЕННОЙ записи рынка:
#     1) python gift_sniper.py --record      (несколько дней копим снапшоты)
#     2) python gift_sniper.py --backtest    (прогоняем решения по записи)
# Выдумывать источник истории было бы хуже, чем не иметь бэктеста вовсе.

def record_snapshot(snap: dict, path: str = None):
    """Дописывает снимок рынка в JSONL. Одна строка — один цикл."""
    path = path or RECORD_PATH
    row = {
        "ts": time.time(),
        "collection": snap.get("collection", ""),
        "floor": str(snap["floor"]),
        "sample_size": snap["sample_size"],
        "competition": snap["competition"],
        "floor_reliable": snap["floor_reliable"],
        "trait_index": snap["trait_index"],
        "trait_total": snap["trait_total"],
        "candidates": snap["candidates"],
    }
    try:
        with io_open_append(path) as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError as e:
        log.error(f"Не удалось записать снапшот в {path}: {e}")


def io_open_append(path):
    """Отдельная обёртка — чтобы тесты могли подменить путь записи."""
    return open(path, "a", encoding="utf-8")


def _load_recording(path: str):
    """
    Читает JSONL-запись рынка и восстанавливает Decimal.

    Битые строки пропускаются с предупреждением, а не роняют весь прогон:
    запись могла прерваться на середине строки при остановке бота.
    """
    snaps = []
    with open(path, encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
                row["floor"] = Decimal(row["floor"])
            except (json.JSONDecodeError, KeyError, InvalidOperation):
                log.warning(f"{path}:{lineno} — строка повреждена, пропускаю")
                continue
            snaps.append(row)
    snaps.sort(key=lambda r: r["ts"])
    return snaps


def _future_floor(snaps, after_ts: float):
    """
    Floor в первом снапшоте, снятом не раньше after_ts.

    Возвращает None, если запись закончилась раньше — такая позиция
    считается НЕЗАКРЫТОЙ и в winrate не попадает. Домысливать за неё
    исход означало бы подогнать результат.
    """
    for snap in snaps:
        if snap["ts"] >= after_ts:
            return snap["floor"]
    return None


def run_backtest(path: str = None, hold_hours: int = None):
    """
    Прогоняет записанную историю через боевую функцию решения.

    Модель выхода: покупаем по цене лота, через hold_hours продаём по
    floor того момента с обычным undercut'ом и теми же комиссиями.
    """
    path = path or RECORD_PATH
    hold_hours = hold_hours if hold_hours is not None else BACKTEST_HOLD_HOURS

    try:
        snaps = _load_recording(path)
    except FileNotFoundError:
        log.error(f"Записи рынка нет: {path}. Сначала соберите её: "
                  f"python gift_sniper.py --record")
        return None

    if not snaps:
        log.error(f"{path} пуст — нечего прогонять.")
        return None

    span_h = (snaps[-1]["ts"] - snaps[0]["ts"]) / 3600
    log.info(f"{_Color.BOLD}=== БЭКТЕСТ ==={_Color.RESET}")
    log.info(f"Снапшотов: {len(snaps)} | Период: {span_h:.1f}ч | "
             f"Удержание: {hold_hours}ч")
    if span_h < hold_hours * 2:
        log.warning(f"{_Color.YELLOW}Запись короче двух периодов удержания — "
                    f"результат статистически неубедителен.{_Color.RESET}")

    # Группируем по коллекциям: floor одной коллекции ничего не говорит о
    # другой, и смешивать их в одном прогоне — считать мусор.
    by_coll = {}
    for snap in snaps:
        by_coll.setdefault(snap.get("collection", ""), []).append(snap)
    if len(by_coll) > 1:
        log.info(f"В записи {len(by_coll)} коллекций — считаю каждую отдельно.")

    trades, bought_addrs = [], set()
    for coll, coll_snaps in by_coll.items():
        trades.extend(_backtest_one(coll_snaps, hold_hours, bought_addrs))

    return _report_backtest(trades, hold_hours)


def _backtest_one(snaps, hold_hours: int, bought_addrs: set):
    """Прогон по снапшотам ОДНОЙ коллекции."""
    trades = []
    for snap in snaps:
        for item in snap["candidates"]:
            # Один и тот же лот не покупаем дважды за прогон.
            if item["address"] in bought_addrs:
                continue
            # recent_sales=None: в записи истории продаж нет, и подставлять
            # вместо неё число значило бы тестировать несуществующие данные.
            ev = evaluate_trade(item, snap, None)
            if not ev["allowed"]:
                continue
            if ev["buy_price"] > MAX_SPEND_PER_TRADE_TON:
                continue

            future = _future_floor(snaps, snap["ts"] + hold_hours * 3600)
            bought_addrs.add(item["address"])
            trade = {"address": item["address"], "buy": ev["buy_price"],
                     "expected": ev["net_profit"], "roi": ev["roi_pct"]}
            if future is None:
                trade["status"] = "open"        # запись кончилась раньше выхода
            else:
                sale = target_sale_price(future, ev["is_rare"] or ev["is_pretty"])
                proceeds = sale * (Decimal("1") - MARKETPLACE_FEE_PCT - ROYALTY_PCT)
                trade["status"] = "closed"
                trade["pnl"] = proceeds - ev["buy_price"] - GAS_FEE_TON
            trades.append(trade)
    return trades


def probe(address: str):
    """
    Диагностика: что РЕАЛЬНО отдаёт API по этому адресу и что из этого
    удалось разобрать.

    Две задачи сразу:
      1) если адрес — кошелёк, показать коллекции ваших подарков (их адреса
         можно сразу вставить в TARGET_COLLECTIONS);
      2) если адрес — коллекция, показать, что парсер извлёк из ответа,
         и ЧЕСТНО напечатать сырой JSON там, где не извлёк ничего.

    Пункт 2 существует потому, что форма ответа TonAPI в этом проекте ни разу
    не проверялась на живых данных. Сырой вывод — это то, по чему парсер
    можно починить, не гадая.
    """
    norm = normalize_ton_address(address)
    if norm is None:
        log.error(f"{address!r} не распознан как адрес TON.")
        return False

    headers = {"Accept": "application/json"}
    if TONAPI_KEY:
        headers["Authorization"] = f"Bearer {TONAPI_KEY}"

    log.info(f"{_Color.BOLD}=== ПРОВЕРКА {_short(address)} ==={_Color.RESET}")
    log.info(f"Нормализованный вид: {norm}")

    # --- Попытка 1: это кошелёк? Тогда покажем коллекции его подарков -------
    acc_url = f"https://tonapi.io/v2/accounts/{address}/nfts"
    try:
        r = requests.get(acc_url, params={"limit": 50}, headers=headers,
                         timeout=HTTP_TIMEOUT_SEC)
        if r.status_code == 200:
            items = r.json().get("nft_items", [])
            colls = {}
            for it in items:
                c = it.get("collection") or {}
                if c.get("address"):
                    colls.setdefault(c["address"], c.get("name", "?"))
            if colls:
                log.info(f"{_Color.GREEN}Это кошелёк. Коллекций среди его NFT: "
                         f"{len(colls)}{_Color.RESET}")
                for addr, name in colls.items():
                    log.info(f"  {name}")
                    log.info(f"    {addr}")
                log.info("")
                log.info("Готовая строка для запуска:")
                log.info(f"  export TARGET_COLLECTIONS=\"{','.join(colls)}\"")
                return True
    except Exception as e:  # noqa: BLE001 — это лишь одна из гипотез
        log.debug(f"Не кошелёк или ошибка: {e}")

    # --- Попытка 2: это коллекция? Проверяем парсер на живых данных --------
    url = f"https://tonapi.io/v2/nfts/collections/{address}/items"
    try:
        r = requests.get(url, params={"limit": 5}, headers=headers,
                         timeout=HTTP_TIMEOUT_SEC)
    except Exception as e:  # noqa: BLE001
        log.error(f"Сеть недоступна: {e}")
        return False

    log.info(f"HTTP {r.status_code} от {url}")
    if r.status_code != 200:
        log.error(f"Ответ: {r.text[:400]}")
        if r.status_code == 429:
            log.error("Это лимит запросов — получите TONAPI_KEY на tonconsole.com")
        return False

    raw_items = r.json().get("nft_items", [])
    if not raw_items:
        log.warning("Ответ пустой: по этому адресу предметов нет. "
                    "Скорее всего адрес не коллекции.")
        return False

    parsed = fetch_items_tonapi(address, limit=5)
    log.info(f"Предметов в ответе: {len(raw_items)} | разобрано: {len(parsed)}")

    # Проверяем КАЖДОЕ поле отдельно: молчаливо не разобранное поле — это
    # именно то, что потом ломает расчёты.
    on_sale = [i for i in parsed if i["is_on_sale"]]
    checks = [
        ("цена продажи", sum(1 for i in parsed if i["sale_price_ton"] > 0)),
        ("лоты на продаже", len(on_sale)),
        ("номер минта", sum(1 for i in parsed if i["mint_index"] is not None)),
        ("трейты", sum(1 for i in parsed if i.get("traits"))),
        ("адрес коллекции", sum(1 for i in parsed if i["collection_address"])),
    ]
    log.info("")
    for name, got in checks:
        mark = f"{_Color.GREEN}OK{_Color.RESET}" if got else f"{_Color.RED}НЕ РАЗОБРАНО{_Color.RESET}"
        log.info(f"  {name:<20} {got}/{len(parsed)}  {mark}")

    failed = [n for n, g in checks if not g]
    if failed:
        log.info("")
        log.warning(f"{_Color.YELLOW}Не разобрано: {', '.join(failed)}. "
                    f"Сырой JSON первого предмета ниже — по нему чинится парсер."
                    f"{_Color.RESET}")
        log.info(json.dumps(raw_items[0], ensure_ascii=False, indent=2)[:2500])
    else:
        log.info("")
        log.info(f"{_Color.GREEN}Парсер разобрал всё. Коллекцию можно "
                 f"добавлять в TARGET_COLLECTIONS.{_Color.RESET}")
    return not failed


def rank_collections(path: str = None):
    """
    Ранжирует коллекции из записи рынка по РЕАЛЬНОЙ активности.

    Зачем это нужно: смотреть 30 коллекций бессмысленно, если в 25 из них
    месяцами ничего не происходит. Но узнать это можно только наблюдением —
    "мёртвая" коллекция выглядит в API точно так же, как живая.

    Метрика активности — ОБОРОТ ВИТРИНЫ: сколько лотов исчезло из числа
    самых дешёвых между соседними снапшотами. Лот исчезает, когда его
    купили или сняли с продажи.

    ЧЕСТНОЕ ОГРАНИЧЕНИЕ: это измеряется по CANDIDATES_TO_ANALYZE самым
    дешёвым лотам, а не по всему объёму коллекции, и исчезновение лота —
    это продажа ИЛИ снятие, различить их без истории сделок нельзя.
    Поэтому метрика сравнительная: она говорит, где активнее, а не сколько
    именно продано.
    """
    path = path or RECORD_PATH
    try:
        snaps = _load_recording(path)
    except FileNotFoundError:
        log.error(f"Записи рынка нет: {path}. Сначала соберите её: "
                  f"python gift_sniper.py --record")
        return None
    if not snaps:
        log.error(f"{path} пуст — нечего анализировать.")
        return None

    by_coll = {}
    for snap in snaps:
        by_coll.setdefault(snap.get("collection", ""), []).append(snap)

    log.info(f"{_Color.BOLD}=== АКТИВНОСТЬ КОЛЛЕКЦИЙ ==={_Color.RESET}")
    log.info(f"Запись: {path} | снапшотов {len(snaps)} | коллекций {len(by_coll)}")

    stats = [_collection_stats(c, sn) for c, sn in by_coll.items()]
    stats.sort(key=lambda st: st["turnover_per_hour"], reverse=True)

    log.info("")
    log.info(f"{'Коллекция':<16} {'Оборот/ч':>9} {'Лотов':>7} {'Floor':>10} "
             f"{'Разброс':>9}  Статус")
    for st in stats:
        colour = (_Color.RED if st["status"] != "живая"
                  else _Color.GREEN if st["turnover_per_hour"] >= 1 else "")
        log.info(f"{_short(st['collection']):<16} "
                 f"{st['turnover_per_hour']:>9.2f} "
                 f"{st['avg_listings']:>7.0f} "
                 f"{st['median_floor']:>10.2f} "
                 f"{st['floor_spread_pct']:>8.1f}%  "
                 f"{colour}{st['status']}{_Color.RESET}")

    alive = [st for st in stats if st["status"] == "живая"]
    log.info("")
    if not alive:
        log.warning(f"{_Color.YELLOW}Ни одной живой коллекции. Либо запись слишком "
                    f"короткая, либо выбранные коллекции не торгуются.{_Color.RESET}")
    else:
        log.info(f"Живых коллекций: {len(alive)} из {len(stats)}. "
                 f"Рекомендую оставить в TARGET_COLLECTIONS верхние "
                 f"{min(3, len(alive))} — остальные только жгут лимиты API.")
    _warn_if_recording_short(snaps)
    return stats


def _collection_stats(collection: str, snaps: list) -> dict:
    """Считает метрики активности одной коллекции по её снапшотам."""
    snaps = sorted(snaps, key=lambda s: s["ts"])
    span_h = max((snaps[-1]["ts"] - snaps[0]["ts"]) / 3600, 0.0001)

    floors = [s["floor"] for s in snaps if s["floor"] > 0]
    sizes = [s["sample_size"] for s in snaps]

    # Оборот витрины: сколько дешёвых лотов исчезло между снапшотами.
    seen = [{i["address"] for i in s.get("candidates", [])} for s in snaps]
    gone = sum(len(a - b) for a, b in zip(seen, seen[1:]))

    median_floor = (sorted(floors)[len(floors) // 2] if floors else Decimal("0"))
    spread = Decimal("0")
    if floors and min(floors) > 0:
        spread = (max(floors) - min(floors)) / min(floors) * Decimal("100")

    # Диагностика "почему тихо" — это разные проблемы с разными решениями.
    if max(sizes, default=0) == 0:
        status = "офлайн (нет данных)"
    elif len(snaps) < 3:
        status = "мало снапшотов"
    elif gone == 0:
        status = "замерла (оборота нет)"
    else:
        status = "живая"

    return {
        "collection": collection or "(без адреса)",
        "snapshots": len(snaps),
        "span_hours": span_h,
        "turnover_per_hour": gone / span_h,
        "avg_listings": sum(sizes) / len(sizes) if sizes else 0,
        "median_floor": float(median_floor),
        "floor_spread_pct": float(spread),
        "status": status,
    }


def _warn_if_recording_short(snaps):
    """Короткая запись даёт красивые, но бессмысленные цифры."""
    span_h = (snaps[-1]["ts"] - snaps[0]["ts"]) / 3600
    if span_h < 24:
        log.warning(f"{_Color.YELLOW}Запись покрывает всего {span_h:.1f}ч. "
                    f"Рынок подарков неравномерен по времени суток — "
                    f"выводы по выборке меньше суток ненадёжны.{_Color.RESET}")


def _report_backtest(trades, hold_hours):
    """Печатает итоги бэктеста и возвращает их словарём."""
    closed = [t for t in trades if t["status"] == "closed"]
    unresolved = len(trades) - len(closed)
    wins = [t for t in closed if t["pnl"] > 0]

    total_pnl = sum((t["pnl"] for t in closed), Decimal("0"))
    invested = sum((t["buy"] for t in closed), Decimal("0"))
    expected = sum((t["expected"] for t in closed), Decimal("0"))

    log.info(f"Сделок отобрано: {len(trades)} | закрыто: {len(closed)} | "
             f"не закрыто (запись кончилась): {unresolved}")

    if not closed:
        log.warning(f"{_Color.YELLOW}Ни одна сделка не закрылась — выводов сделать "
                    f"нельзя. Нужна запись длиннее {hold_hours}ч.{_Color.RESET}")
        return {"trades": len(trades), "closed": 0, "unresolved": unresolved}

    winrate = Decimal(len(wins)) / Decimal(len(closed)) * Decimal("100")
    color = _Color.GREEN if total_pnl > 0 else _Color.RED
    log.info(f"Winrate: {winrate:.1f}% ({len(wins)}/{len(closed)})")
    log.info(f"{color}Итоговый PnL: {total_pnl:.4f} TON{_Color.RESET} "
             f"при вложениях {invested:.4f} TON")
    if invested > 0:
        log.info(f"Фактический ROI: {total_pnl / invested * 100:.2f}%")
    # Расхождение прогноза и факта — главный вывод бэктеста.
    log.info(f"Прогноз обещал {expected:.4f} TON, факт {total_pnl:.4f} TON "
             f"(расхождение {total_pnl - expected:.4f} TON)")

    best = max(closed, key=lambda t: t["pnl"])
    worst = min(closed, key=lambda t: t["pnl"])
    log.info(f"Лучшая: {best['pnl']:+.4f} TON | Худшая: {worst['pnl']:+.4f} TON")

    return {"trades": len(trades), "closed": len(closed), "unresolved": unresolved,
            "wins": len(wins), "winrate": winrate, "pnl": total_pnl,
            "invested": invested, "expected": expected}


# =============================================================================
# 11. ГЛАВНЫЙ ЦИКЛ МОНИТОРИНГА (MONITORING LOOP)
# =============================================================================

def preflight_checks(require_ai: bool = True):
    """Проверяет обязательные настройки перед запуском цикла."""
    problems = []
    if require_ai and not ANTHROPIC_AVAILABLE:
        problems.append("Нет модуля 'anthropic' (нужен для торговли): pip install anthropic")
    if require_ai and not ANTHROPIC_API_KEY:
        problems.append("ANTHROPIC_API_KEY не задан (нужен для ИИ-анализа).")
    if not TARGET_COLLECTIONS:
        problems.append("TARGET_COLLECTIONS не задан — впишите адреса коллекций через запятую.")
    for addr in TARGET_COLLECTIONS:
        if normalize_ton_address(addr) is None:
            problems.append(f"Коллекция нераспознана как адрес TON: {addr!r}")

    # Адреса в whitelist тоже должны быть валидны, иначе защита молча не работает.
    for addr in COLLECTION_WHITELIST:
        if normalize_ton_address(addr) is None:
            problems.append(f"COLLECTION_WHITELIST содержит нераспознанный адрес: {addr!r}")

    if problems:
        for p in problems:
            log.error(p)
        return False

    # Нагрузка растёт линейно по числу коллекций — предупреждаем ДО старта,
    # а не после того, как API начнёт отдавать 429.
    req_per_min = len(TARGET_COLLECTIONS) * FLOOR_SAMPLE_PAGES * (60 / max(POLL_INTERVAL_SEC, 1))
    if req_per_min > 100:
        log.warning(
            f"{_Color.YELLOW}~{req_per_min:.0f} запросов/мин к TonAPI "
            f"({len(TARGET_COLLECTIONS)} коллекций x {FLOOR_SAMPLE_PAGES} страниц "
            f"x {60/max(POLL_INTERVAL_SEC,1):.1f} циклов/мин). Вероятны 429. "
            f"Поднимите POLL_INTERVAL_SEC или снизьте FLOOR_SAMPLE_PAGES.{_Color.RESET}")

    if not COLLECTION_WHITELIST:
        log.warning(f"{_Color.YELLOW}COLLECTION_WHITELIST пуст — проверка на скам-коллекции "
                    f"ОТКЛЮЧЕНА. Укажите доверенные адреса перед реальной торговлей."
                    f"{_Color.RESET}")

    # --- ВОРОТА В ЖИВОЙ РЕЖИМ ----------------------------------------------
    # Каждое условие — отдельный способ потерять деньги. Ни одно не
    # проверяется "по возможности": все обязательны.
    if require_ai and not DRY_RUN:
        blockers = []
        if not REAL_EXECUTOR_AVAILABLE:
            blockers.append("реальная подпись транзакций не реализована (см. SETUP.md)")
        if CONFIRM_LIVE_TRADING != "I_UNDERSTAND_THE_RISK":
            blockers.append("не подтверждён риск: CONFIRM_LIVE_TRADING=I_UNDERSTAND_THE_RISK")
        if BANKROLL_TON <= 0:
            blockers.append("BANKROLL_TON не задан — бот не знает, чем ему разрешено рисковать")
        if BANKROLL_TON > 0 and RESERVE_TON >= BANKROLL_TON:
            blockers.append(f"RESERVE_TON ({RESERVE_TON}) >= BANKROLL_TON ({BANKROLL_TON})")
        if not COLLECTION_WHITELIST:
            blockers.append("COLLECTION_WHITELIST пуст — защита от скам-коллекций обязательна вживую")
        _, key_err = load_wallet_key()
        if key_err:
            blockers.append(f"кошелёк: {key_err}")
        if TRADING_NETWORK not in ("testnet", "mainnet"):
            blockers.append(f"TRADING_NETWORK должен быть testnet или mainnet, а не {TRADING_NETWORK!r}")

        if blockers:
            log.error(f"{_Color.RED}{_Color.BOLD}ЖИВАЯ ТОРГОВЛЯ ЗАБЛОКИРОВАНА:{_Color.RESET}")
            for b in blockers:
                log.error(f"  • {b}")
            log.error("Запустите с DRY_RUN=1 либо устраните причины выше.")
            return False

    return True


def main(record: bool = False, trade: bool = True):
    """
    Главный цикл.

    record — дописывать каждый снапшот рынка в RECORD_PATH (для бэктеста).
    trade  — выполнять торговую логику. В режиме --record по умолчанию
             выключено: сбор данных не должен зависеть от ключа Claude
             и не должен ничего покупать.
    """
    log.info(f"{_Color.BOLD}=== Telegram Gifts NFT Sniper запускается ==={_Color.RESET}")
    mode = "запись рынка" + (" + торговля" if trade else " (без торговли)") if record else "торговля"
    log.info(f"Режим: {mode} | Коллекций: {len(TARGET_COLLECTIONS)} | "
             f"Интервал: {POLL_INTERVAL_SEC}s | DRY_RUN: {DRY_RUN}")
    for c in TARGET_COLLECTIONS:
        log.info(f"  · {_short(c)}")
    if trade:
        log.info(f"Модель ИИ: {CLAUDE_MODEL} | БД: {DB_PATH}")
        if BANKROLL_TON > 0:
            log.info(f"Банк: {BANKROLL_TON} TON | резерв {RESERVE_TON} | "
                     f"в позициях {deployed_capital()} | свободно {available_bankroll()} | "
                     f"потолок сделки {max_position_size()} TON")
        log.info(f"Сеть: {TRADING_NETWORK} | режим: "
                 f"{'СИМУЛЯЦИЯ (средства целы)' if DRY_RUN else 'ЖИВАЯ ТОРГОВЛЯ'}")
        log.info(f"Лимиты: сделка <= {MAX_SPEND_PER_TRADE_TON} | час <= "
                 f"{MAX_SPEND_PER_HOUR_TON} | сутки <= {MAX_SPEND_PER_DAY_TON} TON | "
                 f"позиций <= {MAX_OPEN_POSITIONS} | стоп после {STOP_AFTER_LOSSES} убытков")

    if not preflight_checks(require_ai=trade):
        sys.exit("[FATAL] Исправьте настройки выше и перезапустите.")

    db_init()

    # Клиент Claude нужен только для торговли.
    client = Anthropic(api_key=ANTHROPIC_API_KEY) if trade else None

    cycle = 0
    while True:  # <-- бесконечный цикл реального мониторинга
        cycle += 1
        started = time.monotonic()
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        log.info(f"{_Color.BOLD}--- Проверка #{cycle} @ {now} ---{_Color.RESET}")

        # Каждую коллекцию обрабатываем независимо: floor, редкость и
        # ликвидность у них свои, и сбой одной не должен ронять остальные.
        for coll in TARGET_COLLECTIONS:
            try:
                _process_collection(client, coll, record, trade)
            except KeyboardInterrupt:
                raise
            except Exception as e:  # noqa: BLE001 — одна коллекция не роняет цикл
                log.error(f"Коллекция {_short(coll)}: ошибка ({e})")

        # Держим стабильный интервал (учитываем время работы итерации).
        elapsed = time.monotonic() - started
        sleep_for = max(0, POLL_INTERVAL_SEC - elapsed)
        log.info(f"{_Color.GREY}Итерация заняла {elapsed:.1f}s. Сплю {sleep_for:.1f}s...{_Color.RESET}")
        time.sleep(sleep_for)


def _process_collection(client, collection: str, record: bool, trade: bool):
    """
    Один полный проход по одной коллекции: снимок рынка, запись, торговля.

    Вынесено из main() ради многоколлекционного режима: у каждой коллекции
    свой floor и своя редкость, смешивать их нельзя.
    """
    try:
        snap = get_market_snapshot(collection)
        candidates = snap["candidates"]
        log.info(f"{_Color.BOLD}[{_short(collection)}]{_Color.RESET}")

        # Пишем снапшот ДО торговых решений: запись нужна и тогда,
        # когда торговать нельзя (иначе в истории будут дыры).
        if record and snap["sample_size"] > 0:
            record_snapshot(snap)

        if not trade:
            # Не рапортуем о записи, которой не было: при пустой выборке
            # record_snapshot() выше не вызывался.
            if snap["sample_size"] > 0:
                log.info(f"Записано: floor {snap['floor']} TON | "
                         f"выборка {snap['sample_size']} | кандидатов {len(candidates)}")
            else:
                log.warning("Данных с рынка нет — снапшот НЕ записан.")
        elif not candidates:
            log.warning("Активных лотов на продаже не найдено. Жду следующей проверки.")
        elif not snap["floor_reliable"]:
            log.warning(
                f"{_Color.YELLOW}Floor недостоверен: в выборке всего "
                f"{snap['sample_size']} лотов (нужно >= {MIN_FLOOR_SAMPLE}). "
                f"Торговля в этом цикле пропущена.{_Color.RESET}"
            )
        else:
            log.info(f"Источник: {snap['source']} | Выборка: {snap['sample_size']} лотов | "
                     f"Floor (P{FLOOR_PERCENTILE}): {snap['floor']} TON | "
                     f"Конкуренция у floor: {snap['competition']} | "
                     f"Кандидатов: {len(candidates)}")

            if snap["trait_total"] < MIN_TRAIT_SAMPLE:
                log.warning(f"{_Color.YELLOW}Редкость не оценивается: трейтов только "
                            f"у {snap['trait_total']} лотов (нужно >= {MIN_TRAIT_SAMPLE})."
                            f"{_Color.RESET}")

            # Ликвидность запрашивается ОДИН раз за цикл, а не на каждый лот.
            recent_sales = fetch_recent_sales_count(collection)
            if recent_sales is not None:
                log.info(f"Продаж за {LIQUIDITY_WINDOW_HOURS}ч: {recent_sales}")

            # Прогоняем самые дешёвые лоты через ИИ.
            for item in candidates:
                process_item(client, item, snap, recent_sales)

    except KeyboardInterrupt:
        raise


def parse_args(argv=None):
    """Разбор аргументов командной строки."""
    parser = argparse.ArgumentParser(
        description="Telegram Gifts NFT Sniper — снайпер и ИИ-аналитик рынка TON.",
        epilog="Порядок работы: сначала --record несколько дней, "
               "затем --backtest, и только потом торговля на реальные деньги.")
    parser.add_argument("--record", action="store_true",
                        help=f"писать снапшоты рынка в {RECORD_PATH} (для бэктеста)")
    parser.add_argument("--trade", action="store_true",
                        help="торговать одновременно с записью (по умолчанию --record не торгует)")
    parser.add_argument("--backtest", nargs="?", const=RECORD_PATH, metavar="FILE",
                        help="прогнать решения по записи рынка и выйти")
    parser.add_argument("--rank", nargs="?", const=RECORD_PATH, metavar="FILE",
                        help="показать, какие коллекции реально торгуются, и выйти")
    parser.add_argument("--probe", metavar="ADDRESS",
                        help="проверить адрес (кошелёк или коллекцию) и показать, "
                             "что парсер извлёк из живого ответа API")
    parser.add_argument("--hold-hours", type=int, default=None,
                        help=f"горизонт удержания в бэктесте (по умолчанию {BACKTEST_HOLD_HOURS})")
    return parser.parse_args(argv)


if __name__ == "__main__":
    args = parse_args()
    try:
        if args.probe:
            # Только чтение API: ни покупок, ни записи.
            sys.exit(0 if probe(args.probe) else 1)
        if args.rank:
            # Только чтение записи: ни сети, ни покупок.
            sys.exit(0 if rank_collections(args.rank) else 1)
        if args.backtest:
            # Бэктест ничего не покупает и не ходит в сеть — только считает.
            sys.exit(0 if run_backtest(args.backtest, args.hold_hours) else 1)
        # В режиме записи торговля включается только явным --trade.
        main(record=args.record, trade=args.trade or not args.record)
    except KeyboardInterrupt:
        log.info(f"{_Color.BOLD}Остановлено пользователем. До встречи!{_Color.RESET}")
        sys.exit(0)
