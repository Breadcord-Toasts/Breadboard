import contextlib
import sqlite3

import discord

import breadcord
from breadcord.config import SettingsGroup
from breadcord.module import ModuleCog


class OriginalMessageButton(discord.ui.View):
    def __init__(
        self,
        *,
        original_message_url: str,
        star_count: int,
        star_emoji: discord.PartialEmoji | discord.Emoji | str = "⭐",
    ) -> None:
        super().__init__()
        self.add_item(
            discord.ui.Button(
                label=f"{star_count} | Original Message",
                url=original_message_url,
                style=discord.ButtonStyle.link,
                emoji=star_emoji,
            )
        )


class Breadboard(ModuleCog):
    def __init__(self, module_id: str) -> None:
        super().__init__(module_id)
        interaction.followup.send(
        self.connection = sqlite3.connect(self.module.storage_path / "starred_messages.db")
        self.cursor = self.connection.cursor()
        self.cursor.execute(
            "CREATE TABLE IF NOT EXISTS starred_messages ("
            "   original_id INTEGER PRIMARY KEY NOT NULL UNIQUE,"
            "   starboard_message_id INTEGER NOT NULL UNIQUE,"
            "   star_count INTEGER NOT NULL"
            ")"
        )
        self.connection.commit()

    async def fetch(
        self, *, channel: discord.abc.GuildChannel | int, message: int | discord.Message | None = None
    ) -> discord.abc.GuildChannel | discord.Message | discord.WebhookMessage:
        fetched_channel = (
            channel if isinstance(channel, discord.abc.GuildChannel) else await self.bot.fetch_channel(channel)
        )
        if message is not None:
            if not isinstance(fetched_channel, discord.abc.Messageable):
                raise TypeError(
                    f"The supplied channel of type {fetched_channel.__class__.__name__} can't have messages."
                )
            return await fetched_channel.fetch_message(message.id if isinstance(message, discord.Message) else message)
        return fetched_channel

    def filter_reactions(self, reactions: list[discord.Reaction]) -> list[discord.Reaction]:
        def is_accepted(reaction: discord.Reaction) -> bool:
            return str(reaction.emoji) in self.settings.accepted_emojis.value

        def get_count(reaction: discord.Reaction) -> int:
            return reaction.count

        return sorted(filter(is_accepted, reactions), key=get_count, reverse=True)

    @staticmethod
    async def unique_reactions(reactions: list[discord.Reaction], author_id: int) -> int:
        reactions_users = set()
        for reaction in reactions:
            async for user in reaction.users():
                if user.id != author_id:
                    reactions_users.add(user)

        return len(reactions_users)

    def get_required_reactions(self, channel_id: int) -> int:
        requirement = self.settings.required_stars.value
        special_channels: SettingsGroup = self.settings.special_channel_requirements

        if str(channel_id) in special_channels.keys():
            requirement = special_channels.get(str(channel_id)).value

        return requirement

    async def create_starboard_message(
        self, starred_message: discord.Message, webhook: discord.Webhook, button: discord.ui.View, star_count: int
    ) -> None:
        avatar = starred_message.author.avatar

        starboard_message = await webhook.send(
            allowed_mentions=discord.AllowedMentions.none(),
            avatar_url=avatar.url if avatar else None,
            content=starred_message.content,
            embeds=starred_message.embeds,
            files=[await attachment.to_file() for attachment in starred_message.attachments],
            username=starred_message.author.display_name,
            view=button,
            wait=True,
        )
        try:
            self.cursor.execute(
                "INSERT INTO starred_messages VALUES (?, ?, ?)",
                (starred_message.id, starboard_message.id, star_count),
            )
            self.connection.commit()
        except sqlite3.IntegrityError:
            # How did we get here?
            await starboard_message.delete()

    async def delete_starboard_message(
        self, starred_message_id: int, starboard_webhook: discord.Webhook, starboard_message_id: int
    ) -> None:
        with contextlib.suppress(discord.NotFound):
            await starboard_webhook.delete_message(starboard_message_id)
        self.cursor.execute("DELETE FROM starred_messages WHERE original_id = ?", (starred_message_id,))
        self.connection.commit()

    async def update_starboard_message_button(
        self,
        starred_message_id: int,
        starboard_message_id: int,
        webhook: discord.Webhook,
        button: discord.ui.View,
        star_count: int,
    ) -> None:
        try:
            starboard_message = await webhook.fetch_message(starboard_message_id)
        except discord.NotFound:
            self.cursor.execute("DELETE FROM starred_messages WHERE original_id = ?", (starred_message_id,))
            self.connection.commit()
            return

        await starboard_message.edit(view=button)
        self.cursor.execute("UPDATE starred_messages SET star_count = ?", (star_count,))
        self.connection.commit()

    async def on_reaction_update(self, reaction: discord.RawReactionActionEvent) -> None:
        try:
            # Put before anything else so that the message is fetched as early as possible
            # Thus, the bot is less likely to error due to the message being deleted before it could be fetched
            starred_message = await self.fetch(channel=reaction.channel_id, message=reaction.message_id)
        except discord.errors.NotFound:
            return

        if str(reaction.guild_id) not in self.settings.starboard_channels.keys():
            return

        starboard_channel_id: int = self.settings.starboard_channels.get(str(reaction.guild_id)).value
        starboard_channel = await self.bot.fetch_channel(starboard_channel_id)

        star_reactions = self.filter_reactions(starred_message.reactions)
        unique_reactions = await self.unique_reactions(star_reactions, starred_message.author.id)
        required_reactions = self.get_required_reactions(starred_message.channel.id)

        if required_reactions == -1:
            return

        try:
            starboard_webhook = discord.utils.find(lambda w: w.name == "Starboard", await starboard_channel.webhooks())
        except discord.Forbidden:
            return self.logger.warn(
                f"Bot doesn't have permissions to manage webhooks in the specified starboard channel. "
                f"Channel {self.settings.starboard_channel} in guild {self.settings.starboard_guild}"
            )
        if not starboard_webhook:
            starboard_webhook = await starboard_channel.create_webhook(name="Starboard")

        # Don't repost starboard messages
        if starred_message.webhook_id == starboard_webhook.id:
            return

        sql_response = self.cursor.execute(
            "SELECT starboard_message_id, star_count FROM starred_messages WHERE original_id = ?",
            (starred_message.id,),
        ).fetchone()

        if unique_reactions >= required_reactions:
            button = OriginalMessageButton(
                original_message_url=starred_message.jump_url,
                star_count=unique_reactions,
                star_emoji=star_reactions[0].emoji, # Index 0 is the most reacted with emoji
            )
            if sql_response is None: # Sufficient reactions and doesn't exist
                await self.create_starboard_message(starred_message, starboard_webhook, button, unique_reactions)
            else: # Sufficient reactions but does exist
                starboard_message_id, old_star_count = sql_response
                if old_star_count != unique_reactions:
                    await self.update_starboard_message_button(
                        starred_message.id, starboard_message_id, starboard_webhook, button, unique_reactions
                    )
        elif sql_response is not None: # Not enough reactions and does exist
            starboard_message_id, old_star_count = sql_response
            await self.delete_starboard_message(starred_message.id, starboard_webhook, starboard_message_id)


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
    await bot.add_cog(Breadboard("breadboard"))
