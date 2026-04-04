from __future__ import annotations

import sys
import os
import traceback

# ========== DEBUG MODE ==========
DEBUG = True  # Set to False to reduce console spam
# ================================

def debug_print(*args, **kwargs):
    if DEBUG:
        print("[DEBUG]", *args, **kwargs)

__import__("warnings").filterwarnings("ignore")

try:
    import discord
    import aioconsole
    import asyncio
    from random import random
    from rgbprint import Color
    from datetime import datetime
    from traceback import format_exc
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

# ==================== FALLBACK get_current_cap WITH DEBUG ====================
async def get_current_cap(auth):
    """
    Fetch price floor for each asset type.
    Returns dict: {asset_type_name: {"priceFloor": int}}
    With detailed debug output.
    """
    from core.constants import ITEM_TYPES

    endpoints = [
        "https://economy.roblox.com/v1/assets/price-floor",
        "https://economy.roblox.com/v2/assets/price-floor",
        "https://catalog.roblox.com/v1/price-floors"
    ]

    debug_print("Attempting to fetch price floors from endpoints:", endpoints)

    for endpoint in endpoints:
        try:
            debug_print(f"Trying endpoint: {endpoint}")
            async with auth.get(endpoint) as resp:
                debug_print(f"Response status: {resp.status}")
                if resp.status == 200:
                    data = await resp.json()
                    debug_print(f"Raw response data: {data}")
                    
                    # Try to parse common structures
                    if isinstance(data, dict):
                        caps = {}
                        for asset_type_id, price_floor in data.items():
                            for name, type_id in ITEM_TYPES.items():
                                if str(type_id) == str(asset_type_id):
                                    caps[name] = {"priceFloor": price_floor}
                                    break
                        if caps:
                            debug_print(f"Parsed caps from {endpoint}: {caps}")
                            return caps
                        else:
                            debug_print("No matching asset types found, returning zero caps")
                            break
                    else:
                        debug_print("Response is not a dict, returning zero caps")
                        break
                else:
                    debug_print(f"Non-200 status: {resp.status}, response text: {await resp.text()}")
        except Exception as e:
            debug_print(f"Exception on {endpoint}: {e}")
            debug_print(traceback.format_exc())
            continue

    # Fallback: return floor 5 for all asset types
    debug_print("Using fallback caps (price floor = 5 Robux for all types)")
    caps = {}
    for asset_type_name in ITEM_TYPES.values():
        caps[asset_type_name] = {"priceFloor": 5}
    debug_print(f"Fallback caps: {caps}")
    return caps

# Replace the imported function
import core.main_tools
core.main_tools.get_current_cap = get_current_cap
# ============================================================================

class AutoSeller(ConfigLoader):
    __slots__ = ("config", "_items", "auth", "buy_checker", "blacklist",
                 "seen", "not_resable", "current_index", "done",
                 "total_sold", "selling", "loaded_time", "control_panel")

    def __init__(self,
                 config: dict,
                 blacklist: FileSync,
                 seen: FileSync,
                 not_resable: FileSync) -> None:
        debug_print("Initializing AutoSeller")
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

    def get_item(self, _id: int, default: Optional[Any] = None) -> Union[Item, Any]:
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
        easter_egg = random() < 0.3

        await self.rich_presence.update(
            state=f"{self.current_index + 1} out of {len(self.items)}",
            details=f"Selling {self.current.name} limited",
            large_image=self.current.thumbnail if not easter_egg else "https://cdn.discordapp.com/avatars/1284536257958903808/fa4fba77caa6cc68f2972e2ea33e67a5.png?size=4096",
            large_text=f"{self.current.name} limited" if not easter_egg else "Pisun easter egg (3% chance)",
            small_image="https://cdn.discordapp.com/app-assets/1005469189907173486/1025422070600978553.png?size=160",
            small_text="Roblox",
            buttons=[{"url": self.current.link, "label": "Selling Item"},
                     {"url": URL_REPOSITORY, "label": "Use Tool Yourself"}],
            start=int(self.loaded_time.timestamp())
        )

    async def filter_non_resable(self):
        """Check resaleRestriction with multiple API endpoints + debug"""
        if (self.current_index + 2) % 30 or not self.current_index:
            return None

        endpoints = [
            "https://apis.roblox.com/marketplace-items/v1/items/details",
            "https://economy.roblox.com/v2/assets/details",
            "https://catalog.roblox.com/v1/assets/details"
        ]

        item_ids = [i.item_id for i in self.items[self.current_index:30]]
        debug_print(f"Filtering non-resellable for item IDs: {item_ids[:5]}... (total {len(item_ids)})")
        
        data = None
        used_endpoint = None

        for endpoint in endpoints:
            try:
                debug_print(f"Trying endpoint: {endpoint}")
                async with self.auth.post(endpoint, json={"itemIds": item_ids}) as response:
                    debug_print(f"Response status: {response.status}")
                    if response.status == 200:
                        data = await response.json()
                        used_endpoint = endpoint
                        debug_print(f"Success using {endpoint}, response keys: {data.keys() if isinstance(data, dict) else 'list'}")
                        break
                    else:
                        text = await response.text()
                        debug_print(f"Failed {endpoint} (status {response.status}): {text[:200]}")
            except Exception as e:
                debug_print(f"Exception on {endpoint}: {e}")
                debug_print(traceback.format_exc())
                continue

        if data is None:
            debug_print("All endpoints failed, skipping resale restriction filter")
            return

        items_list = data
        if isinstance(data, dict) and "data" in data:
            items_list = data["data"]
            debug_print(f"Extracted items from 'data' field, count: {len(items_list)}")

        restricted = []
        for item_details in items_list:
            item_id = item_details.get("itemTargetId") or item_details.get("assetId")
            if not item_id:
                debug_print(f"Skipping item without ID: {item_details}")
                continue

            if item_details.get("resaleRestriction") == 1:
                debug_print(f"Item {item_id} is non-resellable (resaleRestriction=1)")
                self.not_resable.add(item_id)
                self.remove_item(item_id)
                restricted.append(item_id)

        debug_print(f"Removed {len(restricted)} non-resellable items using {used_endpoint}")

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
        debug_print("AutoSeller.start() called")
        await asyncio.gather(self.auth.fetch_csrf_token(),
                             self.handle_exceptions())

        Display.info("Checking cookie to be valid")
        user_info = await self.auth.fetch_user_info()
        debug_print(f"User info: {user_info}")
        if user_info is None:
            return Display.exception("Invalid cookie provided")

        Display.info("Checking premium owning")
        premium = await self.auth.fetch_premium()
        debug_print(f"Premium status: {premium}")
        if not premium:
            return Display.exception("You dont have premium to sell limiteds")

        await self._load_items()
        self.sort_items("name")

        if self.presence_enabled:
            try:
                await self.rich_presence.connect()
                await self.update_presence()
                debug_print("Rich presence connected")
            except DiscordNotFound as e:
                debug_print(f"Discord not found: {e}")
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
        except LoginFailure as e:
            debug_print(f"Discord login failure: {e}")
            return Display.exception("Invalid discord token provided")
        except Exception as e:
            debug_print(f"Unexpected error in start(): {e}")
            debug_print(traceback.format_exc())
            return Display.exception(f"Unknown error occurred:\n\n{format_exc()}")

    async def start_selling(self):
        debug_print("Starting selling loop")
        for i in range(2):
            tasks = self.fetch_item_info(step_index=i)
            if tasks:
                for task in tasks:
                    if task:
                        await task

        if self.auto_sell:
            debug_print("Auto-sell mode enabled")
            await self._auto_sell_items()
        else:
            debug_print("Manual selling mode")
            await self._manual_selling()

        Tools.clear_console()
        await Display.custom(
            f"Sold [g{self.total_sold}x] items",
            "done", Color(207, 222, 0))

        clear_items = await Display.input(f"Do you want to reset your selling progress? (Y/n): ")

        if clear_items.lower() == "y":
            self.seen.clear()
            Display.success("Cleared your limiteds selling progress")
            Tools.exit_program()

    async def sell_item(self):
        debug_print(f"Selling item: {self.current.name} (ID {self.current.id})")
        await Display.custom(
            f"Selling [g{len(self.current.collectibles)}x] of [g{self.current.name}] items...",
            "selling", Color(255, 153, 0))

        sold_amount = await self.current.sell_collectibles(
            skip_on_sale=self.skip_on_sale,
            skip_if_cheapest=self.skip_if_cheapest,
            verbose=True
        )

        debug_print(f"Sold amount: {sold_amount}")
        if sold_amount is not None:
            self.total_sold += sold_amount

            if self.sale_webhook and sold_amount > 0:
                asyncio.create_task(self.send_sale_webhook(self.current, sold_amount))

        if self.save_progress:
            self.seen.add(self.current.id)

        self.next_item()

    async def _auto_sell_items(self):
        while not self.done:
            await self.sell_item()
            await asyncio.sleep(0.5)

    async def _manual_selling(self):
        while not self.done:
            await self.update_console()
            choice = (await aioconsole.ainput()).strip()
            debug_print(f"User choice: {choice}")

            match choice:
                case "1":
                    if self.selling:
                        Display.error("This item is already being sold")
                        await asyncio.sleep(0.7)
                        continue

                    with self.selling:
                        await self.sell_item()

                        if self.control_panel is not None:
                            asyncio.create_task(self.control_panel.update_message(self.control_panel.make_embed()))
                case "2":
                    new_price = await Display.input(f"Enter the new price you want to sell: ")

                    if not new_price.isdigit():
                        Display.error("Invalid price amount was provided")
                        await asyncio.sleep(0.7)
                        continue
                    elif int(new_price) < 0:
                        Display.error("Price can not be lower than 0")
                        await asyncio.sleep(0.7)
                        continue

                    self.current.price_to_sell = int(new_price)
                    debug_print(f"New price set: {self.current.price_to_sell}")

                    Display.success(f"Successfully set a new price to sell! ([g${self.current.price_to_sell}])")
                case "3":
                    debug_print(f"Blacklisting {self.current.id}")
                    self.blacklist.add(self.current.id)
                    self.next_item()

                    Display.success(f"Successfully added [g{self.current.name} ({self.current.id})] into a blacklist!")
                case "4":
                    if self.save_progress:
                        self.seen.add(self.current.id)

                    self.next_item()
                    Display.skipping(
                        f"Skipped [g{len(self.current.collectibles)}x] collectibles")
                case _:
                    continue

            await asyncio.sleep(0.7)

    async def __fetch_items(self) -> AsyncGenerator:
        Display.info("Loading your inventory")
        user_items = await AssetsLoader(get_user_inventory, ITEM_TYPES.keys()).load(self.auth)
        debug_print(f"Loaded {len(user_items)} user items")
        if not user_items:
            Display.exception("You dont have any limited UGC items")

        item_ids = [str(asset["assetId"]) for asset in user_items]
        debug_print(f"Item IDs sample: {item_ids[:5]}")

        Display.info("Loading items thumbnails")
        items_thumbnails = await AssetsLoader(get_assets_thumbnails, item_ids, 100).load(self.auth)
        debug_print(f"Loaded {len(items_thumbnails)} thumbnails")

        Display.info(f"Found {len(user_items)} items. Checking them...")
        items_details = await AssetsLoader(get_items_details, item_ids, 120).load(self.auth)
        debug_print(f"Loaded details for {len(items_details)} items")

        for item_info in zip(user_items, items_details, items_thumbnails):
            yield item_info

    async def _load_items(self) -> None:
        if self.loaded_time is not None:
            return Display.exception("You have already loaded items")

        Display.info("Getting current limiteds cap")
        items_cap = await get_current_cap(self.auth)
        debug_print(f"items_cap type: {type(items_cap)}, content: {items_cap}")

        if items_cap is None:
            debug_print("items_cap is None, using fallback caps")
            items_cap = {}
            for asset_type_name in ITEM_TYPES.values():
                items_cap[asset_type_name] = {"priceFloor": 5}

        if not isinstance(items_cap, dict):
            debug_print(f"items_cap is not dict: {type(items_cap)}")
            Display.exception(f"Invalid cap data type: {type(items_cap)}. Expected dict.")
            return

        ignored_items = list(self.seen | self.blacklist | self.not_resable)
        debug_print(f"Ignored items count: {len(ignored_items)}")

        async for item, item_details, thumbnail in self.__fetch_items():
            item_id = item["assetId"]

            if (
                item_id in ignored_items
                or item_details["creatorTargetId"] in self.creators_blacklist
            ):
                debug_print(f"Skipping item {item_id} (blacklisted or ignored)")
                continue

            item_obj = self.get_item(item_id)

            if item_obj is None:
                asset_type = ITEM_TYPES.get(item_details["assetType"])
                debug_print(f"Asset type for {item_id}: {asset_type}")
                if asset_type is None:
                    Display.warning(f"Unknown asset type {item_details['assetType']} for item {item_id}, skipping.")
                    continue

                cap_info = items_cap.get(asset_type)
                debug_print(f"Cap info for {asset_type}: {cap_info}")
                if cap_info is None:
                    Display.warning(f"No cap info for {asset_type} (item {item_id}), using price floor 5.")
                    asset_cap = 5
                else:
                    asset_cap = cap_info.get("priceFloor", 5)

                sell_price = define_sale_price(self.under_cut_amount, self.under_cut_type,
                                               asset_cap, item_details["lowestResalePrice"])
                debug_print(f"Sell price for {item_id}: {sell_price} (cap={asset_cap}, lowest={item_details['lowestResalePrice']})")

                item_obj = Item(
                    item, item_details,
                    price_to_sell=sell_price,
                    thumbnail=thumbnail,
                    auth=self.auth
                )
                self.add_item(item_obj)

            item_obj.add_collectible(
                serial=item["serialNumber"],
                item_id=item["collectibleItemId"],
                instance_id=item["collectibleItemInstanceId"]
            )

        debug_print(f"Total items loaded: {len(self.items)}")
        if not self.items:
            Display.error(f"You dont have any limiteds that are not in[g blacklist/] directory")
            clear_items = await Display.input(f"Do you want to reset your selling progress? (Y/n): ")

            if clear_items.lower() == "y":
                self.seen.clear()
                Display.success("Cleared your limiteds selling progress")
                Tools.exit_program()

        if self.keep_serials or self.keep_copy:
            debug_print(f"Applying keep_serials={self.keep_serials}, keep_copy={self.keep_copy}")
            for item in self.items:
                if len(item.collectibles) <= self.keep_copy:
                    self.remove_item(item.id)
                    debug_print(f"Removed item {item.id} due to keep_copy")
                    continue

                for col in item.collectibles:
                    if col.serial > self.keep_serials:
                        col.skip_on_sale = True
                        debug_print(f"Serial {col.serial} marked skip_on_sale")

        if not self.items:
            not_met = []
            if self.keep_copy: not_met.append(f"{self.keep_copy} copies or higher")
            if self.keep_serials: not_met.append(f"{self.keep_serials} serial or higher")
            list_requirements = ", ".join(not_met)
            return Display.exception(f"You dont have any limiteds with {list_requirements}")

        self.loaded_time = datetime.now()
        debug_print(f"Items loaded at {self.loaded_time}")

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
        await Display.custom("[1] - Sell | [2] - Set Price | [3] - Blacklist | [4] - Skip\n> ",
                             "input", BaseColors.gray, end="")
    
    async def send_sale_webhook(self, item: Item, sold_amount: int) -> None:
        embed = discord.Embed(
            color=2469096,
            timestamp=datetime.now(),
            description=f"**Item name: **`{item.name}`\n"
                        f"**Sold amount: **`{sold_amount}`\n"
                        f"**Sold for: **`{item.price_to_sell}`",
            title="A New Item Went on Sale",
            url=item.link
        )
        embed.set_footer(text="Were sold at")
        
        data = {
            "content": self.user_to_ping,
            "embeds": [embed.to_dict()]
        }

        async with ClientSession() as session:
            async with session.post(self.sale_webhook_url, json=data) as response:
                if response.status == 429:
                    await asyncio.sleep(30)
                    await self.send_sale_webhook(item, sold_amount)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        tasks = (
            self.auth.close_session(),
            self.control_panel.message.delete() if self.control_panel else None
        )

        await asyncio.gather(*filter(None, tasks))


async def main() -> None:
    debug_print("Main function started")
    Display.info("Setting up everything...")

    Display.info("Checking for updates")
    try:
        update_needed = await check_for_update(RAW_CODE_URL, VERSION)
        debug_print(f"Update needed: {update_needed}")
        if update_needed:
            await Display.custom(
                "Your code is outdated. Please update it from github",
                "new", Color(163, 133, 0), exit_after=True)
    except Exception as e:
        debug_print(f"Update check failed: {e}")
        debug_print(traceback.format_exc())

    Display.info("Loading config")
    config = load_file("config.json")
    debug_print(f"Config loaded: {list(config.keys())}")

    Display.info("Loading data assets")
    blacklist = FileSync("blacklist/blacklist.json")
    seen = FileSync("blacklist/seen.json")
    not_resable = FileSync("blacklist/not_resable.json")
    debug_print(f"Blacklist size: {len(blacklist)}, Seen: {len(seen)}, NotResable: {len(not_resable)}")

    auto_seller = AutoSeller(config, blacklist, seen, not_resable)

    try:
        await auto_seller.start()
    except Exception as e:
        debug_print(f"Critical error in main: {e}")
        debug_print(traceback.format_exc())
        return Display.exception(f"Unknown error occurred:\n\n{format_exc()}")


if __name__ == "__main__":
    asyncio.run(main())
