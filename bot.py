import asyncio
import discord
import os
import requests as http_requests
from discord.ext import commands

GUILD_ID = int(os.getenv("DISCORD_GUILD_ID", "0"))
CATEGORY_NAME = "ANOMES-ROOMS"


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

    def sync_create_channel(self, name, is_private, room_code):
        future = asyncio.run_coroutine_threadsafe(self.create_channel(name, is_private, room_code), self.loop)
        try:
            return future.result(timeout=10)
        except Exception as e:
            print(f"[Bot] create_channel error: {e}")
            return None

    async def delete_channel(self, channel_id):
        channel = self.get_channel(channel_id)
        if channel:
            await channel.delete(reason="Anomes room expired due to inactivity")

    def sync_delete_channel(self, channel_id):
        future = asyncio.run_coroutine_threadsafe(self.delete_channel(channel_id), self.loop)
        try:
            future.result(timeout=10)
        except Exception as e:
            print(f"[Bot] delete_channel error: {e}")

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

    def sync_get_webhook_url(self, channel_id):
        future = asyncio.run_coroutine_threadsafe(self.get_webhook_url(channel_id), self.loop)
        try:
            return future.result(timeout=10)
        except Exception as e:
            print(f"[Bot] get_webhook_url error: {e}")
            return None

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

    def sync_get_messages(self, channel_id):
        future = asyncio.run_coroutine_threadsafe(self.get_messages(channel_id), self.loop)
        try:
            return future.result(timeout=10)
        except Exception as e:
            print(f"[Bot] get_messages error: {e}")
            return []

    async def send_webhook(self, webhook_url, content, username):
        payload = {"content": content, "username": username}
        resp = http_requests.post(webhook_url, json=payload)
        return resp.status_code in (200, 204)

    def sync_send_webhook(self, webhook_url, content, username):
        future = asyncio.run_coroutine_threadsafe(self.send_webhook(webhook_url, content, username), self.loop)
        try:
            return future.result(timeout=10)
        except Exception as e:
            print(f"[Bot] send_webhook error: {e}")
            return False
