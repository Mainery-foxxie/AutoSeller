from __future__ import annotations

import traceback

import discord
from discord.ext.commands import Bot, BadArgument, CommandNotFound, MissingRequiredArgument
from discord import app_commands, Intents

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from main import AutoSeller

from discord_bot.visuals.view import ControlPanel
from discord_bot.visuals.embeds import exception_embed
from discord_bot.utils.decorators import users_blacklist, base_command


async def start(auto_seller: AutoSeller) -> None:
    bot = Bot(command_prefix=auto_seller.bot_prefix, intents=Intents.all())

    @bot.event
    async def on_ready():
        await bot.tree.sync()
        print(f"Discord bot logged in as {bot.user}")

    @bot.event
    async def on_message_delete(message: discord.Message):
        # Check if the deleted message is the control panel message
        if auto_seller.control_panel and message.id == auto_seller.control_panel.message.id:
            auto_seller.control_panel = None

    @bot.hybrid_command(name="start", description="Starts selling limiteds")
    @app_commands.describe(channel="The channel to send a control panel to")
    @users_blacklist(auto_seller.owners_list, message="You don't have permission to use this command!")
    @base_command
    async def start_command(ctx: discord.Message, channel: discord.TextChannel = None):
        channel = channel or ctx.channel

        if auto_seller.auto_sell:
            return await ctx.reply(content="You can not run this command when you have auto sell enabled")
        elif auto_seller.control_panel is not None:
            return await ctx.reply(content=f"You already have one control panel running! "
                                           f"({auto_seller.control_panel.message.jump_url})")

        await ControlPanel(auto_seller, channel, ctx).start()
        return await ctx.reply(f"Successfully created a control panel {auto_seller.control_panel.message.jump_url}")

    @bot.event
    async def on_command_error(ctx: discord.Message, exception: Exception):
        if type(exception) in (BadArgument, CommandNotFound, MissingRequiredArgument):
            return None
        await ctx.reply(embed=exception_embed(traceback.format_exc()))

    await bot.start(auto_seller.bot_token)
