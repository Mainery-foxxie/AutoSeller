from traceback import format_exc
import functools
import discord

from typing import List, Callable, Optional

from discord_bot.visuals.embeds import exception_embed

__all__ = ("users_blacklist", "base_command")


def users_blacklist(user_ids: List[int],
                    ignore_empty: Optional[bool] = True,
                    message: Optional[str] = None) -> Callable:
    """
    Decorator to restrict command to users not in the blacklist.
    If ignore_empty is True and the list is empty, no restriction.
    """
    def decorator(func: Callable):
        @functools.wraps(func)
        async def wrapper(ctx: discord.Message, *args, **kwargs):
            # If blacklist is empty and ignore_empty is True, allow everyone
            if ignore_empty and not user_ids:
                return await func(ctx, *args, **kwargs)
            # If user is in blacklist, deny
            if ctx.author.id in user_ids:
                if message:
                    await ctx.reply(message)
                return
            # Otherwise allow
            await func(ctx, *args, **kwargs)
        return wrapper
    return decorator


def base_command(func: Callable):
    @functools.wraps(func)
    async def wrapper(ctx: discord.Message, *args, **kwargs):
        await ctx.defer()
        try:
            await func(ctx, *args, **kwargs)
        except Exception:
            await ctx.reply(embed=exception_embed(format_exc()))
    return wrapper
