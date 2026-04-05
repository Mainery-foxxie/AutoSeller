@Auth.has_auth
async def sell_collectibles(
    self,
    price: Optional[int] = None,
    skip_on_sale: bool = False,
    skip_if_cheapest: bool = False,
    verbose: bool = True,
    retries: int = 1
) -> Optional[int]:
    # Force a fresh fetch of collectibles before selling
    await self.fetch_collectibles()

    if not self.collectibles:
        if verbose:
            Display.error(f"No collectibles found for {self.name}. Skipping.")
        return 0

    sold_amount = 0
    price_to_sell = (price or self.price_to_sell)

    for col in self.collectibles:
        tries = 0

        # Debug output for each collectible
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

        # Check for missing required IDs
        if None in (col.item_id, col.instance_id, col.product_id):
            if verbose:
                Display.error(f"Collectible #{col.serial} missing required IDs (item_id={col.item_id}, "
                              f"instance_id={col.instance_id}, product_id={col.product_id}) – cannot sell")
            continue

        while True:
            if verbose:
                Display.info(f"Attempting to sell #{col.serial} for {price_to_sell} Robux...")
            response = await col.sell(price_to_sell, self.auth)

            if response is None:
                if verbose:
                    Display.error(f"No response from sell() for #{col.serial} (maybe skip_on_sale changed?)")
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
                    if response.reason == "Forbidden":
                        if verbose:
                            Display.error("Forbidden – cookie may be invalid or missing permissions")
                        break
                    tries += 1
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
