import sqlite3
import contextlib

import discord

import breadcord
from breadcord.module import ModuleCog


class OriginalMessageButton(discord.ui.View):
    def __init__(self, url: str, star_count: int):
        super().__init__()
        self.add_item(
            discord.ui.Button(
                label=f"{star_count} | Original Message", url=url, style=discord.ButtonStyle.link, emoji="⭐"
            )
        )


class Breadboard(ModuleCog):
    def __init__(self, name: str | None = None) -> None:
        super().__init__(name)
        self.module_settings = self.bot.settings.Breadboard
        self.connection = sqlite3.connect(self.module.storage_path / "starred_messages.db")
        self.cursor = self.connection.cursor()
        self.cursor.execute(
            "CREATE TABLE IF NOT EXISTS starred_messages ("
            "   original_id INTEGER PRIMARY KEY NOT NULL UNIQUE,"
            "   starboard_message_id INTEGER NOT NULL UNIQUE,"
            "   star_count INTEGER NOT NULL"
            ")"
        )

    async def _fetch(
        self, *, guild: discord.Guild | int = None, channel: discord.abc.GuildChannel | int, message: int = None
    ) -> discord.abc.GuildChannel | discord.Message | discord.WebhookMessage:
        if not isinstance(channel, discord.abc.GuildChannel):
            fetched_guild = await self.bot.fetch_guild(guild.id if isinstance(guild, discord.Guild) else guild)
            fetched_channel = await fetched_guild.fetch_channel(channel)
        else:
            fetched_channel = channel

        if message is not None:
            return await fetched_channel.fetch_message(message.id if isinstance(message, discord.Message) else message)
        return fetched_channel

    async def create_message(self, starred_message: discord.Message, webhook: discord.Webhook, star_count: int) -> None:
        sent_message = await webhook.send(
            allowed_mentions=discord.AllowedMentions.none(),
            avatar_url=starred_message.author.avatar.url,
            content=starred_message.content,
            embeds=starred_message.embeds,
            files=[await attachment.to_file() for attachment in starred_message.attachments],
            username=starred_message.author.display_name,
            view=OriginalMessageButton(starred_message.jump_url, star_count),
            wait=True,
        )

        self.cursor.execute(
            "INSERT INTO starred_messages VALUES (?, ?, ?)",
            (starred_message.id, sent_message.id, starred_message.guild.id),
        )
        self.connection.commit()

    async def update_message(
        self,
        starred_message: discord.Message,
        starboard_message_id: int,
        webhook: discord.Webhook,
        star_count: int,
    ) -> None:
        if star_count >= self.module_settings.required_stars.value:
            await webhook.edit_message(
                starboard_message_id, view=OriginalMessageButton(starred_message.jump_url, star_count)
            )
            self.cursor.execute("UPDATE starred_messages SET star_count = ?", (star_count,))
            self.connection.commit()
        else:
            with contextlib.suppress(discord.NotFound):
                await webhook.delete_message(starboard_message_id)
            self.cursor.execute("DELETE FROM starred_messages WHERE original_id = ?", (starred_message.id,))

    async def on_reaction_update(self, reaction: discord.RawReactionActionEvent) -> None:
        # Put before anything else so that the message is fetched as early as possible
        # Thus, the bot is less likely to error due to the message being deleted before it could be fetched
        starred_message = await self._fetch(
            guild=reaction.guild_id, channel=reaction.channel_id, message=reaction.message_id
        )

        # TODO: Remove this and allow for specifying a starboard for multiple guilds once andrew fixes his framework
        if reaction.guild_id != self.module_settings.starboard_guild.value:
            return

        # This counts the number of star reactions, counting each unique user only once
        star_reactions = []
        for star_reaction in filter(
            lambda r: r.emoji in self.module_settings.accepted_emojis.value, starred_message.reactions
        ):
            star_reactions.extend([user async for user in star_reaction.users()])
        star_count = len(dict.fromkeys(star_reactions))

        starboard_channel = await self._fetch(
            guild=self.module_settings.starboard_guild.value, channel=self.module_settings.starboard_channel.value
        )

        try:
            starboard_webhooks = await starboard_channel.webhooks()
        except discord.Forbidden:
            self.logger.warn(
                f"Bot doesn't have permissions to manage webhooks in the specified starboard channel. "
                f"Channel {self.module_settings.starboard_channel} in guild {self.module_settings.starboard_guild}"
            )
            return

        if not (starboard_webhook := list(filter(lambda x: x.name == "Starboard", starboard_webhooks))):
            starboard_webhook = [await starboard_channel.create_webhook(name="Starboard")]
        starboard_webhook = starboard_webhook[0]

        if starred_message.webhook_id == starboard_webhook.id:
            return

        response = self.cursor.execute(
            "SELECT starboard_message_id, star_count FROM starred_messages WHERE original_id = ?",
            (starred_message.id,),
        ).fetchone()

        if response is None and star_count >= self.module_settings.required_stars.value:
            await self.create_message(starred_message, starboard_webhook, star_count)
        elif response is not None:
            if response[1] == star_count:
                return
            else:
                await self.update_message(starred_message, response[0], starboard_webhook, star_count)

    @ModuleCog.listener()
    async def on_raw_reaction_add(self, reaction: discord.RawReactionActionEvent) -> None:
        await self.on_reaction_update(reaction)

    @ModuleCog.listener()
    async def on_raw_reaction_remove(self, reaction: discord.RawReactionActionEvent) -> None:
        await self.on_reaction_update(reaction)

    @ModuleCog.listener()
    async def on_raw_reaction_clear(self, reaction: discord.RawReactionActionEvent) -> None:
        await self.on_reaction_update(reaction)

    @ModuleCog.listener()
    async def on_reaction_clear_emoji(self, reaction: discord.RawReactionActionEvent) -> None:
        await self.on_reaction_update(reaction)


async def setup(bot: breadcord.Bot):
    await bot.add_cog(Breadboard())
