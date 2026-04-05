from __future__ import annotations

import sys
import os
import traceback
import asyncio
import random
import json
from traceback import format_exc

# ========== DEBUG MODE (controlled by config.json) ==========
DEBUG = True
PURPLE = '\033[95m'
RESET = '\033[0m'

def debug_print(*args, **kwargs):
    if DEBUG:
        print(PURPLE, "[DEBUG]", *args, RESET, **kwargs)
# =============================================================

__import__("warnings").filterwarnings("ignore")

try:
    import discord
    import aioconsole
    from rgbprint import Color
    from datetime import datetime
    from pypresence import AioPresence, DiscordNotFound

    from typing import List, Optional, Any, Union, AsyncGenerator, Iterable, TYPE_CHECKING
    from discord.errors import LoginFailure
    from asyncio import Task
    if TYPE_CHECKING:
        from .discord_bot.visuals.view import ControlPanel

    from core.instances import *
    from core.main_tools import *
    from core.clients import *
    from core.visuals import *
    from core.detection import *
    from core.utils import *
    from core.constants import VERSION, RAW_CODE_URL, ITEM_TYPES, PRESENCE_BOT_ID, URL_REPOSITORY
    from discord_bot import start as discord_bot_start

    os.system("cls" if os.name == "nt" else "clear")
except ModuleNotFoundError as e:
    print(traceback.format_exc())
    install = input("Uninstalled modules found, do you want to install them? Y/n: ").lower() == "y"
    if install:
        print("Installing modules now...")
        os.system("pip uninstall pycord")
        os.system("pip install aiohttp rgbprint discord.py aioconsole pypresence")
        print("Successfully installed required modules.")
    else:
        print("Aborting installing modules.")
    input("Press \"enter\" to exit...")
    sys.exit(1)

__all__ = ("AutoSeller",)

# ==================== PRICE FLOOR MANAGER ====================
FLOOR_FILE = "core/price_floors.json"

def load_floors():
    try:
        with open(FLOOR_FILE, "r") as f:
            return json.load(f)
    except:
        return {}

def save_floors(floors):
    try:
        with open(FLOOR_FILE, "w") as f:
            json.dump(floors, f, indent=4)
    except Exception as e:
        debug_print(f"Failed to save floors: {e}")

def update_floor(asset_type_name: str, observed_price: int):
    floors = load_floors()
    current = floors.get(asset_type_name)
    if current is None or observed_price < current:
        floors[asset_type_name] = observed_price
        save_floors(floors)
        debug_print(f"Updated floor for {asset_type_name}: {observed_price}")
    return floors.get(asset_type_name, observed_price)

def get_floor(asset_type_name: str) -> Optional[int]:
    return load_floors().get(asset_type_name)
# =============================================================

# ==================== AUTOMATIC FLOOR DETECTION ====================
async def get_current_cap(auth):
    from core.constants import ITEM_TYPES
    caps = {}
    for asset_type_name, asset_type_id in ITEM_TYPES.items():
        url = f"https://economy.roblox.com/v1/assets/price-floor?assetTypeId={asset_type_id}"
        try:
            async with auth.get(url) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if isinstance(data, (int, float)) and data > 0:
                        caps[asset_type_name] = {"priceFloor": int(data)}
                        continue
                    elif isinstance(data, dict) and data.get("priceFloor", 0) > 0:
                        caps[asset_type_name] = {"priceFloor": data["priceFloor"]}
                        continue
        except:
            pass
        caps[asset_type_name] = None

    debug_print("Fetching your own on-sale items to infer floors...")
    try:
        user_id = auth.user_id
        url = f"https://economy.roblox.com/v1/users/{user_id}/resellable-items?limit=100"
        async with auth.get(url) as resp:
            if resp.status == 200:
                data = await resp.json()
                for item in data.get("data", []):
                    asset_type_id = item.get("assetType")
                    price = item.get("price", 0)
                    if price <= 0:
                        continue
                    for name, type_id in ITEM_TYPES.items():
                        if type_id == asset_type_id:
                            if caps.get(name) is None or caps[name]["priceFloor"] > price:
                                caps[name] = {"priceFloor": price}
                            break
    except Exception as e:
        debug_print(f"Failed to scan your own items: {e}")

    default_floors = {
        "Emote": 50, "Hat": 90, "HairAccessory": 60, "FaceAccessory": 90,
        "NeckAccessory": 50, "ShoulderAccessory": 50, "FrontAccessory": 50,
        "BackAccessory": 135, "WaistAccessory": 60, "TShirtAccessory": 61,
        "ShirtAccessory": 55, "PantsAccessory": 65, "JacketAccessory": 60,
        "SweaterAccessory": 61, "ShortsAccessory": 55, "DressSkirtAccessory": 55,
    }
    for asset_type_name in ITEM_TYPES.values():
        if caps.get(asset_type_name) is None:
            floor = default_floors.get(asset_type_name, 5)
            caps[asset_type_name] = {"priceFloor": floor}
            debug_print(f"Using default floor for {asset_type_name}: {floor}")

    debug_print(f"Final price floors: {caps}")
    return caps

import core.main_tools
core.main_tools.get_current_cap = get_current_cap
# ==================================================================

class AutoSeller(ConfigLoader):
    __slots__ = ("config", "_items", "auth", "buy_checker", "blacklist",
                 "seen", "not_resable", "current_index", "done",
                 "total_sold", "selling", "loaded_time", "control_panel")

    def __init__(self, config: dict, blacklist: FileSync, seen: FileSync, not_resable: FileSync) -> None:
        super().__init__(config)
        self.config = config
        self._items = dict()
        self.auth = Auth(config.get("Cookie", "").strip())
        self.buy_checker = BuyChecker(self)
        self.blacklist = blacklist
        self.seen = seen
        self.not_resable = not_resable
        if self.presence_enabled:
            self.rich_presence = AioPresence(PRESENCE_BOT_ID)
        self.current_index = 0
        self.done = False
        self.total_sold = 0
        self.selling = WithBool()
        self.loaded_time: datetime = None
        self.control_panel: ControlPanel = None

    @property
    def items(self) -> List[Item]:
        return list(self._items.values())

    @property
    def current(self) -> Item:
        return self.items[self.current_index]

    def get_item(self, _id: int, default=None):
        return self._items.get(_id, default)

    def add_item(self, item: Item) -> Item:
        self._items.update({item.id: item})
        return item

    def remove_item(self, _id: int) -> Item:
        return self._items.pop(_id)

    def next_item(self, *, step_index: int = 1) -> None:
        self.current_index = (self.current_index + 1) % len(self.items)
        if self.presence_enabled:
            asyncio.create_task(self.update_presence())
        if not self.current_index:
            self.done = True
        self.fetch_item_info(step_index=step_index)

    async def update_presence(self) -> None:
        await self.rich_presence.update(
            state=f"{self.current_index + 1} out of {len(self.items)}",
            details=f"Selling {self.current.name} limited",
            large_image=self.current.thumbnail,
            large_text=f"{self.current.name} limited",
            small_image="https://cdn.discordapp.com/app-assets/1005469189907173486/1025422070600978553.png?size=160",
            small_text="Roblox",
            buttons=[{"url": self.current.link, "label": "Selling Item"},
                     {"url": URL_REPOSITORY, "label": "Use Tool Yourself"}],
            start=int(self.loaded_time.timestamp())
        )

    async def filter_non_resable(self):
        if (self.current_index + 2) % 30 or not self.current_index:
            return None
        endpoints = [
            "https://apis.roblox.com/marketplace-items/v1/items/details",
            "https://economy.roblox.com/v2/assets/details",
            "https://catalog.roblox.com/v1/assets/details"
        ]
        item_ids = [i.item_id for i in self.items[self.current_index:30]]
        data = None
        for endpoint in endpoints:
            try:
                async with self.auth.post(endpoint, json={"itemIds": item_ids}) as response:
                    if response.status == 200:
                        data = await response.json()
                        break
            except:
                continue
        if data is None:
            return
        items_list = data if not isinstance(data, dict) else data.get("data", data)
        for item_details in items_list:
            item_id = item_details.get("itemTargetId") or item_details.get("assetId")
            if item_id and item_details.get("resaleRestriction") == 1:
                self.not_resable.add(item_id)
                self.remove_item(item_id)

    # ========== MULTI‑API LOWEST PRICE CHECK ==========
    async def get_lowest_price_multi(self, item_id: int, item_obj: Optional[Item] = None) -> Optional[int]:
        prices = []

        async def fetch_price(url):
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Accept": "application/json",
                "Referer": "https://www.roblox.com/",
            }
            try:
                async with self.auth.get(url, headers=headers) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        if data.get("data") and len(data["data"]) > 0:
                            return data["data"][0].get("price", 0)
                    else:
                        debug_print(f"API {url} returned {resp.status}")
            except Exception as e:
                debug_print(f"Request error for {url}: {e}")
            return None

        if item_obj and item_obj.lowest_resale_price and item_obj.lowest_resale_price > 0:
            prices.append(item_obj.lowest_resale_price)
            debug_print(f"Stored lowest_resale_price: {item_obj.lowest_resale_price}")

        price1 = await fetch_price(f"https://economy.roblox.com/v1/assets/{item_id}/resellers")
        if price1:
            prices.append(price1)
            debug_print(f"economy.roblox.com price: {price1}")

        price2 = await fetch_price(f"https://apis.roblox.com/marketplace-sales/v1/item/{item_id}/resellers?limit=1")
        if price2:
            prices.append(price2)
            debug_print(f"marketplace-sales API price: {price2}")

        price3 = await fetch_price(f"https://catalog.roblox.com/v1/assets/{item_id}/resellers")
        if price3:
            prices.append(price3)
            debug_print(f"catalog.roblox.com price: {price3}")

        if prices:
            lowest = min(prices)
            debug_print(f"All prices: {prices}, lowest: {lowest}")
            return lowest
        return None
    # ==================================================

    # ========== FIXED SELL WITH FLOOR PROTECTION ==========
    async def sell_item(self):
        item = self.current
        debug_print(f"Selling item: {item.name} (ID {item.id})")
        await Display.custom(
            f"Selling [g{len(item.collectibles)}x] of [g{item.name}] items...",
            "selling", Color(255, 153, 0))

        # Get asset type name (stored during item creation)
        asset_type_name = getattr(item, 'asset_type_name', 'Unknown')
        debug_print(f"Asset type: {asset_type_name}")

        # Get the floor for this asset type
        floor = get_floor(asset_type_name)
        if floor is None:
            floor = 5
            debug_print(f"No floor found for {asset_type_name}, using default 5")

        # Get current lowest price
        lowest_price = await self.get_lowest_price_multi(item.id, item)

        if lowest_price and lowest_price > 0:
            # Update floor if this price is lower than saved
            new_floor = update_floor(asset_type_name, lowest_price)
            if new_floor != floor:
                floor = new_floor

            # If the lowest price equals the floor, do NOT undercut – sell at floor
            if lowest_price == floor:
                target_price = floor
                debug_print(f"Lowest price ({lowest_price}) equals floor ({floor}). Selling at floor, no undercut.")
            else:
                # Apply undercut
                if self.under_cut_type == "percent":
                    undercut_amount = int(lowest_price * (self.under_cut_amount / 100))
                    target_price = lowest_price - undercut_amount
                else:
                    target_price = lowest_price - self.under_cut_amount

                # Ensure we are at least 1 lower, but never below floor
                if target_price >= lowest_price:
                    target_price = lowest_price - 1
                if target_price < floor:
                    debug_print(f"Undercut price {target_price} would be below floor {floor}. Setting to floor.")
                    target_price = floor

            if target_price < 5:
                target_price = 5

            debug_print(f"Competition found (lowest={lowest_price}), undercut applied → {target_price}")
        else:
            # No competition – use configurable default
            target_price = self.default_price_no_competition
            # But also respect floor: if default is below floor, use floor
            if floor and target_price < floor:
                debug_print(f"Default price {target_price} below floor {floor}, raising to floor.")
                target_price = floor
            debug_print(f"⚠️ No competition found for {item.name}! Using Default_Price_No_Competition: {target_price}")

        item.price_to_sell = target_price

        # Attempt to sell
        max_retries = 3
        sold_amount = None
        for attempt in range(max_retries):
            try:
                sold_amount = await item.sell_collectibles(
                    skip_on_sale=self.skip_on_sale,
                    skip_if_cheapest=self.skip_if_cheapest,
                    verbose=True
                )
                if sold_amount is not None:
                    if sold_amount > 0:
                        self.total_sold += sold_amount
                        if self.sale_webhook:
                            asyncio.create_task(self.send_sale_webhook(item, sold_amount))
                    break
                else:
                    break
            except Exception as e:
                error_msg = str(e).lower()
                if "rate limit" in error_msg or "429" in error_msg:
                    wait = 30 * (attempt + 1)
                    debug_print(f"Rate limited! Waiting {wait} seconds...")
                    await asyncio.sleep(wait)
                elif "412" in error_msg or "precondition failed" in error_msg:
                    debug_print(f"Item {item.id} returned 412 – not sellable. Skipping.")
                    break
                else:
                    debug_print(f"Unexpected error: {e}")
                    break

        delay = random.uniform(2, 5)
        debug_print(f"Waiting {delay:.1f} seconds before next item...")
        await asyncio.sleep(delay)

        if self.save_progress and item.id in self._items:
            self.seen.add(item.id)
        self.next_item()
    # =========================================================

    def fetch_item_info(self, *, step_index: int = 1) -> Optional[Iterable[Task]]:
        try:
            item = self.items[self.current_index + step_index]
        except IndexError:
            return None
        return (
            asyncio.create_task(item.fetch_sales(save_sales=False)),
            asyncio.create_task(item.fetch_resales(save_resales=False)),
            asyncio.create_task(self.filter_non_resable())
        )

    def sort_items(self, _type: str) -> None:
        self._items = dict(sorted(self._items.items(), key=lambda x: getattr(x[1], _type)))

    async def start(self):
        await asyncio.gather(self.auth.fetch_csrf_token(), self.handle_exceptions())
        Display.info("Checking cookie to be valid")
        if await self.auth.fetch_user_info() is None:
            return Display.exception("Invalid cookie provided")
        Display.info("Checking premium owning")
        if not await self.auth.fetch_premium():
            return Display.exception("You dont have premium to sell limiteds")
        await self._load_items()
        self.sort_items("name")
        if self.presence_enabled:
            try:
                await self.rich_presence.connect()
                await self.update_presence()
            except DiscordNotFound:
                return Display.exception("Could find Discord running to show presence")
        try:
            async with self:
                tasks = (
                    discord_bot_start(self) if self.discord_bot else None,
                    self.buy_checker.start() if self.buy_webhook else None,
                    self.auth.csrf_token_updater(),
                    self.start_selling()
                )
                await asyncio.gather(*filter(None, tasks))
        except LoginFailure:
            return Display.exception("Invalid discord token provided")
        except:
            return Display.exception(f"Unknown error occurred:\n\n{format_exc()}")

    async def start_selling(self):
        for i in range(2):
            for task in self.fetch_item_info(step_index=i):
                await task
        if self.auto_sell:
            await self._auto_sell_items()
        else:
            await self._manual_selling()
        Tools.clear_console()
        await Display.custom(f"Sold [g{self.total_sold}x] items", "done", Color(207, 222, 0))
        clear_items = await Display.input("Do you want to reset your selling progress? (Y/n): ")
        if clear_items.lower() == "y":
            self.seen.clear()
            Display.success("Cleared your limiteds selling progress")
            Tools.exit_program()

    async def _auto_sell_items(self):
        while not self.done:
            await self.sell_item()

    async def _manual_selling(self):
        while not self.done:
            await self.update_console()
            choice = (await aioconsole.ainput()).strip()
            match choice:
                case "1":
                    if self.selling:
                        Display.error("This item is already being sold")
                        await asyncio.sleep(0.7)
                        continue
                    with self.selling:
                        await self.sell_item()
                        if self.control_panel:
                            asyncio.create_task(self.control_panel.update_message(self.control_panel.make_embed()))
                case "2":
                    new_price = await Display.input("Enter the new price you want to sell: ")
                    if not new_price.isdigit() or int(new_price) < 0:
                        Display.error("Invalid price amount")
                        await asyncio.sleep(0.7)
                        continue
                    self.current.price_to_sell = int(new_price)
                    Display.success(f"Successfully set a new price to sell! ([g${self.current.price_to_sell}])")
                case "3":
                    self.blacklist.add(self.current.id)
                    self.next_item()
                    Display.success(f"Successfully added [g{self.current.name} ({self.current.id})] into blacklist!")
                case "4":
                    if self.save_progress:
                        self.seen.add(self.current.id)
                    self.next_item()
                    Display.skipping(f"Skipped [g{len(self.current.collectibles)}x] collectibles")
                case _:
                    continue
            await asyncio.sleep(0.7)

    async def __fetch_items(self) -> AsyncGenerator:
        Display.info("Loading your inventory")
        user_items = await AssetsLoader(get_user_inventory, ITEM_TYPES.keys()).load(self.auth)
        if not user_items:
            Display.exception("You dont have any limited UGC items")
        item_ids = [str(asset["assetId"]) for asset in user_items]
        Display.info("Loading items thumbnails")
        items_thumbnails = await AssetsLoader(get_assets_thumbnails, item_ids, 100).load(self.auth)
        Display.info(f"Found {len(user_items)} items. Checking them...")
        items_details = await AssetsLoader(get_items_details, item_ids, 120).load(self.auth)
        for item_info in zip(user_items, items_details, items_thumbnails):
            yield item_info

    async def _load_items(self) -> None:
        if self.loaded_time:
            return Display.exception("You have already loaded items")
        Display.info("Getting current limiteds cap")
        items_cap = await get_current_cap(self.auth)
        if not items_cap:
            items_cap = {t: {"priceFloor": 5} for t in ITEM_TYPES.values()}
        ignored_items = list(self.seen | self.blacklist | self.not_resable)
        async for item, item_details, thumbnail in self.__fetch_items():
            item_id = item["assetId"]
            if item_id in ignored_items or item_details["creatorTargetId"] in self.creators_blacklist:
                continue
            item_obj = self.get_item(item_id)
            if item_obj is None:
                asset_type_id = item_details["assetType"]
                asset_type_name = ITEM_TYPES.get(asset_type_id, "Unknown")
                if asset_type_name == "Unknown":
                    debug_print(f"Unknown asset type ID {asset_type_id} for item {item_id}, skipping")
                    continue
                asset_cap = items_cap.get(asset_type_name, {}).get("priceFloor", 5)

                lowest_resale = item_details.get("lowestResalePrice")
                if lowest_resale and lowest_resale > 0:
                    if self.under_cut_type == "percent":
                        undercut_amount = int(lowest_resale * (self.under_cut_amount / 100))
                        sell_price = lowest_resale - undercut_amount
                    else:
                        sell_price = lowest_resale - self.under_cut_amount
                    if sell_price < asset_cap:
                        sell_price = asset_cap
                else:
                    sell_price = self.default_price_no_competition

                # Create Item with asset_type_name parameter
                item_obj = Item(
                    item, item_details,
                    price_to_sell=sell_price,
                    thumbnail=thumbnail,
                    auth=self.auth,
                    asset_type_name=asset_type_name   # <-- pass it here
                )
                self.add_item(item_obj)
            item_obj.add_collectible(serial=item["serialNumber"], item_id=item["collectibleItemId"], instance_id=item["collectibleItemInstanceId"])
        if not self.items:
            Display.error("You dont have any limiteds that are not in blacklist")
            clear_items = await Display.input("Do you want to reset your selling progress? (Y/n): ")
            if clear_items.lower() == "y":
                self.seen.clear()
                Display.success("Cleared your limiteds selling progress")
                Tools.exit_program()
        if self.keep_serials or self.keep_copy:
            for item in self.items:
                if len(item.collectibles) <= self.keep_copy:
                    self.remove_item(item.id)
                    continue
                for col in item.collectibles:
                    if col.serial > self.keep_serials:
                        col.skip_on_sale = True
        if not self.items:
            not_met = []
            if self.keep_copy: not_met.append(f"{self.keep_copy} copies or higher")
            if self.keep_serials: not_met.append(f"{self.keep_serials} serial or higher")
            return Display.exception(f"You dont have any limiteds with {', '.join(not_met)}")
        self.loaded_time = datetime.now()

    async def update_console(self) -> None:
        Tools.clear_console()
        Display.main()
        item = self.current
        data = {
            "Info": {
                "Discord Bot": define_status(self.discord_bot),
                "Save Items": define_status(self.save_progress),
                "Under Cut": f"-{self.under_cut_amount}{'%' if self.under_cut_type == 'percent' else ''}",
                "Total Blacklist": f"{len(self.blacklist)}"
            },
            "Current Item": {
                "Name": item.name,
                "Creator": item.creator_name,
                "Price": f"{item.price:,}",
                "Quality": f"{item.quantity:,}",
                "Lowest Price": item.define_lowest_resale_price(),
                "Price to Sell": f"{item.price_to_sell:,}",
                "RAP": item.define_recent_average_price(),
                "Latest Sale": item.define_latest_sale()
            }
        }
        Display.sections(data)
        await Display.custom("[1] - Sell | [2] - Set Price | [3] - Blacklist | [4] - Skip\n> ", "input", BaseColors.gray, end="")

    async def send_sale_webhook(self, item: Item, sold_amount: int) -> None:
        embed = discord.Embed(color=2469096, timestamp=datetime.now(),
                              description=f"**Item name:** `{item.name}`\n**Sold amount:** `{sold_amount}`\n**Sold for:** `{item.price_to_sell}`",
                              title="A New Item Went on Sale", url=item.link)
        embed.set_footer(text="Were sold at")
        data = {"content": self.user_to_ping, "embeds": [embed.to_dict()]}
        async with ClientSession() as session:
            async with session.post(self.sale_webhook_url, json=data) as response:
                if response.status == 429:
                    await asyncio.sleep(30)
                    await self.send_sale_webhook(item, sold_amount)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        tasks = (self.auth.close_session(), self.control_panel.message.delete() if self.control_panel else None)
        await asyncio.gather(*filter(None, tasks))


async def main() -> None:
    global DEBUG
    Display.info("Setting up everything...")
    Display.info("Checking for updates")
    if await check_for_update(RAW_CODE_URL, VERSION):
        await Display.custom("Your code is outdated. Please update it from github", "new", Color(163, 133, 0), exit_after=True)

    Display.info("Loading config")
    config = load_file("config.json")

    DEBUG = config.get("Debug", False)
    debug_print(f"Debug mode: {'ON' if DEBUG else 'OFF'}")

    Display.info("Loading data assets")
    blacklist = FileSync("blacklist/blacklist.json")
    seen = FileSync("blacklist/seen.json")
    not_resable = FileSync("blacklist/not_resable.json")

    auto_seller = AutoSeller(config, blacklist, seen, not_resable)

    try:
        await auto_seller.start()
    except:
        return Display.exception(f"Unknown error occurred:\n\n{format_exc()}")

if __name__ == "__main__":
    asyncio.run(main())
