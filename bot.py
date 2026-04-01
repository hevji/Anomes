import asyncio
import discord
import os
import requests as http_requests
from discord.ext import commands

GUILD_ID = int(os.getenv("DISCORD_GUILD_ID", "0"))
CATEGORY_NAME = "ANOMES-ROOMS"
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")  # must be set in Render environment


class AnomesBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(command_prefix="!", intents=intents)

    async def on_ready(self):
        print(f"[Anomes Bot] Logged in as {self.user}")

    def _get_guild(self):
        return self.get_guild(GUILD_ID)

    async def _get_or_create_category(self, guild):
        for cat in guild.categories:
            if cat.name == CATEGORY_NAME:
                return cat
        return await guild.create_category(CATEGORY_NAME)

    # Channel
    async def create_channel(self, name, is_private, room_code):
        guild = self._get_guild()
        if not guild:
            return None
        category = await self._get_or_create_category(guild)
        overwrites = {guild.default_role: discord.PermissionOverwrite(read_messages=not is_private)}
        channel = await guild.create_text_channel(
            name=f"anomes-{room_code}".lower(),
            category=category,
            topic=f"Anomes Room | {name} | Code: {room_code}",
            overwrites=overwrites,
        )
        await channel.create_webhook(name="anomes-hook")
        return channel.id

    async def delete_channel(self, channel_id):
        channel = self.get_channel(channel_id)
        if channel:
            await channel.delete(reason="Anomes room expired due to inactivity")

    # Webhook
    async def get_webhook_url(self, channel_id):
        channel = self.get_channel(channel_id)
        if not channel:
            return None
        existing = await channel.webhooks()
        if existing:
            return existing[0].url
        webhook = await channel.create_webhook(name="anomes-hook")
        return webhook.url

    # Messages
    async def get_messages(self, channel_id, limit=50):
        channel = self.get_channel(channel_id)
        if not channel:
            return []
        messages = []
        async for msg in channel.history(limit=limit, oldest_first=True):
            messages.append({
                "id": str(msg.id),
                "username": msg.author.display_name,
                "content": msg.content,
                "timestamp": msg.created_at.isoformat(),
            })
        return messages

    async def send_webhook(self, webhook_url, content, username):
        payload = {"content": content, "username": username}
        resp = http_requests.post(webhook_url, json=payload)
        return resp.status_code in (200, 204)


if __name__ == "__main__":
    if not DISCORD_TOKEN or GUILD_ID == 0:
        print("[Error] DISCORD_TOKEN or DISCORD_GUILD_ID not set in environment")
        exit(1)

    bot = AnomesBot()
    bot.run(DISCORD_TOKEN)
