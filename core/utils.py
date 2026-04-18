import asyncio
import json
import os
from os.path import basename
from typing import Union, Any, Iterable, Optional, Literal, Dict, List

from .visuals import Display
from .constants import WEBHOOK_PATTERN
from .clients import ClientSession


# ==================== RETRY DECORATOR ====================
def retry_async(max_attempts: int = 3, delay: float = 1.0, backoff: float = 2.0):
    def decorator(func):
        async def wrapper(*args, **kwargs):
            attempts = 0
            current_delay = delay
            while attempts < max_attempts:
                try:
                    return await func(*args, **kwargs)
                except Exception as e:
                    attempts += 1
                    if attempts == max_attempts:
                        raise
                    print(f"[WARN] {func.__name__} failed: {e}. Retrying in {current_delay}s...")
                    await asyncio.sleep(current_delay)
                    current_delay *= backoff
            return None
        return wrapper
    return decorator
# =========================================================


class IgnoreNew:
    def __set_name__(self, _, name: str) -> None:
        self.name = f"_{name}"

    def __get__(self, instance, _):
        return getattr(instance, self.name)

    def __set__(self, instance, value):
        if getattr(instance, self.name, None):
            return None
        setattr(instance, self.name, value)


class WithBool:
    def __init__(self) -> None:
        self.__bool = False

    def __enter__(self):
        self.__bool = True

    def __exit__(self, *_):
        self.__bool = False

    def __bool__(self) -> bool:
        return self.__bool

    def __repr__(self) -> str:
        return str(self.__bool)


class AssetsLoader:
    def __init__(self, func: callable, source: Iterable, batch_amount: Optional[int] = None):
        self.wrapped = func
        self.source = source
        self.batch_amount = (batch_amount or 0)

    async def load(self, *func_args, **func_kwargs):
        tasks = [
            asyncio.create_task(self.wrapped(s, *func_args, **func_kwargs))
            for s in slice_list(self.source, self.batch_amount)
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        valid_results = [r for r in results if r is not None and not isinstance(r, Exception)]
        return sum(valid_results, [])


class FileSync(set):
    def __init__(self, filename: str) -> None:
        self.filename = filename
        items = load_file(filename)
        if not isinstance(items, list):
            Display.exception("Invalid format type provided")
        super().__init__(items)

    def __getattribute__(self, name: str) -> Union[Any, callable]:
        attr = super().__getattribute__(name)
        if (
            attr is not None
            and not name.startswith("_")
            and callable(attr)
        ):
            def wrapper(*args, **kwargs):
                attr(*args, **kwargs)
                safe_json_write(list(self), self.filename)
            return wrapper
        return attr


def slice_list(iterable: Iterable[Any], n: int) -> Iterable[Iterable[Any]]:
    if not n:
        return iterable
    lst = list(iterable)
    return [lst[i:i + n] for i in range(0, len(lst), n)]


def define_status(flag: bool) -> str:
    return "Enabled" if flag else "Disabled"


def safe_json_write(data: Any, file_path: str) -> None:
    os.makedirs(os.path.dirname(file_path), exist_ok=True)
    temp_path = file_path + ".tmp"
    with open(temp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)
    os.replace(temp_path, file_path)


def load_file(file_path: str) -> Union[Dict, List]:
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        default = {} if file_path.endswith(".json") and "config" in file_path else []
        safe_json_write(default, file_path)
        return default
    except json.JSONDecodeError:
        file_name = basename(file_path)
        Display.exception(f"Failed to decode \"{file_name}\", using empty default")
        return [] if "blacklist" in file_path or "seen" in file_path else {}
    except Exception as err:
        file_name = basename(file_path)
        Display.exception(f"Failed to load \"{file_name}\": {err}")
        return {}


def define_sale_price(undercut_amount: int, undercut_type: Literal["amount", "percent"],
                      limit_price: int, lowest_price: int) -> int:
    def min_sale(price: int) -> int:
        profit = price // 2
        while (price // 2) >= profit:
            price -= 1
        return price + 1

    if undercut_type == "amount":
        final_price = lowest_price - undercut_amount
    else:
        final_price = round(lowest_price - (lowest_price / 100 * undercut_amount))

    final_price = min_sale(final_price)
    return final_price if final_price > limit_price else limit_price


@retry_async(max_attempts=3, delay=1.0)
async def is_webhook_exists(webhook_url: str) -> bool:
    if not WEBHOOK_PATTERN.match(webhook_url):
        return False
    async with ClientSession() as session:
        async with session.get(webhook_url) as response:
            data = await response.json()
            return data.get("name") is not None


@retry_async(max_attempts=2, delay=2.0)
async def check_for_update(code_url: str, _version: str) -> bool:
    async with ClientSession() as session:
        async with session.get(code_url) as response:
            text = await response.text()
            try:
                version = text.strip().split('VERSION = "')[1].split('"')[0]
            except IndexError:
                return False
            return version != _version
