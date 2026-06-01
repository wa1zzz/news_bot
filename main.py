import os
import asyncio
import logging
import re
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

# Default keyword filter (theme: VPN / blocking / censorship / blocking tech).
# Seeded into the DB on first run only; afterwards manage live via /addword and /delword.
DEFAULT_KEYWORDS = [
    # ВПН и обход блокировок
    "vpn", "впн", "обход блокиров", "обойти блокиров", "прокси", "proxy",
    "shadowsocks", "vless", "vmess", "outline", "wireguard", "openvpn",
    "tor", "тор", "amnezia", "амнезия", "xray", "туннел", "обход dpi", "антицензур",
    # Блокировки / РКН
    "роскомнадзор", "ркн", "блокировк", "заблокир", "разблокир", "замедлен",
    "ограничение доступа", "ограничили доступ", "реестр запрещ", "суверенный интернет",
    "чёрный список", "черный список", "белый список", "недоступ", "перестал работать",
    "сбой доступа", "тспу",
    # Мессенджеры
    "telegram", "телеграм", "whatsapp", "ватсап", "signal", "сигнал",
    "discord", "дискорд", "viber", "вайбер", "блокировка звонков", "ограничение звонков",
    # Цензура и законы
    "цензур", "запрет", "закон об интернете", "законопроект", "госдума", "минцифры",
    "штраф за vpn", "реклама vpn", "иноагент", "фильтрация трафика", "маркировка",
    # Технологии
    "dpi", "dns", "doh", "dot", "ssl", "tls", "шифрован", "ip-адрес",
    "протокол", "маршрутизац", "провайдер", "трафик",
]

def strip_source_attribution(text: str, source_username: str = None) -> str:
    """Removes source mentions, Telegram channel links, usernames, and subscribe calls."""
    if not text:
        return ""
    
    # 1. Remove specific source channel username if provided
    if source_username:
        username_clean = source_username.lstrip('@')
        patterns = [
            r'(?i)https?://t\.me/' + re.escape(username_clean) + r'\b',
            r'(?i)t\.me/' + re.escape(username_clean) + r'\b',
            r'@' + re.escape(username_clean) + r'\b',
        ]
        for pattern in patterns:
            text = re.sub(pattern, '', text)
            
    # 2. General Telegram links pointing to channels/posts
    text = re.sub(r'(?i)https?://t\.me/[a-zA-Z0-9_+]{5,}(/\d+)?', '', text)
    text = re.sub(r'(?i)\bt\.me/[a-zA-Z0-9_+]{5,}(/\d+)?', '', text)
    
    # 3. Common Russian attribution / call-to-action phrases
    attribution_patterns = [
        r'(?i)Источник:\s*@[a-zA-Z0-9_+]+',
        r'(?i)Источник:\s*https?://\S+',
        r'(?i)Источник:\s*t\.me/\S+',
        r'(?i)📢\s*Источник:.*$',
        r'(?i)👉\s*Подписаться.*$',
        r'(?i)👉\s*Подпишись.*$',
        r'(?i)Подписаться на канал.*$',
        r'(?i)Подписывайтесь на\s*@[a-zA-Z0-9_+]+',
        r'(?i)Подписывайтесь на канал.*$',
        r'(?i)Подпишись на\s*@[a-zA-Z0-9_+]+',
        r'(?i)Подпишись на канал.*$',
        r'(?i)Читать далее.*$',
        r'(?i)Читать в источнике.*$',
        r'(?i)Подробнее в источнике.*$',
        r'(?i)Подробнее на.*$',
    ]
    
    for pattern in attribution_patterns:
        text = re.sub(pattern, '', text, flags=re.MULTILINE)
        
    # Clean up excess newlines and trailing/leading space
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()

async def on_new_post_captured(post_id: int, original_text: str):
    """Callback triggered when the scraper intercepts a new post."""
    logger.info(f"Processing rephrase for post ID {post_id}...")
    await db.update_post_status(post_id, "rewriting")
    
    # Dynamic source channel username lookup
    source_username = None
    post = await db.get_post(post_id)
    if post:
        source_channel = await db.get_monitored_channel_by_id(post["source_channel_id"])
        if source_channel:
            source_username = source_channel["username"]
            
    # Pre-clean the text to strip source attributions
    cleaned_text = strip_source_attribution(original_text, source_username)
    
    rewritten_text = None
    if cleaned_text.strip():
        try:
            # Rephrase using LLM
            rewritten_text = await rewriter.rewrite(cleaned_text)
            logger.info(f"Rephrase completed for post ID {post_id}.")
        except Exception as e:
            logger.error(f"Failed to rephrase post {post_id}: {e}. Falling back to cleaned text.")
            # Resilient fallback: use clean text so the user can edit or approve manually
            rewritten_text = cleaned_text
    else:
        logger.info(f"Post {post_id} contains no text (media only or only signatures).")
        rewritten_text = ""

    # Always write the clean rewritten text (or fallback) to the database
    await db.update_post_rewritten_text(post_id, rewritten_text)

    # Send draft to admin moderation chat
    if moderation_bot:
        await moderation_bot.send_draft(post_id)

async def main():
    global moderation_bot, rewriter

    # Setup directories
    os.makedirs("data/media", exist_ok=True)

    # Initialize database
    await db.init_db()

    # Seed default keyword filter on first run (no-op if keywords already exist)
    try:
        seeded = await db.seed_default_keywords(DEFAULT_KEYWORDS)
        if seeded:
            logger.info(f"Seeded {seeded} default keywords for the post filter.")
    except Exception as e:
        logger.error(f"Error seeding default keywords: {e}")

    # Load and validate settings
    api_id_str = os.getenv("TELEGRAM_API_ID")
    api_hash = os.getenv("TELEGRAM_API_HASH")
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    admin_chat_id_str = os.getenv("ADMIN_CHAT_ID")
    admin_chat_ids_str = os.getenv("ADMIN_CHAT_IDS", "")
    target_channel_id = os.getenv("TARGET_CHANNEL_ID")
    source_channels_str = os.getenv("SOURCE_CHANNELS", "")

    if not all([api_id_str, api_hash, bot_token, target_channel_id, source_channels_str]) or not (admin_chat_id_str or admin_chat_ids_str):
        logger.error("Missing required environment variables in .env! Check .env.example.")
        return

    try:
        api_id = int(api_id_str)
    except ValueError:
        logger.error("TELEGRAM_API_ID must be a valid integer!")
        return

    # Parse and validate admin IDs
    admin_chat_ids = set()
    if admin_chat_ids_str:
        for idx in admin_chat_ids_str.split(","):
            idx = idx.strip()
            if idx:
                try:
                    admin_chat_ids.add(int(idx))
                except ValueError:
                    logger.warning(f"Invalid admin ID in ADMIN_CHAT_IDS: {idx}")
    elif admin_chat_id_str:
        try:
            admin_chat_ids.add(int(admin_chat_id_str))
        except ValueError:
            logger.error("ADMIN_CHAT_ID must be a valid integer!")
            return

    if not admin_chat_ids:
        logger.error("No valid admin chat IDs configured in .env!")
        return

    # Seed monitored channels table if empty
    try:
        existing_channels = await db.get_monitored_channels()
        if not existing_channels and source_channels_str:
            logger.info("Monitored channels table is empty. Seeding from SOURCE_CHANNELS...")
            for chan in source_channels_str.split(","):
                chan = chan.strip()
                if not chan:
                    continue
                if chan.startswith("-"):
                    try:
                        chan_id = int(chan)
                        await db.add_monitored_channel(channel_id=chan_id, username=None, title=f"Seeded Channel {chan_id}")
                    except ValueError:
                        await db.add_monitored_channel(channel_id=None, username=chan, title=f"Seeded Channel {chan}")
                else:
                    username = chan[1:] if chan.startswith("@") else chan
                    await db.add_monitored_channel(channel_id=None, username=username, title=f"Seeded {chan}")
            logger.info("Seeding completed.")
    except Exception as e:
        logger.error(f"Error seeding database: {e}")

    logger.info(f"Target publishing channel: {target_channel_id}")
    logger.info(f"Admin moderation chat IDs: {list(admin_chat_ids)}")

    # Initialize Rewriter
    try:
        rewriter = get_rewriter()
    except Exception as e:
        logger.error(f"Failed to initialize AI Rewriter: {e}")
        return

    # Initialize Scraper first so we can pass it to Moderation Bot
    scraper = Scraper(
        api_id=api_id,
        api_hash=api_hash,
        source_channels=[], # We now filter dynamically via DB, so source_channels arg is ignored in scraper
        on_new_post_callback=on_new_post_captured
    )

    # Initialize Moderation Bot with Scraper instance and list of admin IDs
    moderation_bot = ModerationBot(
        token=bot_token,
        admin_chat_ids=list(admin_chat_ids),
        target_channel_id=target_channel_id,
        scraper=scraper,
        on_new_post_callback=on_new_post_captured
    )

    # Authenticate Scraper userbot client first to avoid blocking event loop issues
    logger.info("Starting Scraper authentication...")
    await scraper.client.start()
    logger.info("Scraper authenticated successfully.")

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
