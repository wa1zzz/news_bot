import aiosqlite
import json
import logging

DB_NAME = "database.db"

logger = logging.getLogger(__name__)

async def init_db():
    """Initializes the database schema."""
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("""
        CREATE TABLE IF NOT EXISTS posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_channel_id INTEGER NOT NULL,
            source_message_id INTEGER NOT NULL,
            original_text TEXT,
            rewritten_text TEXT,
            media_paths TEXT,
            media_type TEXT,
            status TEXT NOT NULL,
            moderation_message_id INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """)
        await db.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_source_msg 
        ON posts(source_channel_id, source_message_id);
        """)
        await db.commit()
    logger.info("Database initialized.")

async def is_post_processed(channel_id: int, message_id: int) -> bool:
    """Checks if a post has already been captured."""
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute(
            "SELECT 1 FROM posts WHERE source_channel_id = ? AND source_message_id = ?",
            (channel_id, message_id)
        ) as cursor:
            return await cursor.fetchone() is not None

async def add_raw_post(channel_id: int, message_id: int, text: str, media_paths: list, media_type: str) -> int:
    """Inserts a new raw post into the database."""
    media_json = json.dumps(media_paths)
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute(
            """
            INSERT INTO posts (source_channel_id, source_message_id, original_text, media_paths, media_type, status)
            VALUES (?, ?, ?, ?, ?, 'new')
            """,
            (channel_id, message_id, text, media_json, media_type)
        ) as cursor:
            await db.commit()
            return cursor.lastrowid

async def update_post_status(post_id: int, status: str):
    """Updates the status of a post."""
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "UPDATE posts SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (status, post_id)
        )
        await db.commit()

async def update_post_rewritten_text(post_id: int, text: str):
    """Updates the rewritten text of a post."""
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "UPDATE posts SET rewritten_text = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (text, post_id)
        )
        await db.commit()

async def set_moderation_message_id(post_id: int, message_id: int):
    """Saves the message ID sent to the admin chat."""
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "UPDATE posts SET moderation_message_id = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (message_id, post_id)
        )
        await db.commit()

async def get_post(post_id: int) -> dict:
    """Fetches a single post by ID."""
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM posts WHERE id = ?", (post_id,)) as cursor:
            row = await cursor.fetchone()
            if row:
                res = dict(row)
                res["media_paths"] = json.loads(res["media_paths"])
                return res
            return None

async def get_post_by_moderation_message_id(message_id: int) -> dict:
    """Fetches a post by its moderation message ID."""
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM posts WHERE moderation_message_id = ?", (message_id,)) as cursor:
            row = await cursor.fetchone()
            if row:
                res = dict(row)
                res["media_paths"] = json.loads(res["media_paths"])
                return res
            return None
