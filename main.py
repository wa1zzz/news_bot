import os
import asyncio
import logging
from dotenv import load_dotenv
import db
from rewriter import get_rewriter
from scraper import Scraper
from bot import ModerationBot

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()

# Global instances
moderation_bot = None
rewriter = None

async def on_new_post_captured(post_id: int, original_text: str):
    """Callback triggered when the scraper intercepts a new post."""
    logger.info(f"Processing rephrase for post ID {post_id}...")
    await db.update_post_status(post_id, "rewriting")
    
    rewritten_text = None
    if original_text.strip():
        try:
            # Rephrase using LLM
            rewritten_text = await rewriter.rewrite(original_text)
            logger.info(f"Rephrase completed for post ID {post_id}.")
            await db.update_post_rewritten_text(post_id, rewritten_text)
        except Exception as e:
            logger.error(f"Failed to rephrase post {post_id}: {e}. Falling back to original text.")
            # Resilient fallback: use original text so the user can edit or approve manually
            rewritten_text = original_text
    else:
        logger.info(f"Post {post_id} contains no text (media only). Skipping LLM rephrase.")
        rewritten_text = ""

    # Send draft to admin moderation chat
    if moderation_bot:
        await moderation_bot.send_draft(post_id)

async def main():
    global moderation_bot, rewriter

    # Setup directories
    os.makedirs("data/media", exist_ok=True)

    # Initialize database
    await db.init_db()

    # Load and validate settings
    api_id_str = os.getenv("TELEGRAM_API_ID")
    api_hash = os.getenv("TELEGRAM_API_HASH")
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    admin_chat_id_str = os.getenv("ADMIN_CHAT_ID")
    target_channel_id = os.getenv("TARGET_CHANNEL_ID")
    source_channels_str = os.getenv("SOURCE_CHANNELS", "")

    if not all([api_id_str, api_hash, bot_token, admin_chat_id_str, target_channel_id, source_channels_str]):
        logger.error("Missing required environment variables in .env! Check .env.example.")
        return

    try:
        api_id = int(api_id_str)
        admin_chat_id = int(admin_chat_id_str)
    except ValueError:
        logger.error("TELEGRAM_API_ID and ADMIN_CHAT_ID must be valid integers!")
        return

    # Parse target channels
    source_channels = []
    for chan in source_channels_str.split(","):
        chan = chan.strip()
        if not chan:
            continue
        if chan.startswith("-"):
            try:
                source_channels.append(int(chan))
            except ValueError:
                source_channels.append(chan)
        else:
            if chan.startswith("@"):
                chan = chan[1:]
            source_channels.append(chan)

    logger.info(f"Monitored source channels: {source_channels}")
    logger.info(f"Target publishing channel: {target_channel_id}")
    logger.info(f"Admin moderation chat ID: {admin_chat_id}")

    # Initialize Rewriter
    try:
        rewriter = get_rewriter()
    except Exception as e:
        logger.error(f"Failed to initialize AI Rewriter: {e}")
        return

    # Initialize Moderation Bot
    moderation_bot = ModerationBot(
        token=bot_token,
        admin_chat_id=admin_chat_id,
        target_channel_id=target_channel_id
    )

    # Initialize Scraper
    scraper = Scraper(
        api_id=api_id,
        api_hash=api_hash,
        source_channels=source_channels,
        on_new_post_callback=on_new_post_captured
    )

    # Start both loops concurrently
    logger.info("Launching Scraper and Moderation Bot concurrently...")
    await asyncio.gather(
        scraper.start(),
        moderation_bot.start()
    )

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot stopped by user.")
    except Exception as e:
        logger.error(f"Critical error on execution: {e}", exc_info=True)
