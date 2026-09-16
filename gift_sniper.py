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
import logging
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

# --- Сторонние зависимости -------------------------------------------------
# requests   -> HTTP-запросы к рыночным API
# anthropic  -> официальный SDK для Claude API (ИИ-мозг)
try:
    import requests
except ImportError:
    sys.exit("[FATAL] Нет модуля 'requests'. Установите:  pip install requests")

try:
    from anthropic import Anthropic
    # Типизированные исключения SDK — ловим их отдельно от сетевых ошибок.
    from anthropic import (
        APIStatusError,
        RateLimitError,
        APIConnectionError,
    )
except ImportError:
    sys.exit("[FATAL] Нет модуля 'anthropic'. Установите:  pip install anthropic")


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
TARGET_COLLECTION   = os.getenv("TARGET_COLLECTION", "EQAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA")

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
    return {"candidates": [], "floor": Decimal("0"), "sample_size": 0,
            "source": source, "trait_index": {}, "trait_total": 0,
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

def target_sale_price(floor_price: Decimal) -> Decimal:
    """
    Цена, по которой мы реально сможем продать.

    Продать ПО floor нельзя: чтобы уйти первым, надо встать ниже текущего
    минимума. Поэтому целевая цена = floor минус undercut.
    """
    return floor_price * (Decimal("1") - UNDERCUT_PCT)


def compute_net_profit(floor_price: Decimal, buy_price: Decimal) -> Decimal:
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
    sale = target_sale_price(floor_price)
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
# 6. ИИ-МОЗГ — АНАЛИЗ РЫНКА ЧЕРЕЗ CLAUDE API
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
# 7. БЛОКЧЕЙН — ЗАГЛУШКА ПОКУПКИ (реальная подпись НЕ выполняется)
# =============================================================================

def execute_blockchain_buy(item_id: str, price) -> bool:
    """
    ЗАГЛУШКА покупки. Реальная транзакция НЕ подписывается и НЕ отправляется,
    чтобы не рисковать средствами до полноценных тестов.

    Здесь, когда будете готовы, подключите реальную логику (например,
    tonutils / pytoniq): собрать сообщение покупки на смарт-контракт продажи
    и подписать его WALLET_PRIVATE_KEY. Пока — только зелёный лог успеха.
    """
    if not DRY_RUN:
        # Место для будущей реальной реализации.
        log.warning("DRY_RUN=0, но реальная покупка не реализована — работаю как заглушка.")

    # Симулируем "отправку" транзакции.
    log.info(f"{_Color.GREEN}[SUCCESS] Отправлена транзакция на покупку лота "
             f"{item_id} за {price} TON{_Color.RESET}")
    return True


# =============================================================================
# 8. ОБРАБОТКА ОДНОГО ЛОТА (сбор -> ИИ -> действие)
# =============================================================================

def process_item(client: Anthropic, item: dict, snapshot: dict, recent_sales):
    """Прогоняет один лот через ИИ и, при вердикте BUY, вызывает покупку."""
    floor_price = snapshot["floor"]
    floor_reliable = snapshot["floor_reliable"]
    competition = snapshot["competition"]

    buy_price = Decimal(str(item["sale_price_ton"]))
    mint = item["mint_index"]
    pretty = is_pretty_mint(mint)

    # --- ЗАЩИТА ОТ СКАМА: адрес коллекции важнее любого названия -----------
    # Делается ДО вызова ИИ: и дешевле, и ИИ нельзя доверить проверку,
    # которую можно выполнить детерминированно.
    if not is_collection_trusted(item.get("collection_address", "")):
        log.warning(
            f"{_Color.RED}ОТКЛОНЕНО: лот {_short(item['address'])} "
            f"из коллекции вне whitelist ({_short(item.get('collection_address', ''))}). "
            f"Возможен скам-клон.{_Color.RESET}"
        )
        return

    # --- ДЕДУПЛИКАЦИЯ: не переспрашиваем ИИ про тот же лот по той же цене ---
    # Ставится ДО вызова Claude, потому что смысл именно в экономии на API.
    if already_analyzed(item, floor_price):
        log.debug(f"Пропуск (уже анализировали): {_short(item['address'])} @ {buy_price} TON")
        return

    # --- РЕДКОСТЬ: для Telegram Gifts она важнее номера минта ---------------
    rarity_pct, rarest_trait = compute_rarity_pct(
        item, snapshot["trait_index"], snapshot["trait_total"])
    rare = is_rare(rarity_pct)

    # Локальный предрасчёт (для лога; финальное слово — за ИИ).
    local_profit = compute_net_profit(floor_price, buy_price)
    local_roi = compute_roi_pct(local_profit, buy_price)

    rarity_note = (f"редкость {rarity_pct}% по '{rarest_trait}'"
                   if rarity_pct is not None else "редкость н/д")
    log.info(
        f"Лот {_short(item['address'])} | mint #{mint} "
        f"{'★красивый' if pretty else 'обычный'} | "
        f"{'♦РЕДКИЙ' if rare else rarity_note} | "
        f"цена {buy_price} TON | floor {floor_price} TON | "
        f"локальный ROI {local_roi}%"
    )

    # Вердикт ИИ.
    verdict = ai_analyze(client, item, floor_price, floor_reliable,
                         rarity_pct, rarest_trait, competition, recent_sales)
    action = str(verdict.get("ACTION", "SKIP")).upper()
    roi = verdict.get("ROI_PERCENT", 0)
    net = verdict.get("NET_PROFIT_TON", 0)
    reason = verdict.get("REASON", "")

    if action == "BUY":
        log.info(f"{_Color.GREEN}{_Color.BOLD}ВЕРДИКТ ИИ: BUY{_Color.RESET} "
                 f"| ROI {roi}% | профит {net} TON | {reason}")

        # --- ПОСЛЕДНИЙ РУБЕЖ: детерминированная проверка поверх ИИ ----------
        # ИИ может ошибиться в арифметике или проигнорировать правило.
        # Деньги тратятся только если локальный расчёт ТОЖЕ согласен.
        if not floor_reliable:
            log.warning(f"{_Color.RED}ПОКУПКА ОТМЕНЕНА: floor недостоверен "
                        f"(мала выборка рынка).{_Color.RESET}")
            return
        if local_profit <= 0 or local_roi < MIN_ROI_PCT:
            log.warning(f"{_Color.RED}ПОКУПКА ОТМЕНЕНА: локальный расчёт не согласен "
                        f"с ИИ (профит {local_profit:.4f} TON, ROI {local_roi}% "
                        f"< порога {MIN_ROI_PCT}%).{_Color.RESET}")
            return
        # Переплата над floor допустима только за красивый номер ИЛИ редкость.
        if buy_price > floor_price and not (pretty or rare):
            log.warning(f"{_Color.RED}ПОКУПКА ОТМЕНЕНА: цена выше floor, "
                        f"но лот не красивый и не редкий — это переплата."
                        f"{_Color.RESET}")
            return
        if competition > MAX_COMPETITION:
            log.warning(f"{_Color.RED}ПОКУПКА ОТМЕНЕНА: у floor уже {competition} "
                        f"продавцов (порог {MAX_COMPETITION}) — флип не закроется."
                        f"{_Color.RESET}")
            return
        if recent_sales is not None and recent_sales < MIN_SALES_IN_WINDOW:
            log.warning(f"{_Color.RED}ПОКУПКА ОТМЕНЕНА: за {LIQUIDITY_WINDOW_HOURS}ч "
                        f"продаж {recent_sales} (нужно >= {MIN_SALES_IN_WINDOW}) — "
                        f"рынок неликвиден.{_Color.RESET}")
            return

        execute_blockchain_buy(item["address"], buy_price)
    else:
        log.info(f"{_Color.YELLOW}ВЕРДИКТ ИИ: SKIP{_Color.RESET} "
                 f"| ROI {roi}% | профит {net} TON | {reason}")


def _short(addr: str) -> str:
    """Укорачивает длинный адрес для читаемого лога."""
    return f"{addr[:6]}...{addr[-4:]}" if addr and len(addr) > 12 else (addr or "?")


# =============================================================================
# 9. ГЛАВНЫЙ ЦИКЛ МОНИТОРИНГА (MONITORING LOOP)
# =============================================================================

def preflight_checks():
    """Проверяет обязательные настройки перед запуском цикла."""
    problems = []
    if not ANTHROPIC_API_KEY:
        problems.append("ANTHROPIC_API_KEY не задан (нужен для ИИ-анализа).")
    if not TARGET_COLLECTION or TARGET_COLLECTION.startswith("EQAAAAAA"):
        problems.append("TARGET_COLLECTION не задан — впишите реальный адрес коллекции.")
    elif normalize_ton_address(TARGET_COLLECTION) is None:
        problems.append(f"TARGET_COLLECTION нераспознан как адрес TON: {TARGET_COLLECTION!r}")

    # Адреса в whitelist тоже должны быть валидны, иначе защита молча не работает.
    for addr in COLLECTION_WHITELIST:
        if normalize_ton_address(addr) is None:
            problems.append(f"COLLECTION_WHITELIST содержит нераспознанный адрес: {addr!r}")

    if problems:
        for p in problems:
            log.error(p)
        return False

    if not COLLECTION_WHITELIST:
        log.warning(f"{_Color.YELLOW}COLLECTION_WHITELIST пуст — проверка на скам-коллекции "
                    f"ОТКЛЮЧЕНА. Укажите доверенные адреса перед реальной торговлей."
                    f"{_Color.RESET}")
    return True


def main():
    log.info(f"{_Color.BOLD}=== Telegram Gifts NFT Sniper запускается ==={_Color.RESET}")
    log.info(f"Модель ИИ: {CLAUDE_MODEL} | Коллекция: {_short(TARGET_COLLECTION)} | "
             f"Интервал: {POLL_INTERVAL_SEC}s | DRY_RUN: {DRY_RUN}")

    if not preflight_checks():
        sys.exit("[FATAL] Исправьте настройки выше и перезапустите.")

    # Клиент Claude. Ключ читается из ANTHROPIC_API_KEY автоматически.
    client = Anthropic(api_key=ANTHROPIC_API_KEY)

    cycle = 0
    while True:  # <-- бесконечный цикл реального мониторинга
        cycle += 1
        started = time.monotonic()
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        log.info(f"{_Color.BOLD}--- Проверка #{cycle} @ {now} ---{_Color.RESET}")

        try:
            snap = get_market_snapshot(TARGET_COLLECTION)
            candidates = snap["candidates"]

            if not candidates:
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
                recent_sales = fetch_recent_sales_count(TARGET_COLLECTION)
                if recent_sales is not None:
                    log.info(f"Продаж за {LIQUIDITY_WINDOW_HOURS}ч: {recent_sales}")

                # Прогоняем самые дешёвые лоты через ИИ.
                for item in candidates:
                    process_item(client, item, snap, recent_sales)

        except KeyboardInterrupt:
            raise
        except Exception as e:  # noqa: BLE001 — цикл не должен падать целиком
            log.error(f"Ошибка в цикле мониторинга: {e}")

        # Держим стабильный интервал (учитываем время работы итерации).
        elapsed = time.monotonic() - started
        sleep_for = max(0, POLL_INTERVAL_SEC - elapsed)
        log.info(f"{_Color.GREY}Итерация заняла {elapsed:.1f}s. Сплю {sleep_for:.1f}s...{_Color.RESET}")
        time.sleep(sleep_for)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log.info(f"{_Color.BOLD}Остановлено пользователем. До встречи!{_Color.RESET}")
        sys.exit(0)
