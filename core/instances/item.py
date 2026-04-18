import asyncio
import aiohttp
from datetime import datetime

from typing import Optional, List, Any, Union

from ..clients import Auth
from ..utils import IgnoreNew
from ..visuals import Display
from .collectible import Collectible

__all__ = ("Item",)


class Item:
    __slots__ = ("_id", "item_id", "_link", "name", "thumbnail",
                 "price", "quantity", "lowest_resale_price",
                 "_creator_id", "creator_name", "_creator_link",
                 "recent_average_price", "has_resales", "latest_sale",
                 "has_sales", "price_to_sell", "auth", "_collectibles",
                 "resales", "sales", "asset_type_name", "_copy_counter")

    def __init__(
        self,
        item_info: dict,
        item_details: dict,
        *,
        thumbnail: Optional[str] = None,
        price_to_sell: Optional[int] = None,
        auth: Optional[Auth] = None,
        asset_type_name: Optional[str] = None
    ) -> None:
        self._id = item_info["assetId"]
        self.item_id = item_info["collectibleItemId"]
        self._link = f"https://www.roblox.com/catalog/{self._id}"
        self.name = item_info["assetName"]
        self.thumbnail = thumbnail
        self.price = item_details.get("price")
        self.quantity = item_details.get("totalQuantity")
        self.lowest_resale_price = item_details["lowestResalePrice"]

        self._creator_id = item_details.get("creatorTargetId")
        self.creator_name = item_details.get("creatorName")
        self._creator_link = f"https://www.roblox.com/groups/{self._creator_id}"

        self.recent_average_price = None
        self.has_resales = None
        self.latest_sale = None
        self.has_sales = None

        self.price_to_sell = price_to_sell
        self.auth = auth

        self._collectibles = {}
        self.resales = []
        self.sales = []
        self.asset_type_name = asset_type_name
        self._copy_counter = 0

    id = IgnoreNew()
    link = IgnoreNew()

    creator_id = IgnoreNew()
    creator_link = IgnoreNew()

    @property
    def collectibles(self) -> List[Collectible]:
        return list(self._collectibles.values())

    def get_collectible(self, serial: int, default: Optional[Any] = None) -> Union[Collectible, Any]:
        return self._collectibles.get(serial, default)

    def remove_collectible(self, serial: int) -> None:
        return self._collectibles.pop(serial)

    @staticmethod
    def __define_status(value: str, state: str, name: str):
        def decorator(_):
            def wrapper(instance: "Item") -> str:
                match getattr(instance, state):
                    case True:
                        return f"{getattr(instance, value):,}"
                    case False:
                        return f"No {name.capitalize()}"
                    case _:
                        return "Failed to Fetch"
            return wrapper
        return decorator

    @__define_status("lowest_resale_price", "has_resales", "resales")
    def define_lowest_resale_price(self, state) -> str:
        ...

    @__define_status("recent_average_price", "has_sales", "sales")
    def define_recent_average_price(self) -> str:
        ...

    @__define_status("latest_sale", "has_sales", "sales")
    def define_latest_sale(self) -> str:
        ...

    def add_collectible(
        self,
        serial: Optional[int] = None,
        on_sale: Optional[bool] = None,
        sale_price: Optional[int] = None,
        item_id: Optional[int] = None,
        instance_id: Optional[str] = None,
        product_id: Optional[str] = None,
    ) -> None:
        col = self.get_collectible(serial)
        if not col and serial is not None:
            new = Collectible(
                serial=serial,
                on_sale=on_sale,
                sale_price=sale_price,
                item_id=(item_id or self.item_id),
                instance_id=instance_id,
                product_id=product_id
            )
            self._collectibles.update({serial: new})
        elif col:
            col.set_values(
                on_sale=on_sale,
                sale_price=sale_price,
                item_id=(item_id or self.item_id),
                instance_id=instance_id,
                product_id=product_id
            )

    @Auth.has_auth
    async def sell_collectibles(
        self,
        price: Optional[int] = None,
        skip_on_sale: bool = False,
        skip_if_cheapest: bool = False,
        verbose: bool = True,
        retries: int = 1
    ) -> Optional[int]:
        await self.fetch_collectibles()
        if not self.collectibles:
            if verbose:
                Display.error(f"No collectibles found for {self.name}. Skipping.")
            return 0

        sold_amount = 0
        price_to_sell = (price or self.price_to_sell)

        for col in self.collectibles:
            tries = 0
            if verbose:
                print(f"[DEBUG] Collectible #{col.serial}: on_sale={col.on_sale}, "
                      f"sale_price={col.sale_price}, item_id={col.item_id}, "
                      f"instance_id={col.instance_id}, product_id={col.product_id}, "
                      f"skip_on_sale={col.skip_on_sale}")

            if col.skip_on_sale:
                if verbose:
                    Display.skipping(f"Skipping #{col.serial} (skip_on_sale=True)")
                continue

            if col.sale_price == price_to_sell:
                if verbose:
                    Display.skipping(f"#{col.serial} already on sale for {price_to_sell} Robux")
                continue

            if col.on_sale:
                if skip_on_sale:
                    if verbose:
                        Display.skipping(f"#{col.serial} already on sale (skip_on_sale=True)")
                    continue
                if skip_if_cheapest and self.lowest_resale_price == col.sale_price:
                    if verbose:
                        Display.skipping(f"#{col.serial} is already cheapest")
                    continue

            if None in (col.item_id, col.instance_id, col.product_id):
                if verbose:
                    Display.error(f"Collectible #{col.serial} missing required IDs – cannot sell")
                continue

            while True:
                if verbose:
                    Display.info(f"Attempting to sell #{col.serial} for {price_to_sell} Robux...")
                response = await col.sell(price_to_sell, self.auth)

                if response is None:
                    if verbose:
                        Display.error(f"No response from sell() for #{col.serial}")
                    break

                match response.status:
                    case 200:
                        if verbose:
                            Display.success(f"Successfully sold #{col.serial} for {price_to_sell} Robux")
                        sold_amount += 1
                        break
                    case 429:
                        if verbose:
                            Display.error("Rate limited! Waiting 30 seconds...")
                        tries += 1
                        await asyncio.sleep(30)
                    case 403:
                        if verbose:
                            Display.error(f"Forbidden – price {price_to_sell} may be below minimum")
                        raise Exception(f"403 Forbidden - price {price_to_sell} too low")
                    case 412:
                        if verbose:
                            Display.error(f"Precondition Failed (412) – cannot sell #{col.serial}. Skipping permanently.")
                        col.skip_on_sale = True
                        break
                    case _:
                        if verbose:
                            Display.error(f"Failed to sell #{col.serial} (status {response.status}): {response.reason}")
                        tries += 1
                        await asyncio.sleep(3)

                if tries > retries:
                    if verbose:
                        Display.error(f"Gave up after {retries} attempts")
                    break

        return sold_amount

    @Auth.has_auth
    async def fetch_sales(self, *,
                          save_sales: Optional[bool] = True,
                          save_rap: Optional[bool] = True,
                          save_latest_sale: Optional[bool] = True) -> None:
        try:
            async with self.auth.get(
                f"apis.roblox.com/marketplace-sales/v1/item/{self.item_id}/resale-data"
            ) as response:
                if response.status != 200:
                    return None
                data = await response.json()
                if save_sales:
                    for price, amount in zip(data["priceDataPoints"], data["volumeDataPoints"]):
                        sale_data = {
                            "price": price["value"],
                            "amount": amount["value"],
                            "date": datetime.strptime(price["date"], "%Y-%m-%dT%H:%M:%SZ")
                        }
                        self.sales.append(sale_data)
                if save_rap:
                    self.recent_average_price = round(data.get("recentAveragePrice", 0))
                if save_latest_sale:
                    if data["priceDataPoints"] and data["priceDataPoints"][0]["value"]:
                        self.latest_sale = data["priceDataPoints"][0]["value"]
                        self.has_sales = True
                    else:
                        self.has_sales = False
        except (asyncio.TimeoutError, aiohttp.ClientError) as e:
            print(f"[WARN] Timeout/error fetching sales for {self.name}: {e}")
            return None

    @Auth.has_auth
    async def fetch_resales(self, *,
                            save_resales: Optional[bool] = True,
                            save_lrp: Optional[bool] = True) -> None:
        try:
            async with self.auth.get(
                f"apis.roblox.com/marketplace-sales/v1/item/{self.item_id}/resellers?limit=99"
            ) as response:
                try:
                    data = (await response.json()).get("data")
                except:
                    return None
                if data is None:
                    return None
                if save_resales:
                    for resale in data:
                        seller = resale["seller"]
                        resale_data = {
                            "lowest_resale_price": resale["price"],
                            "serial": resale["serialNumber"],
                            "seller_id": seller["sellerId"],
                            "seller_name": seller["name"]
                        }
                        self.resales.append(resale_data)
                if save_lrp:
                    if data:
                        self.lowest_resale_price = data[0]["price"]
                        self.has_resales = True
                    else:
                        self.has_resales = False
        except (asyncio.TimeoutError, aiohttp.ClientError) as e:
            print(f"[WARN] Timeout/error fetching resales for {self.name}: {e}")
            return None

    @Auth.has_auth
    async def fetch_collectibles(self) -> None:
        cursor = ""
        while True:
            try:
                async with self.auth.get(
                    f"apis.roblox.com/marketplace-sales/v1/item/{self.item_id}/resellable-instances?"
                    f"cursor={cursor}&ownerType=User&ownerId={self.auth.user_id}&limit=9999999"
                ) as response:
                    if response.status != 200:
                        return None
                    data = await response.json()
                    serials_list = []
                    for instance in data.get("itemInstances"):
                        col_serial = instance["serialNumber"]
                        self.add_collectible(
                            serial=col_serial,
                            on_sale=(True if instance["saleState"] == "OnSale" else False),
                            sale_price=instance.get("price"),
                            item_id=instance["collectibleItemId"],
                            instance_id=instance["collectibleInstanceId"],
                            product_id=instance["collectibleProductId"]
                        )
                        serials_list.append(col_serial)
                    for serial in list(self._collectibles):
                        if serial not in serials_list:
                            self.remove_collectible(serial)
                    cursor = data.get("nextPageCursor")
                    if cursor == data.get("previousPageCursor"):
                        return None
            except (asyncio.TimeoutError, aiohttp.ClientError) as e:
                print(f"[WARN] Timeout fetching collectibles for {self.name}, retrying in 5 seconds...")
                await asyncio.sleep(5)
                continue

    def __len__(self) -> int:
        return len(self.collectibles)
