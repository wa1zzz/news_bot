import os
import logging
from aiogram import Bot, Dispatcher, types, F
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.filters import Command
import db

logger = logging.getLogger(__name__)

# Active editing states: maps user_id -> post_id
active_edits = {}

class ModerationBot:
    def __init__(self, token: str, admin_chat_id: int, target_channel_id: str):
        self.bot = Bot(token=token)
        self.dp = Dispatcher()
        self.admin_chat_id = admin_chat_id
        self.target_channel_id = target_channel_id

        # Register bot handlers
        self._register_handlers()

    def _register_handlers(self):
        @self.dp.message(Command("start"))
        async def start_cmd(message: types.Message):
            if message.chat.id != self.admin_chat_id:
                await message.reply("Доступ ограничен.")
                return
            await message.reply("Бот-модератор запущен. Сюда будут приходить черновики для проверки.")

        @self.dp.callback_query(F.data.startswith("publish:"))
        async def handle_publish(callback: types.CallbackQuery):
            post_id = int(callback.data.split(":")[1])
            post = await db.get_post(post_id)
            if not post:
                await callback.answer("Пост не найден в базе данных.", show_alert=True)
                return

            await callback.answer("Публикую в канал...")
            try:
                # Publish to target channel
                text = post["rewritten_text"] or post["original_text"]
                media_paths = post["media_paths"]
                media_type = post["media_type"]

                if media_type == "photo" and media_paths:
                    photo_file = types.FSInputFile(media_paths[0])
                    await self.bot.send_photo(chat_id=self.target_channel_id, photo=photo_file, caption=text)
                elif media_type == "video" and media_paths:
                    video_file = types.FSInputFile(media_paths[0])
                    await self.bot.send_video(chat_id=self.target_channel_id, video=video_file, caption=text)
                else:
                    await self.bot.send_message(chat_id=self.target_channel_id, text=text)

                await db.update_post_status(post_id, "published")
                
                # Update moderation message in admin chat
                await callback.message.edit_reply_markup(reply_markup=None)
                await callback.message.reply("✅ Опубликовано в канал!")
            except Exception as e:
                logger.error(f"Failed to publish post {post_id}: {e}")
                await callback.message.reply(f"❌ Ошибка публикации: {e}")

        @self.dp.callback_query(F.data.startswith("reject:"))
        async def handle_reject(callback: types.CallbackQuery):
            post_id = int(callback.data.split(":")[1])
            post = await db.get_post(post_id)
            if not post:
                await callback.answer("Пост не найден.")
                return

            await callback.answer("Отклонено")
            await db.update_post_status(post_id, "rejected")

            # Clean up media file to save space
            for path in post["media_paths"]:
                if os.path.exists(path):
                    try:
                        os.remove(path)
                    except Exception as e:
                        logger.error(f"Error removing media file {path}: {e}")

            await callback.message.edit_reply_markup(reply_markup=None)
            await callback.message.reply("❌ Черновик отклонен и удален с сервера.")

        @self.dp.callback_query(F.data.startswith("edit:"))
        async def handle_edit_request(callback: types.CallbackQuery):
            post_id = int(callback.data.split(":")[1])
            active_edits[callback.from_user.id] = post_id
            await callback.answer()
            await callback.message.reply(
                "✏️ Отправьте следующим сообщением обновленный текст для этого поста (просто пришлите его текстом)."
            )

        @self.dp.message()
        async def handle_admin_text(message: types.Message):
            if message.chat.id != self.admin_chat_id:
                return

            # Check if admin is currently editing a post
            user_id = message.from_user.id
            if user_id in active_edits:
                post_id = active_edits.pop(user_id)
                new_text = message.text

                # Update database
                await db.update_post_rewritten_text(post_id, new_text)
                await db.update_post_status(post_id, "pending")

                await message.reply("📝 Текст обновлен! Отправляю обновленный черновик на модерацию...")
                
                # Resend the draft to the admin
                await self.send_draft(post_id)
            else:
                await message.reply("Пожалуйста, используйте кнопки под черновиками для управления постами.")

    async def send_draft(self, post_id: int):
        """Sends a post draft to the admin for manual approval."""
        post = await db.get_post(post_id)
        if not post:
            return

        text = post["rewritten_text"] or post["original_text"]
        media_paths = post["media_paths"]
        media_type = post["media_type"]

        # Form inline keyboard
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Опубликовать", callback_data=f"publish:{post_id}"),
                InlineKeyboardButton(text="✏️ Править", callback_data=f"edit:{post_id}"),
            ],
            [
                InlineKeyboardButton(text="❌ Отклонить", callback_data=f"reject:{post_id}")
            ]
        ])

        caption_suffix = "\n\n🔍 *Черновик готов к публикации.*"
        full_caption = text + caption_suffix

        try:
            sent_msg = None
            if media_type == "photo" and media_paths:
                photo_file = types.FSInputFile(media_paths[0])
                sent_msg = await self.bot.send_photo(
                    chat_id=self.admin_chat_id,
                    photo=photo_file,
                    caption=full_caption,
                    parse_mode="Markdown",
                    reply_markup=keyboard
                )
            elif media_type == "video" and media_paths:
                video_file = types.FSInputFile(media_paths[0])
                sent_msg = await self.bot.send_video(
                    chat_id=self.admin_chat_id,
                    video=video_file,
                    caption=full_caption,
                    parse_mode="Markdown",
                    reply_markup=keyboard
                )
            else:
                sent_msg = await self.bot.send_message(
                    chat_id=self.admin_chat_id,
                    text=full_caption,
                    parse_mode="Markdown",
                    reply_markup=keyboard
                )

            if sent_msg:
                await db.set_moderation_message_id(post_id, sent_msg.message_id)
                await db.update_post_status(post_id, "pending")
        except Exception as e:
            logger.error(f"Failed to send draft for post {post_id} to admin: {e}", exc_info=True)

    async def start(self):
        logger.info("Starting Moderation Bot...")
        await self.dp.start_polling(self.bot)
