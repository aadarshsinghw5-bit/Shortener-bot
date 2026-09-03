import os
import asyncio
import logging
import threading

from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import quote

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
    MessageHandler,
    filters,
)
from telegram.error import TelegramError

from database import Database
from shortener import Shortener


# =========================================================
# LOGGING
# =========================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)

log = logging.getLogger(__name__)


# =========================================================
# ENVIRONMENT
# =========================================================

BOT_TOKEN = os.environ["BOT_TOKEN"]
OWNER_ID = int(os.environ["OWNER_ID"])
DB_CHANNEL_ID = int(os.environ["DB_CHANNEL_ID"])
BOT_USERNAME = os.environ["BOT_USERNAME"].lstrip("@")


# =========================================================
# DATABASE
# =========================================================

db = Database()


# =========================================================
# SHORTENER
# =========================================================

shortener = Shortener(
    os.getenv(
        "SHORTENER_API_URL",
        "https://arolinks.com/api",
    ),
    os.getenv(
        "SHORTENER_API_KEY",
        "",
    ),
    BOT_USERNAME,
    db,
)


# =========================================================
# HEALTH SERVER
# =========================================================

class HealthHandler(BaseHTTPRequestHandler):

    def do_GET(self):
        self.send_response(200)
        self.send_header(
            "Content-Type",
            "text/plain; charset=utf-8",
        )
        self.end_headers()

        self.wfile.write(
            b"Bot is running!"
        )

    def log_message(self, fmt, *args):
        return


def start_health_server():

    try:
        port = int(
            os.environ.get(
                "PORT",
                "10000",
            )
        )

        server = HTTPServer(
            ("0.0.0.0", port),
            HealthHandler,
        )

        log.info(
            "Health server running on port %s",
            port,
        )

        server.serve_forever()

    except Exception:
        log.exception(
            "Health server error"
        )


# =========================================================
# HELPERS
# =========================================================

def is_owner(user_id):
    return user_id == OWNER_ID


def is_admin(user_id):
    return (
        user_id == OWNER_ID
        or db.is_admin(user_id)
    )


def bot_link(target):
    return (
        f"https://t.me/{BOT_USERNAME}"
        f"?start={target}"
    )


async def send_text(
    update,
    text,
    **kwargs,
):

    message = update.effective_message

    if message:
        return await message.reply_text(
            text,
            **kwargs,
        )


def get_delete_seconds():

    try:
        return db.get_delete_seconds()

    except Exception:
        return 600


# =========================================================
# AUTO DELETE
# =========================================================

async def delete_later(
    context,
    chat_id,
    message_ids,
    seconds,
):

    await asyncio.sleep(seconds)

    if not isinstance(
        message_ids,
        list,
    ):
        message_ids = [message_ids]

    for message_id in message_ids:

        try:
            await context.bot.delete_message(
                chat_id=chat_id,
                message_id=message_id,
            )

        except TelegramError:
            pass


# =========================================================
# FILE DELIVERY
# =========================================================

async def copy_stored_message(
    context,
    chat_id,
    row,
):

    return await context.bot.copy_message(
        chat_id=chat_id,
        from_chat_id=row["channel_id"],
        message_id=row["message_id"],
    )


async def send_file(
    context,
    chat_id,
    row,
):

    seconds = get_delete_seconds()

    file_msg = await copy_stored_message(
        context,
        chat_id,
        row,
    )

    minutes = max(
        1,
        seconds // 60,
    )

    warning = await context.bot.send_message(
        chat_id=chat_id,
        text=(
            f"⏳ This file will be "
            f"automatically deleted in "
            f"{minutes} minutes."
        ),
    )

    asyncio.create_task(
        delete_later(
            context,
            chat_id,
            [
                file_msg.message_id,
                warning.message_id,
            ],
            seconds,
        )
    )


async def send_batch(
    context,
    chat_id,
    items,
):

    seconds = get_delete_seconds()

    message_ids = []

    for row in items:

        try:

            msg = await copy_stored_message(
                context,
                chat_id,
                row,
            )

            message_ids.append(
                msg.message_id
            )

        except TelegramError:
            log.exception(
                "Batch delivery failed"
            )

    if not message_ids:

        await context.bot.send_message(
            chat_id=chat_id,
            text="❌ Could not deliver the batch.",
        )

        return

    minutes = max(
        1,
        seconds // 60,
    )

    warning = await context.bot.send_message(
        chat_id=chat_id,
        text=(
            f"⏳ These {len(message_ids)} "
            f"files will be automatically "
            f"deleted in {minutes} minutes."
        ),
    )

    message_ids.append(
        warning.message_id
    )

    asyncio.create_task(
        delete_later(
            context,
            chat_id,
            message_ids,
            seconds,
        )
    )


# =========================================================
# FORCE SUB
# =========================================================

async def check_fsub(
    update,
    context,
):

    channels = db.list_fsub()

    if not channels:
        return True

    user_id = update.effective_user.id

    missing = []

    for channel in channels:

        try:

            member = await context.bot.get_chat_member(
                channel["channel_id"],
                user_id,
            )

            if member.status in (
                "left",
                "kicked",
            ):
                missing.append(channel)

        except TelegramError as e:

            log.warning(
                "FSUB check failed for %s: %s",
                channel["channel_id"],
                e,
            )

    if not missing:
        return True

    buttons = []

    for channel in missing:

        link = (
            channel.get("invite_link")
            or channel.get("username")
        )

        title = (
            channel.get("title")
            or "Join Channel"
        )

        if link:

            buttons.append([
                InlineKeyboardButton(
                    f"📢 {title}",
                    url=link,
                )
            ])

    buttons.append([
        InlineKeyboardButton(
            "✅ Check Subscription",
            callback_data="check_fsub",
        )
    ])

    await send_text(
        update,
        (
            "🔒 <b>Join Required Channel(s)</b>\n\n"
            "Please join all required channels "
            "and then press <b>Check Subscription</b>."
        ),
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(
            buttons
        ),
    )

    return False


async def fsub_callback(
    update,
    context,
):

    query = update.callback_query

    await query.answer()

    try:
        await query.message.delete()

    except TelegramError:
        pass

    if not await check_fsub(
        update,
        context,
    ):
        return

    await query.message.chat.send_message(
        (
            "✅ Subscription verified.\n\n"
            "Now open your file link again."
        )
    )


# =========================================================
# START MESSAGE
# =========================================================

async def show_start_menu(
    update,
    context,
):

    start_image = db.get_setting(
        "start_image",
        None,
    )

    caption = (
        f"⚡ <b>HEY, "
        f"{update.effective_user.first_name} ~</b>\n\n"
        "I AM FILE STORE BOT, I CAN STORE PRIVATE "
        "FILES IN SPECIFIED CHANNEL AND OTHER USERS "
        "CAN ACCESS IT FROM SPECIAL LINK."
    )

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "HELP",
                callback_data="help",
            ),
            InlineKeyboardButton(
                "CLOSE",
                callback_data="close",
            ),
        ],
    ])

    if start_image:

        try:

            await update.message.reply_photo(
                photo=start_image,
                caption=caption,
                parse_mode="HTML",
                reply_markup=keyboard,
            )

            return

        except TelegramError:

            log.exception(
                "Saved start image could not be sent"
            )

            db.set_setting(
                "start_image",
                "",
            )

    await update.message.reply_text(
        caption,
        parse_mode="HTML",
        reply_markup=keyboard,
    )


async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    user = update.effective_user

    user_id = user.id

    try:

        db.add_user(
            user_id,
            user.username or "",
            user.first_name or "",
        )

    except Exception:

        log.exception(
            "Could not save user"
        )

    try:

        if db.is_banned(user_id):

            await send_text(
                update,
                "🚫 You are blocked from using this bot.",
            )

            return

    except Exception:
        pass

    arg = (
        context.args[0]
        if context.args
        else ""
    )

    if arg.startswith("verify_"):

        await verify_token(
            update,
            context,
            arg[7:],
        )

        return

    if not await check_fsub(
        update,
        context,
    ):
        return

    if arg.startswith("file_"):

        await deliver_file(
            update,
            context,
            arg[5:],
        )

        return

    if arg.startswith("batch_"):

        await deliver_batch(
            update,
            context,
            arg[6:],
        )

        return

    await show_start_menu(
        update,
        context,
    )


# =========================================================
# START MENU CALLBACKS
# =========================================================

async def start_menu_callback(
    update,
    context,
):

    query = update.callback_query

    await query.answer()

    data = query.data

    if data == "close":

        try:
            await query.message.delete()

        except TelegramError:
            pass

        return

    if data == "help":

        help_text = """
📚 <b>Bot Commands</b>

<b>User Commands</b>

/start - Wake up bot
/my_plan - Check premium
/request - Submit movie/series request

<b>Admin Commands</b>

/auto_del - Auto delete settings
/fsub_chnl - View FSUB channels
/add_banuser - Ban user
/del_banuser - Unban user
/banuser_list - Banned users

/add_premium - Add premium
/remove_premium - Remove premium
/list_premium - Premium users

/add_fsub - Add force-sub channel
/del_fsub - Remove force-sub channel

/broadcast - Broadcast
/pbroadcast - Broadcast + pin

/batch - Create batch
/genlink - Generate file link

/settings - Bot settings

<b>Owner Commands</b>

/add_admins - Add admin
/del_admins - Remove admin
/admin_list - Admin list
/users - Total users

/cancel - Cancel setup
"""

        await query.message.edit_caption(
            caption=help_text,
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "🔙 BACK",
                        callback_data="back_start",
                    ),
                    InlineKeyboardButton(
                        "❌ CLOSE",
                        callback_data="close",
                    ),
                ]
            ]),
        )


async def back_start_callback(
    update,
    context,
):

    query = update.callback_query

    await query.answer()

    start_image = db.get_setting(
        "start_image",
        None,
    )

    caption = (
        f"⚡ <b>HEY, "
        f"{query.from_user.first_name} ~</b>\n\n"
        "I AM FILE STORE BOT, I CAN STORE PRIVATE "
        "FILES IN SPECIFIED CHANNEL AND OTHER USERS "
        "CAN ACCESS IT FROM SPECIAL LINK."
    )

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "HELP",
                callback_data="help",
            ),
            InlineKeyboardButton(
                "CLOSE",
                callback_data="close",
            ),
        ],
    ])

    try:

        if start_image:

            await query.message.edit_media(
                media={
                    "type": "photo",
                    "media": start_image,
                    "caption": caption,
                    "parse_mode": "HTML",
                },
                reply_markup=keyboard,
            )

        else:

            await query.message.edit_caption(
                caption=caption,
                parse_mode="HTML",
                reply_markup=keyboard,
            )

    except Exception:

        try:
            await query.message.delete()

        except TelegramError:
            pass


# =========================================================
# HELP
# =========================================================

async def help_cmd(
    update,
    context,
):

    await send_text(
        update,
        """
📚 <b>Bot Commands</b>

<b>User Commands</b>

/start - Wake up bot
/my_plan - Check premium
/request - Submit movie/series request

<b>Admin Commands</b>

/auto_del - Auto delete settings
/fsub_chnl - View FSUB channels
/add_banuser - Ban user
/del_banuser - Unban user
/banuser_list - Banned users

/add_premium - Add premium
/remove_premium - Remove premium
/list_premium - Premium users

/add_fsub - Add force-sub channel
/del_fsub - Remove force-sub channel

/broadcast - Broadcast
/pbroadcast - Broadcast + pin

/batch - Create batch
/genlink - Generate file link

/settings - Bot settings

<b>Owner Commands</b>

/add_admins - Add admin
/del_admins - Remove admin
/admin_list - Admin list
/users - Total users

/cancel - Cancel setup
""",
        parse_mode="HTML",
    )


# =========================================================
# ID COMMAND
# =========================================================

async def id_cmd(
    update,
    context,
):

    msg = update.effective_message

    if not msg or not msg.reply_to_message:

        await msg.reply_text(
            "📸 Kisi photo ko reply karke /id bhejo."
        )

        return

    replied = msg.reply_to_message

    if replied.photo:

        photo_id = replied.photo[-1].file_id

        await msg.reply_text(
            (
                "🆔 <b>Photo File ID:</b>\n\n"
                f"<code>{photo_id}</code>"
            ),
            parse_mode="HTML",
        )

        return

    await msg.reply_text(
        "❌ Replied message me photo nahi hai."
    )


# =========================================================
# SETTINGS PANEL
# =========================================================

async def settings_cmd(
    update,
    context,
):

    if not is_admin(
        update.effective_user.id
    ):
        return

    await show_settings(
        update.effective_message,
    )


async def show_settings(
    message,
):

    delete_seconds = get_delete_seconds()

    minutes = max(
        1,
        delete_seconds // 60,
    )

    channels = db.list_fsub()

    start_image = db.get_setting(
        "start_image",
        "",
    )

    image_status = (
        "Set ✅"
        if start_image
        else "Not Set ❌"
    )

    text = (
        "⚙️ <b>Bot Settings</b>\n\n"
        f"🗑️ Auto Delete: <b>{minutes} Minutes</b>\n"
        f"📢 FSub Channels: <b>{len(channels)}</b>\n"
        f"🖼️ Start Image: <b>{image_status}</b>\n\n"
        "Choose an option:"
    )

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "🗑️ Auto Delete",
                callback_data="settings_autodel",
            )
        ],
        [
            InlineKeyboardButton(
                "📢 FSub Channels",
                callback_data="settings_fsub",
            )
        ],
        [
            InlineKeyboardButton(
                "🖼️ Start Image",
                callback_data="settings_image",
            )
        ],
        [
            InlineKeyboardButton(
                "❌ Close",
                callback_data="settings_close",
            )
        ],
    ])

    await message.reply_text(
        text,
        parse_mode="HTML",
        reply_markup=keyboard,
    )


async def settings_callback(
    update,
    context,
):

    query = update.callback_query

    await query.answer()

    if not is_admin(
        query.from_user.id
    ):
        return

    data = query.data

    # -----------------------------------------------------
    # CLOSE
    # -----------------------------------------------------

    if data == "settings_close":

        try:
            await query.message.delete()

        except TelegramError:
            pass

        return

    # -----------------------------------------------------
    # AUTO DELETE
    # -----------------------------------------------------

    if data == "settings_autodel":

        current = get_delete_seconds()

        minutes = max(
            1,
            current // 60,
        )

        await query.message.edit_text(
            (
                "🗑️ <b>Auto Delete</b>\n\n"
                f"Current: <b>{minutes} Minutes</b>\n\n"
                "Use command:\n"
                "<code>/auto_del 600</code>\n\n"
                "600 seconds = 10 minutes."
            ),
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "🔙 Back",
                        callback_data="settings_back",
                    )
                ]
            ]),
        )

        return

    # -----------------------------------------------------
    # FSUB
    # -----------------------------------------------------

    if data == "settings_fsub":

        channels = db.list_fsub()

        if not channels:

            text = (
                "📢 <b>FSub Channels</b>\n\n"
                "No force-sub channels configured."
            )

        else:

            text = (
                "📢 <b>FSub Channels</b>\n\n"
            )

            for channel in channels:

                title = (
                    channel.get("title")
                    or "Unknown Channel"
                )

                text += (
                    f"• 📢 <b>{title}</b>\n"
                    f"  ID: <code>"
                    f"{channel['channel_id']}"
                    f"</code>\n\n"
                )

        await query.message.edit_text(
            text,
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "🔙 Back",
                        callback_data="settings_back",
                    )
                ]
            ]),
        )

        return

    # -----------------------------------------------------
    # START IMAGE
    # -----------------------------------------------------

    if data == "settings_image":

        context.user_data["waiting_for_start_image"] = True

        await query.message.edit_text(
            (
                "🖼️ <b>Start Image</b>\n\n"
                "Send me the new start image now.\n\n"
                "I will save its Telegram <code>file_id</code> "
                "in the database automatically.\n\n"
                "You will NOT need to change Render variables."
            ),
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "❌ Cancel",
                        callback_data="settings_cancel_image",
                    )
                ]
            ]),
        )

        return

    # -----------------------------------------------------
    # CANCEL IMAGE
    # -----------------------------------------------------

    if data == "settings_cancel_image":

        context.user_data.pop(
            "waiting_for_start_image",
            None,
        )

        await query.message.edit_text(
            "❌ Start image setup cancelled.",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "⚙️ Settings",
                        callback_data="settings_back",
                    )
                ]
            ]),
        )

        return

    # -----------------------------------------------------
    # BACK
    # -----------------------------------------------------

    if data == "settings_back":

        await show_settings(
            query.message,
        )

        return


# =========================================================
# SET IMAGE
# =========================================================

async def setimage_cmd(
    update,
    context,
):

    if not is_admin(
        update.effective_user.id
    ):
        return

    # If replying to a photo
    if (
        update.message.reply_to_message
        and update.message.reply_to_message.photo
    ):

        photo = (
            update.message
            .reply_to_message
            .photo[-1]
        )

        db.set_setting(
            "start_image",
            photo.file_id,
        )

        context.user_data.pop(
            "waiting_for_start_image",
            None,
        )

        await send_text(
            update,
            (
                "✅ <b>Start Image Updated!</b>\n\n"
                "The new image has been saved "
                "to the database."
            ),
            parse_mode="HTML",
        )

        return

    context.user_data[
        "waiting_for_start_image"
    ] = True

    await send_text(
        update,
        (
            "🖼️ <b>Send the new Start Image</b>\n\n"
            "Send a photo now and I will save it "
            "automatically.\n\n"
            "You can also reply to an existing photo "
            "with <code>/setimage</code>."
        ),
        parse_mode="HTML",
    )


async def receive_start_image(
    update,
    context,
):

    if not is_admin(
        update.effective_user.id
    ):
        return

    if not context.user_data.get(
        "waiting_for_start_image"
    ):
        return

    if not update.message.photo:
        return

    photo = update.message.photo[-1]

    file_id = photo.file_id

    db.set_setting(
        "start_image",
        file_id,
    )

    context.user_data.pop(
        "waiting_for_start_image",
        None,
    )

    await update.message.reply_text(
        (
            "✅ <b>Start Image Updated!</b>\n\n"
            "🖼️ New start image has been saved "
            "successfully.\n\n"
            "Ab Render me jaake File ID change "
            "karne ki zarurat nahi hai."
        ),
        parse_mode="HTML",
    )


# =========================================================
# SAVE
# =========================================================

async def save_cmd(
    update,
    context,
):

    if not is_admin(
        update.effective_user.id
    ):
        return

    msg = update.message.reply_to_message

    if not msg:

        await send_text(
            update,
            "Reply to a file/post with /save.",
        )

        return

    try:

        copied = await context.bot.copy_message(
            chat_id=DB_CHANNEL_ID,
            from_chat_id=msg.chat_id,
            message_id=msg.message_id,
        )

        file_id = db.add_file(
            DB_CHANNEL_ID,
            copied.message_id,
            msg.caption or "",
        )

        link = bot_link(
            f"file_{file_id}"
        )

        share_url = (
            "https://telegram.me/share/url?url="
            + quote(link, safe="")
        )

        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "📤 Share Link",
                    url=share_url,
                )
            ]
        ])

        try:

            await context.bot.edit_message_reply_markup(
                chat_id=DB_CHANNEL_ID,
                message_id=copied.message_id,
                reply_markup=keyboard,
            )

        except TelegramError:

            log.exception(
                "Could not add share button"
            )

        await send_text(
            update,
            (
                "✅ <b>File Saved!</b>\n\n"
                f"🆔 File ID: <code>{file_id}</code>\n\n"
                f"🤖 Bot Link:\n{link}"
            ),
            parse_mode="HTML",
        )

    except Exception as e:

        log.exception(
            "SAVE ERROR"
        )

        await send_text(
            update,
            f"❌ Could not save file:\n{e}",
        )


# =========================================================
# GENLINK
# =========================================================

async def genlink_cmd(
    update,
    context,
):

    if not is_admin(
        update.effective_user.id
    ):
        return

    if len(context.args) != 1:

        await send_text(
            update,
            "Usage: /genlink <file_id>",
        )

        return

    file_id = context.args[0]

    if not db.get_file(file_id):

        await send_text(
            update,
            "❌ File not found.",
        )

        return

    await send_text(
        update,
        (
            "🔗 <b>Bot Link</b>\n\n"
            f"{bot_link('file_' + file_id)}"
        ),
        parse_mode="HTML",
    )


# =========================================================
# GET
# =========================================================

async def get_cmd(
    update,
    context,
):

    if len(context.args) != 1:

        await send_text(
            update,
            "Usage: /get <file_id>",
        )

        return

    await deliver_file(
        update,
        context,
        context.args[0],
    )


# =========================================================
# DELIVER FILE
# =========================================================

async def deliver_file(
    update,
    context,
    file_id,
):

    if not await check_fsub(
        update,
        context,
    ):
        return

    row = db.get_file(file_id)

    if not row:

        await send_text(
            update,
            "❌ File not found.",
        )

        return

    user_id = update.effective_user.id

    if db.is_premium(user_id):

        await send_file(
            context,
            update.effective_chat.id,
            row,
        )

        return

    short_url = shortener.create(
        file_id,
        user_id,
    )

    if not short_url:

        await send_text(
            update,
            "⚠️ Shortener is not configured correctly.",
        )

        return

    await send_text(
        update,
        (
            "🔐 <b>Continue to unlock</b>\n\n"
            "Complete the shortener first. "
            "After verification, Telegram will "
            "bring you back to the bot."
        ),
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "🔗 Continue",
                    url=short_url,
                )
            ]
        ]),
    )


# =========================================================
# BATCH
# =========================================================

async def batch_cmd(
    update,
    context,
):

    if not is_admin(
        update.effective_user.id
    ):
        return

    if not context.args:

        await send_text(
            update,
            "Usage: /batch <file_id> <file_id> ...",
        )

        return

    valid = [
        file_id
        for file_id in context.args
        if db.get_file(file_id)
    ]

    if not valid:

        await send_text(
            update,
            "❌ No valid file IDs.",
        )

        return

    batch_id = db.create_batch(valid)

    await send_text(
        update,
        (
            "📦 <b>Batch Created</b>\n\n"
            f"Files: {len(valid)}\n"
            f"🆔 {batch_id}\n\n"
            "🤖 Bot Link:\n"
            f"{bot_link('batch_' + batch_id)}"
        ),
        parse_mode="HTML",
    )


async def deliver_batch(
    update,
    context,
    batch_id,
):

    if not await check_fsub(
        update,
        context,
    ):
        return

    items = db.get_batch_items(
        batch_id
    )

    if not items:

        await send_text(
            update,
            "❌ Batch not found or empty.",
        )

        return

    user_id = update.effective_user.id

    if db.is_premium(user_id):

        await send_batch(
            context,
            update.effective_chat.id,
            items,
        )

        return

    short_url = shortener.create(
        f"batch:{batch_id}",
        user_id,
    )

    if not short_url:

        await send_text(
            update,
            "⚠️ Shortener is not configured correctly.",
        )

        return

    await send_text(
        update,
        (
            f"📦 This batch contains "
            f"<b>{len(items)}</b> files.\n\n"
            "Complete the shortener to unlock."
        ),
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "🔗 Continue",
                    url=short_url,
                )
            ]
        ]),
    )


# =========================================================
# VERIFY
# =========================================================

async def verify_token(
    update,
    context,
    token,
):

    payload = shortener.verify(token)

    if not payload:

        await send_text(
            update,
            "❌ Invalid, expired, or already-used token.",
        )

        return

    token_user_id, target = payload

    current_user_id = (
        update.effective_user.id
    )

    if (
        token_user_id != 0
        and token_user_id != current_user_id
    ):

        await send_text(
            update,
            "❌ This verification link belongs to another user.",
        )

        return

    if target.startswith("batch:"):

        items = db.get_batch_items(
            target[6:]
        )

        if not items:

            await send_text(
                update,
                "❌ Batch not found or empty.",
            )

            return

        await send_batch(
            context,
            update.effective_chat.id,
            items,
        )

        return

    row = db.get_file(target)

    if not row:

        await send_text(
            update,
            "❌ File not found.",
        )

        return

    await send_file(
        context,
        update.effective_chat.id,
        row,
    )


async def verify_cmd(
    update,
    context,
):

    if len(context.args) != 1:

        await send_text(
            update,
            "Usage: /verify <token>",
        )

        return

    await verify_token(
        update,
        context,
        context.args[0],
    )


# =========================================================
# PREMIUM
# =========================================================

async def my_plan_cmd(
    update,
    context,
):

    plan = db.get_premium(
        update.effective_user.id
    )

    if not plan:

        await send_text(
            update,
            (
                "📋 <b>Plan:</b> Free\n\n"
                "No active premium membership."
            ),
            parse_mode="HTML",
        )

        return

    await send_text(
        update,
        (
            "💎 <b>Premium Active</b>\n\n"
            f"👤 User ID: "
            f"{update.effective_user.id}\n"
            f"⏳ Expires: "
            f"{plan['expires_at']}"
        ),
        parse_mode="HTML",
    )


async def add_premium_cmd(
    update,
    context,
):

    if not is_admin(
        update.effective_user.id
    ):
        return

    if len(context.args) != 2:

        await send_text(
            update,
            "Usage: /add_premium <user_id> <days>",
        )

        return

    try:

        user_id = int(
            context.args[0]
        )

        days = int(
            context.args[1]
        )

        db.add_premium(
            user_id,
            days,
        )

        await send_text(
            update,
            (
                f"💎 Premium added for "
                f"{user_id} for {days} days."
            ),
        )

    except ValueError:

        await send_text(
            update,
            "❌ User ID and days must be numbers.",
        )


async def remove_premium_cmd(
    update,
    context,
):

    if not is_admin(
        update.effective_user.id
    ):
        return

    if len(context.args) != 1:

        await send_text(
            update,
            "Usage: /remove_premium <user_id>",
        )

        return

    try:

        db.remove_premium(
            int(context.args[0])
        )

        await send_text(
            update,
            "✅ Premium removed.",
        )

    except ValueError:

        await send_text(
            update,
            "❌ Invalid user ID.",
        )


async def list_premium_cmd(
    update,
    context,
):

    if not is_admin(
        update.effective_user.id
    ):
        return

    rows = db.list_premium()

    if not rows:

        await send_text(
            update,
            "💎 No active premium accounts.",
        )

        return

    text = (
        "💎 <b>Active Premium Users</b>\n\n"
    )

    for row in rows:

        text += (
            f"• {row['user_id']} — "
            f"{row['expires_at']}\n"
        )

    await send_text(
        update,
        text,
        parse_mode="HTML",
    )


# =========================================================
# FSUB COMMANDS
# =========================================================

async def add_fsub_cmd(
    update,
    context,
):

    if not is_admin(
        update.effective_user.id
    ):
        return

    if len(context.args) < 1:

        await send_text(
            update,
            (
                "Usage:\n"
                "/add_fsub <channel_id>\n\n"
                "Optional:\n"
                "/add_fsub <channel_id> <invite_link>"
            ),
        )

        return

    channel_id = context.args[0]

    invite_link = (
        context.args[1]
        if len(context.args) >= 2
        else ""
    )

    try:

        chat = await context.bot.get_chat(
            channel_id
        )

        title = (
            chat.title
            or chat.first_name
            or str(channel_id)
        )

        username = (
            f"https://t.me/{chat.username}"
            if chat.username
            else ""
        )

        if not invite_link:
            invite_link = username

    except TelegramError as e:

        log.warning(
            "Could not get channel information: %s",
            e,
        )

        title = str(channel_id)

    db.add_fsub(
        channel_id,
        invite_link,
        title,
    )

    await send_text(
        update,
        (
            "✅ <b>Force-sub channel added!</b>\n\n"
            f"📢 Channel: <b>{title}</b>\n"
            f"🆔 ID: <code>{channel_id}</code>\n\n"
            "Make sure the bot is an administrator "
            "in the channel."
        ),
        parse_mode="HTML",
    )


async def del_fsub_cmd(
    update,
    context,
):

    if not is_admin(
        update.effective_user.id
    ):
        return

    if len(context.args) != 1:

        await send_text(
            update,
            "Usage: /del_fsub <channel_id>",
        )

        return

    db.del_fsub(
        context.args[0]
    )

    await send_text(
        update,
        "✅ Force-sub channel removed.",
    )


async def fsub_chnl_cmd(
    update,
    context,
):

    if not is_admin(
        update.effective_user.id
    ):
        return

    channels = db.list_fsub()

    if not channels:

        await send_text(
            update,
            "📢 No force-sub channels configured.",
        )

        return

    text = (
        "📢 <b>Force-sub Channels</b>\n\n"
    )

    for channel in channels:

        title = (
            channel.get("title")
            or "Unknown Channel"
        )

        text += (
            f"• 📢 <b>{title}</b>\n"
            f"  ID: <code>"
            f"{channel['channel_id']}"
            f"</code>\n\n"
        )

    await send_text(
        update,
        text,
        parse_mode="HTML",
    )


# =========================================================
# BAN
# =========================================================

async def add_banuser_cmd(
    update,
    context,
):

    if not is_admin(
        update.effective_user.id
    ):
        return

    if len(context.args) != 1:

        await send_text(
            update,
            "Usage: /add_banuser <user_id>",
        )

        return

    try:

        user_id = int(
            context.args[0]
        )

        db.ban_user(
            user_id,
            update.effective_user.id,
        )

        await send_text(
            update,
            f"🚫 User {user_id} banned.",
        )

    except ValueError:

        await send_text(
            update,
            "❌ User ID must be a number.",
        )


async def del_banuser_cmd(
    update,
    context,
):

    if not is_admin(
        update.effective_user.id
    ):
        return

    if len(context.args) != 1:

        await send_text(
            update,
            "Usage: /del_banuser <user_id>",
        )

        return

    try:

        db.unban_user(
            int(context.args[0])
        )

        await send_text(
            update,
            "✅ User unbanned.",
        )

    except ValueError:

        await send_text(
            update,
            "❌ Invalid user ID.",
        )


async def banuser_list_cmd(
    update,
    context,
):

    if not is_admin(
        update.effective_user.id
    ):
        return

    rows = db.list_banned()

    if not rows:

        await send_text(
            update,
            "🚫 No banned users.",
        )

        return

    text = (
        "🚫 <b>Banned Users</b>\n\n"
    )

    for row in rows:

        text += (
            f"• {row['user_id']}\n"
        )

    await send_text(
        update,
        text,
        parse_mode="HTML",
    )


# =========================================================
# ADMIN MANAGEMENT
# =========================================================

async def add_admins_cmd(
    update,
    context,
):

    if not is_owner(
        update.effective_user.id
    ):
        return

    if len(context.args) != 1:

        await send_text(
            update,
            "Usage: /add_admins <user_id>",
        )

        return

    try:

        db.add_admin(
            int(context.args[0])
        )

        await send_text(
            update,
            "✅ Admin added.",
        )

    except ValueError:

        await send_text(
            update,
            "❌ Invalid user ID.",
        )


async def del_admins_cmd(
    update,
    context,
):

    if not is_owner(
        update.effective_user.id
    ):
        return

    if len(context.args) != 1:

        await send_text(
            update,
            "Usage: /del_admins <user_id>",
        )

        return

    try:

        db.remove_admin(
            int(context.args[0])
        )

        await send_text(
            update,
            "✅ Admin removed.",
        )

    except ValueError:

        await send_text(
            update,
            "❌ Invalid user ID.",
        )


async def admin_list_cmd(
    update,
    context,
):

    if not is_owner(
        update.effective_user.id
    ):
        return

    admins = db.list_admins()

    text = (
        f"👑 Owner: "
        f"<code>{OWNER_ID}</code>\n\n"
    )

    if admins:

        text += "\n".join(
            f"• <code>{uid}</code>"
            for uid in admins
        )

    else:

        text += "• No secondary admins"

    await send_text(
        update,
        text,
        parse_mode="HTML",
    )


# =========================================================
# AUTO DELETE COMMAND
# =========================================================

async def auto_del_cmd(
    update,
    context,
):

    if not is_admin(
        update.effective_user.id
    ):
        return

    current = get_delete_seconds()

    if not context.args:

        await send_text(
            update,
            (
                f"⏳ Current auto-delete: "
                f"{current} seconds\n\n"
                "Usage:\n"
                "/auto_del <seconds>\n\n"
                "Example:\n"
                "/auto_del 600"
            ),
        )

        return

    try:

        seconds = int(
            context.args[0]
        )

        if seconds < 0:
            raise ValueError

    except ValueError:

        await send_text(
            update,
            "❌ Seconds must be a valid number.",
        )

        return

    db.set_setting(
        "delete_seconds",
        str(seconds),
    )

    await send_text(
        update,
        (
            f"✅ Auto-delete saved: "
            f"{seconds} seconds."
        ),
    )


# =========================================================
# REQUEST
# =========================================================

async def request_cmd(
    update,
    context,
):

    request_text = (
        " ".join(context.args)
        .strip()
    )

    if not request_text:

        await send_text(
            update,
            "Usage: /request <movie or series name>",
        )

        return

    request_id = db.add_request(
        update.effective_user.id,
        request_text,
    )

    await send_text(
        update,
        (
            "✅ <b>Request submitted!</b>\n\n"
            f"🆔 Request ID: {request_id}"
        ),
        parse_mode="HTML",
    )

    try:

        await context.bot.send_message(
            OWNER_ID,
            (
                f"📩 <b>New Request #{request_id}</b>\n\n"
                f"👤 User: "
                f"{update.effective_user.id}\n"
                f"🎬 Request: {request_text}"
            ),
            parse_mode="HTML",
        )

    except TelegramError:
        pass


# =========================================================
# BROADCAST
# =========================================================

async def broadcast_cmd(
    update,
    context,
):

    if not is_admin(
        update.effective_user.id
    ):
        return

    text = (
        " ".join(context.args)
        .strip()
    )

    if not text:

        await send_text(
            update,
            "Usage: /broadcast <message>",
        )

        return

    users = db.list_users()

    success = 0
    failed = 0

    for user_id in users:

        try:

            await context.bot.send_message(
                user_id,
                text,
            )

            success += 1

        except TelegramError:

            failed += 1

        await asyncio.sleep(0.05)

    await send_text(
        update,
        (
            "📣 <b>Broadcast Complete</b>\n\n"
            f"✅ Sent: {success}\n"
            f"❌ Failed: {failed}"
        ),
        parse_mode="HTML",
    )


async def pbroadcast_cmd(
    update,
    context,
):

    if not is_admin(
        update.effective_user.id
    ):
        return

    text = (
        " ".join(context.args)
        .strip()
    )

    if not text:

        await send_text(
            update,
            "Usage: /pbroadcast <message>",
        )

        return

    users = db.list_users()

    success = 0
    failed = 0

    for user_id in users:

        try:

            msg = await context.bot.send_message(
                user_id,
                text,
            )

            try:

                await context.bot.pin_chat_message(
                    user_id,
                    msg.message_id,
                    disable_notification=True,
                )

            except TelegramError:
                pass

            success += 1

        except TelegramError:

            failed += 1

        await asyncio.sleep(0.05)

    await send_text(
        update,
        (
            "📌 <b>Broadcast Complete</b>\n\n"
            f"✅ Sent: {success}\n"
            f"❌ Failed: {failed}"
        ),
        parse_mode="HTML",
    )


# =========================================================
# USERS
# =========================================================

async def users_cmd(
    update,
    context,
):

    if not is_owner(
        update.effective_user.id
    ):
        return

    await send_text(
        update,
        f"👥 Total users: {db.user_count()}",
    )


# =========================================================
# CANCEL
# =========================================================

async def cancel_cmd(
    update,
    context,
):

    context.user_data.clear()

    await send_text(
        update,
        "✅ All active setup state has been reset.",
    )


# =========================================================
# ALIASES
# =========================================================

async def addsub_cmd(
    update,
    context,
):
    await add_premium_cmd(
        update,
        context,
    )


async def remsub_cmd(
    update,
    context,
):
    await remove_premium_cmd(
        update,
        context,
    )


async def addadmin_cmd(
    update,
    context,
):
    await add_admins_cmd(
        update,
        context,
    )


async def removeadmin_cmd(
    update,
    context,
):
    await del_admins_cmd(
        update,
        context,
    )


async def admins_cmd(
    update,
    context,
):
    await admin_list_cmd(
        update,
        context,
    )


# =========================================================
# STATS
# =========================================================

async def stats_cmd(
    update,
    context,
):

    if not is_admin(
        update.effective_user.id
    ):
        return

    stats = db.stats()

    await send_text(
        update,
        (
            "📊 <b>Statistics</b>\n\n"
            f"📁 Files: {stats['files']}\n"
            f"📦 Batches: {stats['batches']}\n"
            f"👑 Admins: {stats['admins']}\n"
            f"💎 Premium: {stats['premium']}\n"
            f"👥 Users: {stats['users']}\n"
            f"🚫 Banned: {stats['banned']}"
        ),
        parse_mode="HTML",
    )


# =========================================================
# MAIN
# =========================================================

def main():

    threading.Thread(
        target=start_health_server,
        daemon=True,
    ).start()

    app = (
        Application
        .builder()
        .token(BOT_TOKEN)
        .build()
    )

    # -----------------------------------------------------
    # COMMANDS
    # -----------------------------------------------------

    handlers = {

        "start": start,
        "id": id_cmd,
        "help": help_cmd,

        "save": save_cmd,
        "get": get_cmd,
        "verify": verify_cmd,

        "batch": batch_cmd,
        "genlink": genlink_cmd,

        "my_plan": my_plan_cmd,
        "request": request_cmd,

        "settings": settings_cmd,
        "setimage": setimage_cmd,
        "auto_del": auto_del_cmd,

        "fsub_chnl": fsub_chnl_cmd,
        "add_fsub": add_fsub_cmd,
        "del_fsub": del_fsub_cmd,

        "add_banuser": add_banuser_cmd,
        "del_banuser": del_banuser_cmd,
        "banuser_list": banuser_list_cmd,

        "add_premium": add_premium_cmd,
        "remove_premium": remove_premium_cmd,
        "list_premium": list_premium_cmd,

        "add_admins": add_admins_cmd,
        "del_admins": del_admins_cmd,
        "admin_list": admin_list_cmd,

        "broadcast": broadcast_cmd,
        "pbroadcast": pbroadcast_cmd,

        "cancel": cancel_cmd,
        "users": users_cmd,

        "addsub": addsub_cmd,
        "remsub": remsub_cmd,

        "addadmin": addadmin_cmd,
        "removeadmin": removeadmin_cmd,
        "admins": admins_cmd,

        "stats": stats_cmd,
    }

    for command, function in handlers.items():

        app.add_handler(
            CommandHandler(
                command,
                function,
            )
        )

    # -----------------------------------------------------
    # CALLBACKS
    # -----------------------------------------------------

    app.add_handler(
        CallbackQueryHandler(
            fsub_callback,
            pattern=r"^check_fsub$",
        )
    )

    app.add_handler(
        CallbackQueryHandler(
            start_menu_callback,
            pattern=r"^(help|close)$",
        )
    )

    app.add_handler(
        CallbackQueryHandler(
            back_start_callback,
            pattern=r"^back_start$",
        )
    )

    app.add_handler(
        CallbackQueryHandler(
            settings_callback,
            pattern=r"^settings_",
        )
    )

    # -----------------------------------------------------
    # START IMAGE PHOTO HANDLER
    # -----------------------------------------------------

    app.add_handler(
        MessageHandler(
            filters.PHOTO & ~filters.COMMAND,
            receive_start_image,
        )
    )

    log.info(
        "🔥 Bot starting on Render..."
    )

    app.run_polling(
        drop_pending_updates=True
    )


# =========================================================
# RUN
# =========================================================

if __name__ == "__main__":
    main()
