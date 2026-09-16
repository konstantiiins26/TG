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
import sys
import json
import time
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

# --- Экономика сделки (используется и в промпте ИИ, и в локальной проверке) ---
MARKETPLACE_FEE_PCT = Decimal(os.getenv("MARKETPLACE_FEE_PCT", "0.05"))   # 5% комиссия площадки при перепродаже
GAS_FEE_TON         = Decimal(os.getenv("GAS_FEE_TON", "0.15"))           # газ (сеть) в TON
MIN_ROI_PCT         = Decimal(os.getenv("MIN_ROI_PCT", "5"))             # ниже этого ROI бот не покупает

# --- Параметры цикла мониторинга --------------------------------------------
POLL_INTERVAL_SEC   = int(os.getenv("POLL_INTERVAL_SEC", "12"))           # задержка между проверками (10–15 сек)
ITEMS_PER_POLL      = int(os.getenv("ITEMS_PER_POLL", "20"))              # сколько лотов тянуть за одну проверку
HTTP_TIMEOUT_SEC    = int(os.getenv("HTTP_TIMEOUT_SEC", "15"))           # таймаут HTTP-запросов

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
# 3. СБОР ДАННЫХ С РЫНКА (БЕСПЛАТНЫЕ ПУБЛИЧНЫЕ API)
# =============================================================================

def _nano_to_ton(nano_value) -> Decimal:
    """Конвертирует цену из нанотонов в TON. Безопасно обрабатывает None/мусор."""
    if nano_value in (None, "", "0", 0):
        return Decimal("0")
    try:
        return (Decimal(str(nano_value)) / NANO_PER_TON).quantize(Decimal("0.000000001"))
    except (InvalidOperation, ValueError):
        return Decimal("0")


def fetch_items_tonapi(collection: str, limit: int):
    """
    ОСНОВНОЙ ИСТОЧНИК: TonAPI.io
    GET /v2/nfts/collections/{account_address}/items

    Возвращает список нормализованных лотов (см. _normalize_item).
    Работает и без ключа (низкие лимиты), с ключом TONAPI_KEY — выше квота.
    """
    url = f"https://tonapi.io/v2/nfts/collections/{collection}/items"
    params = {"limit": limit, "offset": 0}
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

        items.append(_normalize_item(
            address=nft.get("address", ""),
            collection_name=(nft.get("collection") or {}).get("name") or meta.get("name", "Unknown"),
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


def _normalize_item(address, collection_name, mint_index, sale_price_ton, is_on_sale):
    """Единый формат лота для всего приложения (и для отправки в ИИ)."""
    return {
        "address": address,
        "collection_name": collection_name,
        "mint_index": mint_index,
        "sale_price_ton": float(sale_price_ton),   # float для JSON-сериализации в ИИ
        "is_on_sale": is_on_sale,
    }


def get_market_snapshot(collection: str, limit: int):
    """
    Возвращает (items, floor_price_ton).

    items       — список выставленных на продажу лотов.
    floor_price — минимальная цена продажи среди них (Floor Price коллекции).

    Сначала пробуем TonAPI, при ошибке — Getgems.
    """
    items = []
    try:
        items = fetch_items_tonapi(collection, limit)
        source = "TonAPI"
    except Exception as e:  # noqa: BLE001 — намеренно ловим всё, чтобы уйти на фолбэк
        log.warning(f"TonAPI недоступен ({e}). Переключаюсь на Getgems...")
        try:
            items = fetch_items_getgems(collection, limit)
            source = "Getgems"
        except Exception as e2:  # noqa: BLE001
            log.error(f"Оба источника недоступны. Getgems: {e2}")
            return [], Decimal("0"), "none"

    # Оставляем только реально продающиеся лоты.
    on_sale = [it for it in items if it["is_on_sale"] and it["sale_price_ton"] > 0]

    # Floor Price = минимальная цена среди выставленных лотов.
    floor = min((Decimal(str(it["sale_price_ton"])) for it in on_sale), default=Decimal("0"))
    return on_sale, floor, source


# =============================================================================
# 4. ЛОКАЛЬНАЯ ЭКОНОМИКА (быстрая проверка до вызова ИИ)
# =============================================================================

def compute_net_profit(floor_price: Decimal, buy_price: Decimal) -> Decimal:
    """
    Формула чистой прибыли (та же, что зашита в промпт ИИ):

        Profit = (Floor - Buy) - (Buy * fee) - gas

    Логика: покупаем по buy_price, перепродаём по floor_price, платим
    комиссию площадки с цены покупки и газ сети.
    """
    return (floor_price - buy_price) - (buy_price * MARKETPLACE_FEE_PCT) - GAS_FEE_TON


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
# 5. ИИ-МОЗГ — АНАЛИЗ РЫНКА ЧЕРЕЗ CLAUDE API
# =============================================================================

# Системный промпт с ЖЁСТКИМИ правилами. ИИ обязан вернуть строгий JSON.
AI_SYSTEM_PROMPT = f"""\
Ты — беспощадный риск-аналитик рынка NFT-подарков Telegram (сеть TON).
Твоя единственная цель — не дать боту переплатить и купить только то, что
даёт реальную чистую прибыль при перепродаже по Floor Price.

ПРАВИЛА РАСЧЁТА (соблюдай буквально):

1) ЧИСТАЯ ПРИБЫЛЬ (в TON):
   NET_PROFIT = (Floor_Price - Buy_Price) - (Buy_Price * {MARKETPLACE_FEE_PCT}) - {GAS_FEE_TON}
   где {MARKETPLACE_FEE_PCT} — комиссия площадки (5%), {GAS_FEE_TON} TON — газ сети.

2) ROI в процентах:
   ROI_PERCENT = NET_PROFIT / Buy_Price * 100

3) ОТСЕЧЕНИЕ ХАЙПА / ПЕРЕПЛАТЫ (критично!):
   Считай номер минта "красивым", ТОЛЬКО если он входит в топ-100 (<= 100)
   ЛИБО является классическим красивым числом (например 7, 77, 777, 1111,
   5555, 8888, 9999). Если номер НЕ красивый и НЕ в топ-100, то ЛЮБАЯ цена
   покупки ВЫШЕ Floor_Price — это ПЕРЕПЛАТА за хайп. В этом случае немедленно
   выдавай ACTION = "SKIP", даже если формально прибыль кажется возможной.

4) РЕШЕНИЕ:
   ACTION = "BUY"  только если ОДНОВРЕМЕННО:
       - NET_PROFIT > 0,
       - ROI_PERCENT >= {MIN_ROI_PCT},
       - нет переплаты по правилу (3).
   Иначе ACTION = "SKIP".

ФОРМАТ ОТВЕТА — СТРОГО ОДИН JSON-ОБЪЕКТ, без markdown, без пояснений вокруг:
{{
  "ACTION": "BUY" | "SKIP",
  "ROI_PERCENT": <число>,
  "NET_PROFIT_TON": <число>,
  "REASON": "<краткое объяснение на русском>"
}}
"""


def ai_analyze(client: Anthropic, item: dict, floor_price: Decimal) -> dict:
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
        "marketplace_fee_pct": float(MARKETPLACE_FEE_PCT),
        "gas_fee_ton": float(GAS_FEE_TON),
        "min_roi_pct": float(MIN_ROI_PCT),
        "is_top_100_or_pretty": is_pretty_mint(item["mint_index"]),
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
# 6. БЛОКЧЕЙН — ЗАГЛУШКА ПОКУПКИ (реальная подпись НЕ выполняется)
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
# 7. ОБРАБОТКА ОДНОГО ЛОТА (сбор -> ИИ -> действие)
# =============================================================================

def process_item(client: Anthropic, item: dict, floor_price: Decimal):
    """Прогоняет один лот через ИИ и, при вердикте BUY, вызывает покупку."""
    buy_price = Decimal(str(item["sale_price_ton"]))
    mint = item["mint_index"]
    pretty = is_pretty_mint(mint)

    # Локальный предрасчёт (для лога; финальное слово — за ИИ).
    local_profit = compute_net_profit(floor_price, buy_price)
    local_roi = compute_roi_pct(local_profit, buy_price)

    log.info(
        f"Лот {_short(item['address'])} | mint #{mint} "
        f"{'★красивый' if pretty else 'обычный'} | "
        f"цена {buy_price} TON | floor {floor_price} TON | "
        f"локальный ROI {local_roi}%"
    )

    # Вердикт ИИ.
    verdict = ai_analyze(client, item, floor_price)
    action = str(verdict.get("ACTION", "SKIP")).upper()
    roi = verdict.get("ROI_PERCENT", 0)
    net = verdict.get("NET_PROFIT_TON", 0)
    reason = verdict.get("REASON", "")

    if action == "BUY":
        log.info(f"{_Color.GREEN}{_Color.BOLD}ВЕРДИКТ ИИ: BUY{_Color.RESET} "
                 f"| ROI {roi}% | профит {net} TON | {reason}")
        execute_blockchain_buy(item["address"], buy_price)
    else:
        log.info(f"{_Color.YELLOW}ВЕРДИКТ ИИ: SKIP{_Color.RESET} "
                 f"| ROI {roi}% | профит {net} TON | {reason}")


def _short(addr: str) -> str:
    """Укорачивает длинный адрес для читаемого лога."""
    return f"{addr[:6]}...{addr[-4:]}" if addr and len(addr) > 12 else (addr or "?")


# =============================================================================
# 8. ГЛАВНЫЙ ЦИКЛ МОНИТОРИНГА (MONITORING LOOP)
# =============================================================================

def preflight_checks():
    """Проверяет обязательные настройки перед запуском цикла."""
    problems = []
    if not ANTHROPIC_API_KEY:
        problems.append("ANTHROPIC_API_KEY не задан (нужен для ИИ-анализа).")
    if not TARGET_COLLECTION or TARGET_COLLECTION.startswith("EQAAAAAA"):
        problems.append("TARGET_COLLECTION не задан — впишите реальный адрес коллекции.")
    if problems:
        for p in problems:
            log.error(p)
        return False
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
            items, floor, source = get_market_snapshot(TARGET_COLLECTION, ITEMS_PER_POLL)

            if not items:
                log.warning("Активных лотов на продаже не найдено. Жду следующей проверки.")
            else:
                log.info(f"Источник: {source} | Лотов на продаже: {len(items)} | "
                         f"Floor Price: {floor} TON")
                # Прогоняем каждый обнаруженный подарок через ИИ.
                for item in items:
                    process_item(client, item, floor)

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
