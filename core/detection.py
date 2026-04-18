import asyncio
import aiohttp
from typing import List, Iterable, Optional

from .utils import slice_list
from .clients import Auth
from .constants import FAILED_IMAGE_URL


async def get_recent_sales(auth: Auth, *,
                           limit: Optional[int] = 10) -> Optional[List[dict]]:
    async with auth.get(
        f"economy.roblox.com/v2/users/{auth.user_id}/transactions?"
        f"cursor=&limit={limit}&transactionType=Sale&itemPricingType=PaidAndLimited"
    ) as response:
        if response.status != 200:
            return None
        return (await response.json()).get("data")


async def get_users_thumbnails(user_ids: Iterable[str], auth: Auth) -> Optional[List[str]]:
    thumbnails = []
    for chunk in slice_list(list(user_ids), 100):
        try:
            async with auth.get(
                "thumbnails.roblox.com/v1/users/avatar-headshot?"
                f"userIds={','.join(chunk)}&size=50x50&format=Png&isCircular=false"
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    if data.get("data"):
                        thumbnails_with_ids = {str(img["targetId"]): img["imageUrl"] if img["state"] == "Completed" else FAILED_IMAGE_URL for img in data["data"]}
                        for user_id in user_ids:
                            if user_id in thumbnails_with_ids:
                                thumbnails.append(thumbnails_with_ids[user_id])
        except (asyncio.TimeoutError, aiohttp.ClientError) as e:
            print(f"[WARN] Timeout/error fetching thumbnails: {e}")
            continue
    return thumbnails


async def get_assets_thumbnails(asset_ids: Iterable[str], auth: Auth) -> Optional[List[str]]:
    thumbnails = []
    for chunk in slice_list(list(asset_ids), 100):
        try:
            async with auth.get(
                "thumbnails.roblox.com/v1/assets?"
                f"assetIds={','.join(chunk)}&returnPolicy=PlaceHolder&size=50x50&format=Png&isCircular=false"
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    if data.get("data"):
                        thumbnails_with_ids = {str(img["targetId"]): img["imageUrl"] if img["state"] == "Completed" else FAILED_IMAGE_URL for img in data["data"]}
                        for item_id in asset_ids:
                            if item_id in thumbnails_with_ids:
                                thumbnails.append(thumbnails_with_ids[item_id])
        except (asyncio.TimeoutError, aiohttp.ClientError) as e:
            print(f"[WARN] Timeout/error fetching thumbnails: {e}")
            continue
    return thumbnails


async def get_items_details(item_ids: List[int], auth: Auth) -> List[dict]:
    items = []
    for chunk in slice_list(item_ids, 120):
        payload = {"items": [{"itemType": 1, "id": str(_id)} for _id in chunk]}
        try:
            async with auth.post(
                "catalog.roblox.com/v1/catalog/items/details",
                json=payload,
                timeout=30
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    if data.get("data"):
                        items_with_ids = {str(details["id"]): details for details in data["data"]}
                        for item_id in chunk:
                            if str(item_id) in items_with_ids:
                                items.append(items_with_ids[str(item_id)])
        except (asyncio.TimeoutError, aiohttp.ClientError) as e:
            print(f"[WARN] Timeout/error fetching details: {e}")
            continue
    return items


async def get_user_inventory(item_type: int, auth: Auth) -> List[dict]:
    assets = []
    cursor = ""
    while True:
        url = f"inventory.roblox.com/v2/users/{auth.user_id}/inventory/{item_type}?limit=100&cursor={cursor}&sortOrder=Desc"
        try:
            async with auth.get(url, timeout=30) as response:
                if response.status != 200:
                    break
                data = await response.json()
                cursor = data.get("nextPageCursor")
                for asset in data.get("data", []):
                    if asset.get("serialNumber") is not None:
                        assets.append(asset)
                if not cursor:
                    break
        except (asyncio.TimeoutError, aiohttp.ClientError) as e:
            print(f"[WARN] Timeout/error fetching inventory type {item_type}: {e}, retrying...")
            await asyncio.sleep(5)
            continue
    return assets


async def get_current_cap(auth: Auth) -> Optional[dict]:
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
        caps[asset_type_name] = {"priceFloor": 5}
    return caps
