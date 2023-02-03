import discord

import breadcord
from breadcord.module import ModuleCog


class Breadboard(ModuleCog):
    class OriginalMessageButton(discord.ui.View):
        def __init__(self, url: str):
            super().__init__()
            self.add_item(discord.ui.Button(label='Original Message', url=url, style=discord.ButtonStyle.link))

    @ModuleCog.listener()
    async def on_raw_reaction_add(self, reaction: discord.RawReactionActionEvent):
        # Put before anything else so that the message is fetched as early as possible
        # THus, the bot is less likely to error due to the message being deleted before it could be fetched
        starred_message = await (
            await (await self.bot.fetch_guild(reaction.guild_id)).fetch_channel(reaction.channel_id)
        ).fetch_message(reaction.message_id)

        settings = self.bot.settings.Breadboard
        if str(reaction.emoji) not in settings.accepted_emojis.value or starred_message.webhook_id is not None:
            return

        # TODO: Remove this and allow for specifying a starboard for multiple guilds once andrew fixes his framework
        if reaction.guild_id != settings.starboard_guild.value:
            return

        starboard_guild = await self.bot.fetch_guild(settings.starboard_guild.value)
        starboard_channel = await starboard_guild.fetch_channel(settings.starboard_channel.value)

        if not (starboard_webhook := list(filter(lambda x: x.name == "Starboard", await starboard_channel.webhooks()))):
            starboard_webhook = [await starboard_channel.create_webhook(name="Starboard")]
        starboard_webhook = starboard_webhook[0]

        await starboard_webhook.send(
            content=starred_message.content,
            avatar_url=starred_message.author.avatar.url,
            username=starred_message.author.display_name,
            files=[await attachment.to_file() for attachment in starred_message.attachments],
            embeds=starred_message.embeds,
            allowed_mentions=discord.AllowedMentions.none(),
            view=self.OriginalMessageButton(starred_message.jump_url),
        )


async def setup(bot: breadcord.Bot):
    await bot.add_cog(Breadboard())
