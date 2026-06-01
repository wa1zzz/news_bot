import os
import logging
import asyncio
from telethon import TelegramClient, events
import db

logger = logging.getLogger(__name__)

# Directory to save downloaded media
MEDIA_DIR = "data/media"
os.makedirs(MEDIA_DIR, exist_ok=True)


def matched_keywords(text: str, keywords: list) -> list:
    """Returns the list of keywords found in the text (case-insensitive substring match).

    An empty keyword list means "no filter configured" -> the post is allowed through
    (returns a sentinel non-empty result so callers treat it as a match).
    """
    if not keywords:
        return ["*"]  # no filter configured -> allow all
    if not text:
        return []
    haystack = text.lower()
    return [kw for kw in keywords if kw and kw in haystack]

class Scraper:
    def __init__(self, api_id: int, api_hash: str, source_channels: list, on_new_post_callback):
        self.client = TelegramClient("user_session", api_id, api_hash)
        self.source_channels = source_channels
        self.on_new_post_callback = on_new_post_callback

    async def start(self):
        logger.info("Starting Scraper Client...")
        await self.client.start()
        logger.info("Scraper Client successfully authenticated and started.")

        # Album cache: grouped_id -> list of messages
        album_cache = {}

        # Register message listener
        @self.client.on(events.NewMessage())
        async def my_event_handler(event):
            try:
                # We only monitor channel posts
                if not event.is_channel:
                    return
                
                channel_id = event.chat_id
                chat = await event.get_chat()
                username = getattr(chat, "username", None)
                
                # Dynamically check if this channel is actively monitored
                if not await db.is_channel_monitored(channel_id, username):
                    return

                # Auto-update channel details in the database so that its ID, username, and title are correctly resolved
                title = getattr(chat, "title", None)
                try:
                    await db.add_monitored_channel(channel_id=channel_id, username=username, title=title)
                except Exception as ex:
                    logger.warning(f"Failed to auto-update monitored channel metadata: {ex}")

                # Load the active keyword filter once for this post
                keywords = await db.get_keywords()

                # Handle media group (album) messages
                grouped_id = event.message.grouped_id
                if grouped_id:
                    if grouped_id not in album_cache:
                        album_cache[grouped_id] = []
                        album_cache[grouped_id].append(event.message)
                        
                        # Wait for other messages in the album to arrive
                        await asyncio.sleep(1.5)
                        
                        messages = album_cache.pop(grouped_id, [])
                        if not messages:
                            return
                        
                        # Sort messages by ID to preserve original order
                        messages.sort(key=lambda m: m.id)
                        
                        first_msg_id = messages[0].id
                        if await db.is_post_processed(channel_id, first_msg_id):
                            return

                        # Extract the album caption first (text lives on one of the messages)
                        text = ""
                        for msg in messages:
                            if msg.message and not text:
                                text = msg.message
                                break

                        # Keyword filter: skip the whole album (before downloading media) if it doesn't match
                        hits = matched_keywords(text, keywords)
                        if not hits:
                            logger.info(
                                f"Album {channel_id}:{first_msg_id} skipped — no keyword match (filtered)."
                            )
                            return

                        media_paths = []
                        media_type = "none"

                        for msg in messages:
                            if msg.media:
                                path = await msg.download_media(file=MEDIA_DIR)
                                if path:
                                    media_paths.append(path)
                                    # Set type based on media files
                                    if hasattr(msg.media, 'document') or hasattr(msg.media, 'video'):
                                        media_type = "video"
                                    elif hasattr(msg.media, 'photo') and media_type != "video":
                                        media_type = "photo"
                                    elif media_type == "none":
                                        media_type = "document"

                        logger.info(f"New raw post captured (Album) from channel {channel_id}, message ID {first_msg_id}, count: {len(media_paths)}, matched: {hits[:5]}.")
                        post_id = await db.add_raw_post(channel_id, first_msg_id, text, media_paths, media_type)
                        asyncio.create_task(self.on_new_post_callback(post_id, text))
                    else:
                        album_cache[grouped_id].append(event.message)
                    return

                # Normal single message flow
                message_id = event.id
                
                # Avoid processing duplicate posts
                if await db.is_post_processed(channel_id, message_id):
                    logger.debug(f"Post {channel_id}:{message_id} already processed. Skipping.")
                    return

                text = event.message.message or ""
                media_paths = []
                media_type = "none"

                # Keyword filter: skip the post (before downloading media) if it doesn't match
                hits = matched_keywords(text, keywords)
                if not hits:
                    logger.info(
                        f"Post {channel_id}:{message_id} skipped — no keyword match (filtered)."
                    )
                    return

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

                logger.info(f"New raw post captured from channel {channel_id}, message ID {message_id}, matched: {hits[:5]}.")
                post_id = await db.add_raw_post(channel_id, message_id, text, media_paths, media_type)

                # Trigger callback (LLM processing and moderation sending)
                asyncio.create_task(self.on_new_post_callback(post_id, text))
            except Exception as e:
                logger.error(f"Error handling new channel message: {e}", exc_info=True)

        # Run until disconnected
        await self.client.run_until_disconnected()
