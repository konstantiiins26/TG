# tonutils — проверенный API (не по памяти)

Всё ниже получено **интроспекцией установленного пакета**, а не из памяти.
Записано, чтобы при следующем подходе не выяснять заново.

Установка: `pip install tonutils ton-core`
(`tonutils.utils` переехал в отдельный пакет `ton-core`.)

## Раскладка пакета — НЕ та, что ожидается интуитивно

Модулей `tonutils.wallet` и `tonutils.nft` **не существует**. Реально:

```
tonutils.contracts     # BaseWallet, WalletV4R2, WalletV5R1, NFT*, Jetton*
tonutils.clients       # TonapiClient, ToncenterClient, LiteBalancer, ...
tonutils.clients.base  # NetworkGlobalID
tonutils.types         # Cell, StateInit, ...
ton_core               # to_nano, Address (отдельный пакет!)
```

## Идентификаторы сети

```python
from tonutils.clients.base import NetworkGlobalID
NetworkGlobalID.MAINNET  # -239
NetworkGlobalID.TESTNET  # -3
NetworkGlobalID.TETRA    # 662387
```

## Клиент

```python
TonapiClient(network: NetworkGlobalID, *, api_key: str | None = None,
             base_url=None, timeout=10.0, session=None, headers=None,
             cookies=None, rps_limit=None, rps_period=None, retry_policy=None)
```

## Кошелёк

Версии: `WalletV4R2`, `WalletV5R1`, `WalletV3R2`, `WalletHighloadV3R1`, `WalletTg` и др.

```python
# СИНХРОННЫЕ конструкторы:
WalletV4R2.from_mnemonic(client, mnemonic: list[str] | str, validate=True,
                         workchain=WorkchainID.BASECHAIN, config=None)
    -> tuple[wallet, PublicKey, PrivateKey, list[str]]      # ВОЗВРАЩАЕТ КОРТЕЖ

WalletV4R2.from_private_key(client, private_key: PrivateKey, ...) -> wallet
WalletV4R2.from_address(client, address, load_state=True)
```

Свойства экземпляра: `address`, `balance`, `state`, `is_active`, `is_uninit`,
`last_transaction_hash`, `client`.

## Отправка перевода — АСИНХРОННАЯ, и она РЕАЛЬНО ОТПРАВЛЯЕТ

```python
async def transfer(destination: AddressLike,
                   amount: int,                 # ⚠ В НАНОТОНАХ, не в TON
                   body: Cell | str | None = None,
                   state_init: StateInit | None = None,
                   send_mode: SendMode | int = 3,
                   bounce: bool | None = None,   # None = автоопределение
                   params=None) -> ExternalMessage
```

Docstring в исходнике: *"Send a simple TON transfer … :return: **Sent**
ExternalMessage"* — то есть метод не просто собирает сообщение, а отправляет.

`ExternalMessage` имеет `.to_boc()`, `.to_cell()`, `.serialize()`.

`SendMode`: `PAY_GAS_SEPARATELY=1`, `IGNORE_ERRORS=2`,
`CARRY_ALL_REMAINING_BALANCE=128`, `BOUNCE_IF_ACTION_FAIL=16`, `DEFAULT=0`.
Значение по умолчанию у `transfer` — `3` (= 1|2).

## Баланс — ЛОВУШКА: состояние не загружается автоматически

`balance` — синхронное свойство, отдаёт **нанотоны**:

```python
@property
def balance(self) -> int:
    """Contract balance in nanotons."""
    return self.info.balance
```

Но `info` — это *кэш* состояния, и он пуст, пока состояние не загружено:

```python
@property
def info(self) -> ContractInfo:
    if self._info is None:
        raise StateNotLoadedError(self, missing="info")   # ← НЕ ноль, а исключение
    return self._info
```

**`from_mnemonic()` состояние НЕ грузит.** Она сводится к `from_private_key()`,
которая лишь выводит адрес из code+data — в блокчейн не ходит. Поэтому:

```python
wallet, _pub, _priv, _words = WalletV4R2.from_mnemonic(client, mnemonic)
wallet.balance          # ❌ StateNotLoadedError

await wallet.refresh()  # async: self._info = await self._load_info(...)
wallet.balance          # ✅ int, нанотоны
```

Исключение импортируется как `from tonutils.exceptions import StateNotLoadedError`.

Наивный код («создал кошелёк → прочитал баланс») падает. Это ровно тот случай,
где написанное по памяти выглядит правильным и не работает.

Исключение из правила: `from_address(client, address, load_state=True)` —
у неё загрузка состояния включена по умолчанию.

## Нанотоны и адреса

```python
from ton_core import to_nano, Address
to_nano(1.5)                    # 1500000000
Address("0:" + "a1"*32).to_str()  # -> EQ... форма
```

## Что это значит для бота

1. **Бот синхронный, tonutils — асинхронный.** Покупку придётся заворачивать
   в `asyncio.run(...)`. Это приемлемо: покупки редки, граница чистая.

2. **Платёж уходит на КОНТРАКТ ПРОДАЖИ, а не на NFT.** Нужен
   `sale.address` из ответа TonAPI. Сейчас парсер его **не сохраняет** —
   это обязательная правка перед любой попыткой покупки.

3. **Сверх цены нужен газ** на исполнение контракта продажи и пересылку NFT
   (излишек контракт возвращает). Это отдельная величина от `GAS_FEE_TON`
   в экономике, где учитывается безвозвратная стоимость круга.

4. **Ключ — это мнемоника** (24 слова), её принимает `from_mnemonic`.
   Хранить только в файле с правами 0600, не в переменной окружения.

4а. **Перед проверкой баланса — `await wallet.refresh()`.** Без него чтение
   `wallet.balance` бросает `StateNotLoadedError`. Баланс в нанотонах.

5. `REAL_EXECUTOR_AVAILABLE` должен определяться **успехом импорта**
   tonutils, а не константой: если библиотеки нет, живой режим обязан быть
   заблокирован заранее, а не падать в момент покупки.

## Статус

Подпись транзакций **не внедрена в бота**: правки торгового пути блокируются
классификатором среды разработки (категория «реальные транзакции»). API выше
проверен и готов к применению — нужно лишь разрешение на правку.
