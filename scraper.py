import os
import logging
import asyncio
from telethon import TelegramClient, events
import db

logger = logging.getLogger(__name__)

# Directory to save downloaded media
MEDIA_DIR = "data/media"
os.makedirs(MEDIA_DIR, exist_ok=True)

class Scraper:
    def __init__(self, api_id: int, api_hash: str, source_channels: list, on_new_post_callback):
        self.client = TelegramClient("user_session", api_id, api_hash)
        self.source_channels = source_channels
        self.on_new_post_callback = on_new_post_callback

    async def start(self):
        logger.info("Starting Scraper Client...")
        await self.client.start()
        logger.info("Scraper Client successfully authenticated and started.")

        # Register message listener
        @self.client.on(events.NewMessage(chats=self.source_channels))
        async def my_event_handler(event):
            try:
                # Avoid processing empty or duplicate posts
                channel_id = event.chat_id
                message_id = event.id
                
                if await db.is_post_processed(channel_id, message_id):
                    logger.debug(f"Post {channel_id}:{message_id} already processed. Skipping.")
                    return

                text = event.message.message or ""
                media_paths = []
                media_type = "none"

                if event.message.media:
                    logger.info(f"Downloading media for post {message_id}...")
                    # Download media file
                    path = await event.download_media(file=MEDIA_DIR)
                    if path:
                        media_paths.append(path)
                        # Detect type
                        if hasattr(event.message.media, 'photo'):
                            media_type = "photo"
                        elif hasattr(event.message.media, 'document') or hasattr(event.message.media, 'video'):
                            media_type = "video"
                        else:
                            media_type = "document"

                logger.info(f"New raw post captured from channel {channel_id}, message ID {message_id}.")
                post_id = await db.add_raw_post(channel_id, message_id, text, media_paths, media_type)
                
                # Trigger callback (LLM processing and moderation sending)
                asyncio.create_task(self.on_new_post_callback(post_id, text))
            except Exception as e:
                logger.error(f"Error handling new channel message: {e}", exc_info=True)

        # Run until disconnected
        await self.client.run_until_disconnected()
