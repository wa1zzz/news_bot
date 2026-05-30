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
        
        await db.execute("""
        CREATE TABLE IF NOT EXISTS monitored_channels (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            channel_id INTEGER UNIQUE,
            username TEXT UNIQUE,
            title TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
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

async def add_monitored_channel(channel_id: int, username: str, title: str):
    """Adds a new monitored channel or updates an existing one."""
    if isinstance(username, str) and username.startswith("@"):
        username = username[1:]
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        
        row_by_id = None
        if channel_id:
            async with db.execute("SELECT * FROM monitored_channels WHERE channel_id = ?", (channel_id,)) as cursor:
                row_by_id = await cursor.fetchone()
                
        row_by_name = None
        if username:
            async with db.execute("SELECT * FROM monitored_channels WHERE username = ? COLLATE NOCASE", (username,)) as cursor:
                row_by_name = await cursor.fetchone()
                
        if row_by_id and row_by_name:
            if row_by_id["id"] == row_by_name["id"]:
                # Same row, just update
                await db.execute(
                    "UPDATE monitored_channels SET username = ?, title = COALESCE(?, title) WHERE id = ?",
                    (username, title, row_by_id["id"])
                )
            else:
                # Merge: update the id row with new info, delete the other row
                await db.execute(
                    "UPDATE monitored_channels SET username = ?, title = COALESCE(?, title) WHERE id = ?",
                    (username, title, row_by_id["id"])
                )
                await db.execute("DELETE FROM monitored_channels WHERE id = ?", (row_by_name["id"],))
        elif row_by_id:
            await db.execute(
                "UPDATE monitored_channels SET username = COALESCE(?, username), title = COALESCE(?, title) WHERE id = ?",
                (username, title, row_by_id["id"])
            )
        elif row_by_name:
            await db.execute(
                "UPDATE monitored_channels SET channel_id = COALESCE(?, channel_id), title = COALESCE(?, title) WHERE id = ?",
                (channel_id, title, row_by_name["id"])
            )
        else:
            await db.execute(
                "INSERT INTO monitored_channels (channel_id, username, title) VALUES (?, ?, ?)",
                (channel_id, username, title)
            )
        await db.commit()

async def remove_monitored_channel(identifier: str) -> bool:
    """Removes a channel by username or channel ID. Returns True if deleted."""
    if isinstance(identifier, str) and identifier.startswith("@"):
        identifier = identifier[1:]
    
    async with aiosqlite.connect(DB_NAME) as db:
        try:
            chan_id = int(identifier)
            cursor = await db.execute("DELETE FROM monitored_channels WHERE channel_id = ?", (chan_id,))
        except ValueError:
            cursor = await db.execute("DELETE FROM monitored_channels WHERE username = ? COLLATE NOCASE", (identifier,))
        
        await db.commit()
        return cursor.rowcount > 0

async def get_monitored_channels() -> list:
    """Returns a list of dicts of all monitored channels."""
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM monitored_channels ORDER BY created_at DESC") as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

async def is_channel_monitored(channel_id: int, username: str) -> bool:
    """Checks if a channel_id or username is in the monitored list."""
    if not channel_id and not username:
        return False
    
    if isinstance(username, str) and username.startswith("@"):
        username = username[1:]
        
    async with aiosqlite.connect(DB_NAME) as db:
        query = "SELECT 1 FROM monitored_channels WHERE 0=1"
        params = []
        if channel_id:
            query += " OR channel_id = ?"
            params.append(channel_id)
        if username:
            query += " OR username = ? COLLATE NOCASE"
            params.append(username)
        
        async with db.execute(query, tuple(params)) as cursor:
            res = await cursor.fetchone() is not None
            return res

async def get_monitored_channel_by_id(channel_id: int) -> dict:
    """Gets a monitored channel by its numeric ID."""
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM monitored_channels WHERE channel_id = ?", (channel_id,)) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None
