import os
import logging
import html
from aiogram import Bot, Dispatcher, types, F
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.filters import Command
import db

logger = logging.getLogger(__name__)

def escape_markdown(text: str) -> str:
    """Escapes Markdown V1 special characters."""
    if not text:
        return ""
    for char in ['*', '_', '`', '[']:
        text = text.replace(char, f'\\{char}')
    return text

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
            
            reply_lines = ["📋 <b>Отслеживаемые каналы:</b>"]
            for i, ch in enumerate(channels, 1):
                title = html.escape(ch["title"] or "Без названия")
                username = f"@{html.escape(ch['username'])}" if ch["username"] else "приватный"
                ch_id = ch["channel_id"]
                reply_lines.append(f"{i}. <b>{title}</b> ({username}, ID: <code>{ch_id}</code>)")
            
            await message.reply("\n".join(reply_lines), parse_mode="HTML")

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
                
                username_str = f" (@{html.escape(username)})" if username else ""
                await status_msg.edit_text(
                    f"✅ <b>Канал успешно добавлен!</b>\n\n"
                    f"📌 <b>Название:</b> {html.escape(title)}\n"
                    f"🔗 <b>Ссылка/Юзернейм:</b> {username_str or 'приватный'}\n"
                    f"🆔 <b>ID:</b> <code>{signed_id}</code>",
                    parse_mode="HTML"
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

                if len(media_paths) > 1:
                    media_group = []
                    for i, path in enumerate(media_paths):
                        file_input = types.FSInputFile(path)
                        if media_type == "video" and ("mp4" in path.lower() or "mov" in path.lower()):
                            item = types.InputMediaVideo(media=file_input, caption=text if i == 0 else None, parse_mode="Markdown")
                        else:
                            item = types.InputMediaPhoto(media=file_input, caption=text if i == 0 else None, parse_mode="Markdown")
                        media_group.append(item)
                    await self.bot.send_media_group(chat_id=self.target_channel_id, media=media_group)
                elif media_type == "photo" and media_paths:
                    photo_file = types.FSInputFile(media_paths[0])
                    await self.bot.send_photo(chat_id=self.target_channel_id, photo=photo_file, caption=text)
                elif media_type == "video" and media_paths:
                    video_file = types.FSInputFile(media_paths[0])
                    await self.bot.send_video(chat_id=self.target_channel_id, video=video_file, caption=text)
                else:
                    await self.bot.send_message(chat_id=self.target_channel_id, text=text)

                await db.update_post_status(post_id, "published")
                
                # Update moderation message in admin chat with multi-publish keyboard
                multi_keyboard = InlineKeyboardMarkup(inline_keyboard=[
                    [
                        InlineKeyboardButton(text="✅ Опубликовать еще раз", callback_data=f"publish:{post_id}"),
                        InlineKeyboardButton(text="✏️ Править", callback_data=f"edit:{post_id}"),
                    ],
                    [
                        InlineKeyboardButton(text="📥 Завершить (Убрать кнопки)", callback_data=f"finish:{post_id}")
                    ]
                ])
                await callback.message.edit_reply_markup(reply_markup=multi_keyboard)
                await callback.message.reply("✅ Опубликовано в канал!")
            except Exception as e:
                logger.error(f"Failed to publish post {post_id}: {e}")
                await callback.message.reply(f"❌ Ошибка публикации: {e}")

        @self.dp.callback_query(F.data.startswith("finish:"))
        async def handle_finish(callback: types.CallbackQuery):
            post_id = int(callback.data.split(":")[1])
            await callback.answer("Модерация завершена.")
            await callback.message.edit_reply_markup(reply_markup=None)
            await callback.message.reply("📥 Пост успешно завершен и отправлен в архив.")

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
            ch_title = escape_markdown(source_channel["title"] or "Без названия")
            ch_username = f"@{escape_markdown(source_channel['username'])}" if source_channel["username"] else f"ID: {post['source_channel_id']}"
            source_str = f"📢 *Источник:* {ch_title} ({ch_username})\n"
        else:
            source_str = f"📢 *Источник:* ID {post['source_channel_id']}\n"

        caption_suffix = f"\n\n{source_str}🔍 *Черновик готов к публикации.*"
        full_caption = text + caption_suffix

        for admin_id in self.admin_chat_ids:
            try:
                sent_msg = None
                if len(media_paths) > 1:
                    media_group = []
                    for i, path in enumerate(media_paths):
                        file_input = types.FSInputFile(path)
                        if media_type == "video" and ("mp4" in path.lower() or "mov" in path.lower()):
                            item = types.InputMediaVideo(media=file_input, caption=full_caption if i == 0 else None, parse_mode="Markdown")
                        else:
                            item = types.InputMediaPhoto(media=file_input, caption=full_caption if i == 0 else None, parse_mode="Markdown")
                        media_group.append(item)
                    
                    # Send media group first (doesn't support inline keyboard)
                    await self.bot.send_media_group(chat_id=admin_id, media=media_group)
                    
                    # Send follow-up command panel containing inline keyboard
                    sent_msg = await self.bot.send_message(
                        chat_id=admin_id,
                        text="⚙️ *Управление альбомом выше:*",
                        parse_mode="Markdown",
                        reply_markup=keyboard
                    )
                elif media_type == "photo" and media_paths:
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
        
        # Set native bot command menu in the Telegram UI
        from aiogram.types import BotCommand
        try:
            await self.bot.set_my_commands([
                BotCommand(command="start", description="Запустить бота"),
                BotCommand(command="list", description="Показать список каналов"),
                BotCommand(command="add", description="Добавить канал в мониторинг"),
                BotCommand(command="remove", description="Удалить канал из мониторинга")
            ])
            logger.info("Bot commands menu registered successfully.")
        except Exception as e:
            logger.error(f"Failed to register bot commands menu: {e}")
            
        await self.dp.start_polling(self.bot)
