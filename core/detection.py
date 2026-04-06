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
    for chunk in slice_list(user_ids, 100):
        processed_chunk = ','.join(chunk)
        async with auth.get(
            "thumbnails.roblox.com/v1/users/avatar-headshot?"
            f"userIds={processed_chunk}&size=50x50&format=Png&isCircular=false"
        ) as response:
            data = (await response.json()).get("data")
            if data is None:
                return thumbnails
            thumbnails_with_ids = {str(img["targetId"]): img["imageUrl"] if img["state"] == "Completed" else FAILED_IMAGE_URL for img in data}
            for user_id in user_ids:
                if user_id in thumbnails_with_ids:
                    thumbnails.append(thumbnails_with_ids[user_id])
    return thumbnails


async def get_assets_thumbnails(asset_ids: Iterable[str], auth: Auth) -> Optional[List[str]]:
    thumbnails = []
    for chunk in slice_list(asset_ids, 100):
        async with auth.get(
            "thumbnails.roblox.com/v1/assets?"
            f"assetIds={','.join(chunk)}&returnPolicy=PlaceHolder&size=50x50&format=Png&isCircular=false"
        ) as response:
            data = (await response.json()).get("data")
            if data is None:
                return thumbnails
            thumbnails_with_ids = {str(img["targetId"]): img["imageUrl"] if img["state"] == "Completed" else FAILED_IMAGE_URL for img in data}
            for item_id in asset_ids:
                if item_id in thumbnails_with_ids:
                    thumbnails.append(thumbnails_with_ids[item_id])
    return thumbnails


async def get_items_details(item_ids: List[int], auth: Auth) -> List[dict]:
    items = []
    for chunk in slice_list(item_ids, 120):
        payload = {"items": [{"itemType": 1, "id": str(_id)} for _id in chunk]}
        async with auth.post(
            "catalog.roblox.com/v1/catalog/items/details",
            json=payload
        ) as response:
            data = (await response.json()).get("data")
            if data is None:
                return items
            items_with_ids = {str(details["id"]): details for details in data}
            for item_id in item_ids:
                if item_id in items_with_ids:
                    items.append(items_with_ids[item_id])
    return items


async def get_user_inventory(item_type: int, auth: Auth) -> List[dict]:
    assets = []
    cursor = ""
    while True:
        async with auth.get(
            f"inventory.roblox.com/v2/users/{auth.user_id}/inventory/{item_type}?"
            f"limit=100&cursor={cursor}&sortOrder=Desc"
        ) as response:
            if response.status != 200:
                return assets
            data = await response.json()
            cursor = data.get("nextPageCursor")
            assets.extend([asset for asset in data.get("data") if asset.get("serialNumber")])
            if not cursor:
                return assets


# ==================== FIXED get_current_cap WITH MULTI‑API FALLBACK ====================
async def get_current_cap(auth: Auth) -> Optional[dict]:
    """
    Fetch price floor for each asset type using multiple endpoints.
    Returns dict: {asset_type_name: {"priceFloor": int}}
    Uses fallback chain to ensure a value is always returned.
    """
    from core.constants import ITEM_TYPES

    caps = {}

    # Define fallback endpoints for each asset type (per‑type API is most reliable)
    for asset_type_name, asset_type_id in ITEM_TYPES.items():
        floor = None

        # 1. Official price-floor endpoint
        url = f"https://economy.roblox.com/v1/assets/price-floor?assetTypeId={asset_type_id}"
        try:
            async with auth.get(url) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if isinstance(data, (int, float)) and data > 0:
                        floor = int(data)
                    elif isinstance(data, dict) and data.get("priceFloor", 0) > 0:
                        floor = data["priceFloor"]
        except:
            pass

        # 2. If failed, try to infer from your own on‑sale items (already implemented in main.py, but we'll repeat)
        if floor is None:
            try:
                user_id = auth.user_id
                url2 = f"https://economy.roblox.com/v1/users/{user_id}/resellable-items?limit=100"
                async with auth.get(url2) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        for item in data.get("data", []):
                            if item.get("assetType") == asset_type_id:
                                price = item.get("price", 0)
                                if price > 0:
                                    floor = price
                                    break
            except:
                pass

        # 3. Fallback to default floors from your provided data
        if floor is None:
            default_floors = {
                "Emote": 50, "Hat": 90, "HairAccessory": 60, "FaceAccessory": 90,
                "NeckAccessory": 50, "ShoulderAccessory": 50, "FrontAccessory": 50,
                "BackAccessory": 135, "WaistAccessory": 60, "TShirtAccessory": 61,
                "ShirtAccessory": 55, "PantsAccessory": 65, "JacketAccessory": 60,
                "SweaterAccessory": 61, "ShortsAccessory": 55, "DressSkirtAccessory": 55,
            }
            floor = default_floors.get(asset_type_name, 5)

        caps[asset_type_name] = {"priceFloor": floor}

    return caps
# =======================================================================================
