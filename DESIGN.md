# Technical Design: Telegram Channel Aggregator and Rephraser Bot

This document describes the architecture, data flow, database schema, and component layout for the Telegram News Aggregator Bot.

---

## 1. Architectural Overview

The bot is designed as a single asynchronous Python service utilizing `asyncio`. It handles four main responsibilities:
1. **Scraping**: Monitoring multiple external Telegram channels for new posts using a Telegram Client (Userbot).
2. **Rephrasing**: Rewriting original posts using an LLM (default: Anthropic Claude, designed for easy provider switching).
3. **Moderation**: Presenting drafts to the admin in a private Telegram chat with inline keyboard buttons.
4. **Publishing**: Sending approved posts (including media attachments) to the target Telegram channel.

```
                          ┌───────────────────────────┐
                          │ External Telegram Sources │
                          └─────────────┬─────────────┘
                                        │ (New post event)
                                        ▼
                          ┌───────────────────────────┐
                          │ Scraper Engine (Telethon) │
                          └─────────────┬─────────────┘
                                        │ 1. Download media to data/media/
                                        │ 2. Insert raw post to SQLite
                                        ▼
                          ┌───────────────────────────┐
                          │   Rewrite Engine (LLM)    │
                          │ (Claude / rules.txt prompt)│
                          └─────────────┬─────────────┘
                                        │ 3. Generate rephrased version
                                        ▼
                          ┌───────────────────────────┐
                          │   Moderator Bot (aiogram) │
                          └─────────────┬─────────────┘
                                        │ 4. Send draft to admin chat
                                        ▼
                          ┌───────────────────────────┐
                          │   Admin Moderation Chat   │
                          │  [Publish] [Edit] [Reject]│
                          └─────────────┬─────────────┘
                                        │ (On "Publish" callback)
                                        ▼
                          ┌───────────────────────────┐
                          │   Target Telegram Channel │
                          └───────────────────────────┘
```

---

## 2. Database Schema (SQLite)

We use SQLite for local state management, ensuring durability and preventing double-processing of posts.

```sql
CREATE TABLE IF NOT EXISTS posts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_channel_id INTEGER NOT NULL,
    source_message_id INTEGER NOT NULL,
    original_text TEXT,
    rewritten_text TEXT,
    media_paths TEXT,            -- JSON array of local file paths (e.g., '["data/media/img1.jpg"]')
    media_type TEXT,             -- 'photo', 'video', 'none', 'album'
    status TEXT NOT NULL,        -- 'new', 'rewriting', 'pending', 'approved', 'rejected', 'published'
    moderation_message_id INTEGER, -- ID of the message sent to the admin for moderation
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_source_msg 
ON posts(source_channel_id, source_message_id);
```

---

## 3. Project Structure

```
news_bot/
├── .env.example          # Environment variables template
├── .gitignore            # Git exclusion rules
├── README.md             # Project overview and setup instructions
├── DESIGN.md             # Technical design spec (this file)
├── requirements.txt      # Python dependencies
├── prompt.txt            # System instructions for the LLM
├── db.py                 # SQLite database helper functions
├── rewriter.py           # LLM rewriting module (pluggable architecture)
├── scraper.py            # Userbot monitoring external channels using Telethon
├── bot.py                # Admin moderation bot using aiogram
└── main.py               # Application entrypoint to orchestrate services
```

---

## 4. Component Details

### A. Scraper Engine (`scraper.py`)
- Built using **Telethon** (Telegram Client API).
- Uses a personal user account session (`session_name.session`) requiring `API_ID` and `API_HASH` from [my.telegram.org](https://my.telegram.org).
- Filters for `events.NewMessage` from defined source channel IDs.
- On receipt of a message, it downloads media using `client.download_media` to a local `data/media/` folder and inserts the post into the database with a status of `new`.

### B. Rewrite Engine (`rewriter.py`)
- Provides a clean abstraction interface:
  ```python
  class BaseRewriter:
      async def rewrite(self, text: str) -> str:
          pass
  ```
- Implements `ClaudeRewriter` as the default provider using the `anthropic` SDK.
- Reads guidelines from `prompt.txt` to dynamically instruct the LLM.
- Can be easily updated or replaced with `GeminiRewriter` or `OpenAIRewriter` by changing the active class or configuration.

### C. Database Helper (`db.py`)
- Simple asynchronous database adapter using Python's standard `sqlite3` library (wrapped in async helpers) or `aiosqlite`.
- Tracks post lifecycles and caches media file associations.

### D. Moderation & Publishing Bot (`bot.py`)
- Built using **aiogram v3** (standard asynchronous Telegram Bot framework).
- Receives completed drafts and forwards them to the admin's `ADMIN_CHAT_ID`.
- Presents a premium interactive experience with inline keyboard buttons:
  - `✅ Опубликовать` (Trigger callback: `publish:<post_id>`)
  - `✏️ Редактировать` (Trigger callback: `edit:<post_id>`)
  - `❌ Отклонить` (Trigger callback: `reject:<post_id>`)
- Implements edit mode by listening to replies to the moderation message.

---

## 5. Deployment and Operations

1. **Prerequisites**:
   - Python 3.10+
   - API ID & API Hash (from [my.telegram.org](https://my.telegram.org))
   - Bot Token (from `@BotFather`)
   - Anthropic API Key (from [console.anthropic.com](https://console.anthropic.com))
2. **Execution**:
   - Run `python main.py` to start the daemon.
   - The userbot will prompt for the phone number and login code on the first run to establish the session.
