# Telegram News Aggregator and Rephraser Bot

This bot monitors specified Telegram channels, downloads their posts (with media), rephrases them using an LLM (Anthropic Claude/Gemini/OpenAI), and presents them to an administrator for manual review. Only approved posts are published to the target channel.

## 🚀 Key Features
- **Automatic Scraping**: Monitors multiple public/private external channels using a Telegram Userbot (Telethon).
- **AI-Powered Rewriting**: Rephrases post content according to a customizable prompt (`prompt.txt`) to preserve context but rewrite in your style.
- **Manual Moderation**: Zero-exposure publishing. Every draft is sent to your private admin chat with convenient inline buttons: `[✅ Опубликовать]`, `[✏️ Редактировать]`, `[❌ Отклонить]`.
- **Media Preservation**: Keeps photos and videos linked to their corresponding rewritten text when publishing.

---

## 🛠️ Installation & Setup

### 1. Clone & Install Dependencies
Ensure you have Python 3.10+ installed.
```bash
pip install -r requirements.txt
```

### 2. Configure Environment Variables
Create a `.env` file based on `.env.example`:
```bash
cp .env.example .env
```

Fill in the required tokens and credentials:
- **TELEGRAM_API_ID** & **TELEGRAM_API_HASH**: Obtain from [my.telegram.org](https://my.telegram.org).
- **TELEGRAM_BOT_TOKEN**: Obtain from `@BotFather`.
- **ANTHROPIC_API_KEY**: Your Claude API key.
- **ADMIN_CHAT_ID**: Your personal Telegram User ID (can be fetched using `@userinfobot`).
- **TARGET_CHANNEL_ID**: The ID or handle of your channel (e.g., `@my_awesome_channel` or `-100xxxxxxxxx`).
- **SOURCE_CHANNELS**: A comma-separated list of channel usernames or IDs to monitor (e.g., `competitor_channel_1, @competitor_2`).

### 3. Customize Writing Style
Open `prompt.txt` and define how the AI should rewrite the incoming posts.

### 4. Run the Bot
```bash
python main.py
```
*Note: On the first run, the Userbot will ask you to enter your phone number and the Telegram verification code in the terminal to create the session.*

---

## 📂 Project Architecture
For detailed component schematics, database schemas, and integration details, please refer to [DESIGN.md](DESIGN.md).
