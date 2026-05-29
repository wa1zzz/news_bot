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
    def __init__(self, token: str, admin_chat_ids: list, target_channel_id: str, scraper=None):
        self.bot = Bot(token=token)
        self.dp = Dispatcher()
        self.admin_chat_ids = admin_chat_ids
        self.target_channel_id = target_channel_id
        self.scraper = scraper

        # Register bot handlers
        self._register_handlers()

    def _register_handlers(self):
        @self.dp.message(Command("start"))
        async def start_cmd(message: types.Message):
            if message.chat.id not in self.admin_chat_ids:
                await message.reply("Доступ ограничен.")
                return
            await message.reply("Бот-модератор запущен. Сюда будут приходить черновики для проверки.")

        @self.dp.message(Command("list"))
        async def list_cmd(message: types.Message):
            if message.chat.id not in self.admin_chat_ids:
                return
            
            channels = await db.get_monitored_channels()
            if not channels:
                await message.reply("Список отслеживаемых каналов пуст.")
                return
            
            reply_lines = ["📋 *Отслеживаемые каналы:*"]
            for i, ch in enumerate(channels, 1):
                title = ch["title"] or "Без названия"
                username = f"@{ch['username']}" if ch["username"] else "приватный"
                ch_id = ch["channel_id"]
                reply_lines.append(f"{i}. *{title}* ({username}, ID: `{ch_id}`)")
            
            await message.reply("\n".join(reply_lines), parse_mode="Markdown")

        @self.dp.message(Command("add"))
        async def add_cmd(message: types.Message):
            if message.chat.id not in self.admin_chat_ids:
                return
            
            args = message.text.split(maxsplit=1)
            if len(args) < 2:
                await message.reply("Использование: `/add <username_или_id>` (например, `/add @rhymestg` или `/add -1001234567890`)", parse_mode="Markdown")
                return
            
            identifier = args[1].strip()
            status_msg = await message.reply("⌛ Попытка разрешить канал в Telegram...")
            
            try:
                if not self.scraper or not self.scraper.client:
                    raise Exception("Клиент юзербота (scraper) не инициализирован.")
                
                entity_id = None
                try:
                    if identifier.startswith("-"):
                        entity_id = int(identifier)
                    else:
                        entity_id = identifier
                except ValueError:
                    entity_id = identifier
                
                # Fetch entity using userbot
                entity = await self.scraper.client.get_entity(entity_id)
                
                # Verify it is a channel or chat
                from telethon.tl.types import Channel, Chat
                if not isinstance(entity, (Channel, Chat)):
                    await status_msg.edit_text("❌ Указанный объект не является каналом или группой.")
                    return
                
                import telethon.utils
                signed_id = telethon.utils.get_peer_id(entity)
                
                username = getattr(entity, 'username', None)
                title = getattr(entity, 'title', 'Без названия')
                
                await db.add_monitored_channel(channel_id=signed_id, username=username, title=title)
                
                # Auto-join
                try:
                    from telethon.tl.functions.channels import JoinChannelRequest
                    await self.scraper.client(JoinChannelRequest(entity))
                except Exception as e:
                    logger.debug(f"Could not join channel automatically: {e}")
                
                username_str = f" (@{username})" if username else ""
                await status_msg.edit_text(
                    f"✅ *Канал успешно добавлен!*\n\n"
                    f"📌 *Название:* {title}\n"
                    f"🔗 *Ссылка/Юзернейм:* {username_str or 'приватный'}\n"
                    f"🆔 *ID:* `{signed_id}`",
                    parse_mode="Markdown"
                )
            except Exception as e:
                logger.error(f"Failed to add channel: {e}", exc_info=True)
                await status_msg.edit_text(f"❌ *Ошибка при разрешении канала:* {e}", parse_mode="Markdown")

        @self.dp.message(Command("remove"))
        async def remove_cmd(message: types.Message):
            if message.chat.id not in self.admin_chat_ids:
                return
            
            args = message.text.split(maxsplit=1)
            if len(args) < 2:
                await message.reply("Использование: `/remove <username_или_id>` (например, `/remove @rhymestg` или `/remove -1001234567890`)", parse_mode="Markdown")
                return
            
            identifier = args[1].strip()
            
            deleted = await db.remove_monitored_channel(identifier)
            if deleted:
                await message.reply(f"✅ Канал *{identifier}* успешно удален из списка отслеживания.", parse_mode="Markdown")
            else:
                await message.reply(f"❌ Канал *{identifier}* не найден в списке отслеживания.", parse_mode="Markdown")

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
            if message.chat.id not in self.admin_chat_ids:
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
        """Sends a post draft to all admins for manual approval."""
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

        # Get source channel details
        source_channel = await db.get_monitored_channel_by_id(post["source_channel_id"])
        source_str = ""
        if source_channel:
            ch_title = source_channel["title"] or "Без названия"
            ch_username = f"@{source_channel['username']}" if source_channel["username"] else f"ID: {post['source_channel_id']}"
            source_str = f"📢 *Источник:* {ch_title} ({ch_username})\n"
        else:
            source_str = f"📢 *Источник:* ID {post['source_channel_id']}\n"

        caption_suffix = f"\n\n{source_str}🔍 *Черновик готов к публикации.*"
        full_caption = text + caption_suffix

        for admin_id in self.admin_chat_ids:
            try:
                sent_msg = None
                if media_type == "photo" and media_paths:
                    photo_file = types.FSInputFile(media_paths[0])
                    sent_msg = await self.bot.send_photo(
                        chat_id=admin_id,
                        photo=photo_file,
                        caption=full_caption,
                        parse_mode="Markdown",
                        reply_markup=keyboard
                    )
                elif media_type == "video" and media_paths:
                    video_file = types.FSInputFile(media_paths[0])
                    sent_msg = await self.bot.send_video(
                        chat_id=admin_id,
                        video=video_file,
                        caption=full_caption,
                        parse_mode="Markdown",
                        reply_markup=keyboard
                    )
                else:
                    sent_msg = await self.bot.send_message(
                        chat_id=admin_id,
                        text=full_caption,
                        parse_mode="Markdown",
                        reply_markup=keyboard
                    )

                if sent_msg:
                    await db.set_moderation_message_id(post_id, sent_msg.message_id)
                    await db.update_post_status(post_id, "pending")
            except Exception as e:
                logger.error(f"Failed to send draft for post {post_id} to admin {admin_id}: {e}", exc_info=True)

    async def start(self):
        logger.info("Starting Moderation Bot...")
        await self.dp.start_polling(self.bot)
