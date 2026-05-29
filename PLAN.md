# Implementation Plan: News Aggregator and Rephraser Bot

This document outlines the step-by-step checklist to implement the bot. You can follow this plan to complete the code on any device.

---

## Phase 1: Environment & Database Setup
- [ ] **Step 1.1**: Create the `.env` file from `.env.example` and populate keys (`TELEGRAM_API_ID`, `TELEGRAM_API_HASH`, `TELEGRAM_BOT_TOKEN`, `ANTHROPIC_API_KEY`, etc.).
- [ ] **Step 1.2**: Implement `db.py` using `aiosqlite`.
  - [ ] Write asynchronous function to initialize the database and create the `posts` table (see [DESIGN.md](DESIGN.md) for schema).
  - [ ] Write `add_raw_post(source_channel, source_msg_id, original_text, media_paths, media_type)` to insert new scrapings.
  - [ ] Write `update_post_status(post_id, status)` and `update_post_text(post_id, text)`.
  - [ ] Write query functions to fetch posts by status.

---

## Phase 2: AI Rewrite Engine
- [ ] **Step 2.1**: Implement `rewriter.py`.
  - [ ] Create `BaseRewriter` interface.
  - [ ] Implement `ClaudeRewriter` (or `GeminiRewriter`) using official SDK.
  - [ ] Write code to read system prompt instructions dynamically from `prompt.txt`.
  - [ ] Implement automatic retries (up to 3 times) for network robustness.

---

## Phase 3: Scraper Engine (Userbot)
- [ ] **Step 3.1**: Implement `scraper.py` using `Telethon`.
  - [ ] Initialize `TelegramClient` session.
  - [ ] Set up an event handler for `events.NewMessage` filtering by the configured list of `SOURCE_CHANNELS`.
  - [ ] Implement media download: download attachments into `data/media/` and format media paths as JSON arrays.
  - [ ] Save the scraped metadata and files into SQLite via `db.py` with status `new`.
  - [ ] Chain the process to trigger the Rewrite Engine automatically for new posts.

---

## Phase 4: Admin Moderation Bot
- [ ] **Step 4.1**: Implement `bot.py` using `aiogram v3`.
  - [ ] Initialize the `Bot` and `Dispatcher`.
  - [ ] Implement a handler to format and send draft posts to `ADMIN_CHAT_ID`.
  - [ ] Attach inline buttons under each draft:
    - `✅ Опубликовать` (callback: `publish:<post_id>`)
    - `✏️ Редактировать` (callback: `edit:<post_id>`)
    - `❌ Отклонить` (callback: `reject:<post_id>`)
- [ ] **Step 4.2**: Implement button callbacks.
  - [ ] **Publish**: Send the post content and media to `TARGET_CHANNEL_ID`, update database status to `published`, and edit the admin message to display a success badge.
  - [ ] **Reject**: Update database status to `rejected`, delete local media files from `data/media/`, and edit the admin message to display a deletion badge.
  - [ ] **Edit**: Place the bot into a state waiting for the admin's reply. When the reply is received, update the draft's text in SQLite and send the updated draft to the admin.

---

## Phase 5: Entrypoint & Orchestration
- [ ] **Step 5.1**: Implement `main.py` to launch both async loops together.
  - [ ] Use `asyncio.gather` or similar orchestration to run the `Telethon` client loop and the `aiogram` dispatcher loop concurrently.
  - [ ] Ensure directories like `data/media/` are automatically created on startup if they do not exist.
  - [ ] Setup standard python logging to capture errors gracefully.
