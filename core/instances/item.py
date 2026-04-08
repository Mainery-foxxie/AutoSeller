@Auth.has_auth
async def fetch_sales(self, *, save_sales: Optional[bool] = True, save_rap: Optional[bool] = True, save_latest_sale: Optional[bool] = True) -> None:
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
async def fetch_resales(self, *, save_resales: Optional[bool] = True, save_lrp: Optional[bool] = True) -> None:
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
