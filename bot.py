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
    ContextTypes,
    CallbackQueryHandler,
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
# ENVIRONMENT VARIABLES
# =========================================================

BOT_TOKEN = os.environ["BOT_TOKEN"]
OWNER_ID = int(os.environ["OWNER_ID"])
DB_CHANNEL_ID = int(os.environ["DB_CHANNEL_ID"])
BOT_USERNAME = os.environ["BOT_USERNAME"].lstrip("@")


# =========================================================
# DATABASE
# =========================================================

db = Database(
    os.getenv("DATABASE_PATH", "bot.db")
)


# =========================================================
# SHORTENER
# =========================================================

shortener = Shortener(
    os.getenv(
        "SHORTENER_API_URL",
        "https://arolinks.com/api"
    ),
    os.getenv(
        "SHORTENER_API_KEY",
        ""
    ),
    BOT_USERNAME,
    db,
)


# =========================================================
# AUTO DELETE
# =========================================================

DEFAULT_DELETE_SECONDS = int(
    os.getenv(
        "DELETE_AFTER_SECONDS",
        "600"
    )
)


# =========================================================
# HEALTH SERVER FOR RENDER
# =========================================================

class HealthHandler(BaseHTTPRequestHandler):

    def do_GET(self):

        self.send_response(200)

        self.send_header(
            "Content-Type",
            "text/plain; charset=utf-8"
        )

        self.end_headers()

        self.wfile.write(
            b"Bot is running!"
        )

    def log_message(
        self,
        fmt,
        *args
    ):
        return


def start_health_server():

    try:

        port = int(
            os.environ.get(
                "PORT",
                "10000"
            )
        )

        server = HTTPServer(
            ("0.0.0.0", port),
            HealthHandler
        )

        log.info(
            "Health server running on port %s",
            port
        )

        server.serve_forever()

    except Exception:

        log.exception(
            "Health server error"
        )


# =========================================================
# PERMISSION HELPERS
# =========================================================

def is_owner(uid):

    return uid == OWNER_ID


def is_admin(uid):

    return (
        uid == OWNER_ID
        or db.is_admin(uid)
    )


def admin_only(update):

    return is_admin(
        update.effective_user.id
    )


def owner_only(update):

    return is_owner(
        update.effective_user.id
    )


# =========================================================
# BOT LINK
# =========================================================

def bot_link(target):

    return (
        f"https://t.me/"
        f"{BOT_USERNAME}"
        f"?start={target}"
    )


# =========================================================
# SAFE REPLY
# =========================================================

async def send_text(
    update,
    text,
    **kwargs
):

    msg = update.effective_message

    if msg:

        return await msg.reply_text(
            text,
            **kwargs
        )


# =========================================================
# DELETE MESSAGES LATER
# =========================================================

async def delete_later(
    context,
    chat_id,
    message_ids,
    seconds
):

    await asyncio.sleep(
        seconds
    )

    if not isinstance(
        message_ids,
        list
    ):

        message_ids = [
            message_ids
        ]

    for message_id in message_ids:

        try:

            await context.bot.delete_message(
                chat_id=chat_id,
                message_id=message_id
            )

        except TelegramError:

            pass


# =========================================================
# COPY STORED FILE
# =========================================================

async def copy_stored_message(
    context,
    chat_id,
    row
):

    return await context.bot.copy_message(
        chat_id=chat_id,
        from_chat_id=row["channel_id"],
        message_id=row["message_id"],
    )


# =========================================================
# SEND FILE
# =========================================================

async def send_file(
    context,
    chat_id,
    row
):

    file_message = await copy_stored_message(
        context,
        chat_id,
        row
    )

    warning = await context.bot.send_message(
        chat_id=chat_id,
        text=(
            "⏳ This file will be automatically "
            f"deleted in {DEFAULT_DELETE_SECONDS // 60} minutes."
        )
    )

    asyncio.create_task(
        delete_later(
            context,
            chat_id,
            [
                file_message.message_id,
                warning.message_id
            ],
            DEFAULT_DELETE_SECONDS
        )
    )


# =========================================================
# SEND BATCH
# =========================================================

async def send_batch(
    context,
    chat_id,
    items
):

    message_ids = []

    for row in items:

        try:

            msg = await copy_stored_message(
                context,
                chat_id,
                row
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
            text="❌ Could not deliver the batch."
        )

        return

    warning = await context.bot.send_message(
        chat_id=chat_id,
        text=(
            f"⏳ These {len(message_ids)} files "
            "will be automatically deleted in "
            f"{DEFAULT_DELETE_SECONDS // 60} minutes."
        )
    )

    message_ids.append(
        warning.message_id
    )

    asyncio.create_task(
        delete_later(
            context,
            chat_id,
            message_ids,
            DEFAULT_DELETE_SECONDS
        )
    )


# =========================================================
# FORCE SUB CHECK
# =========================================================

async def check_fsub(
    update,
    context
):

    channels = db.list_fsub()

    if not channels:

        return True

    uid = update.effective_user.id

    missing = []

    for channel in channels:

        try:

            member = await context.bot.get_chat_member(
                chat_id=channel["channel_id"],
                user_id=uid
            )

            if member.status in (
                "left",
                "kicked"
            ):

                missing.append(
                    channel
                )

        except TelegramError as e:

            log.warning(
                "FSUB check failed for %s: %s",
                channel["channel_id"],
                e
            )

            # If bot cannot check the channel,
            # don't block the user silently.
            continue

    if not missing:

        return True

    buttons = []

    for channel in missing:

        join_url = channel["invite_link"]

        if not join_url:

            username = None

            try:

                chat = await context.bot.get_chat(
                    channel["channel_id"]
                )

                username = getattr(
                    chat,
                    "username",
                    None
                )

            except TelegramError:

                pass

            if username:

                join_url = (
                    f"https://t.me/{username}"
                )

        if join_url:

            title = (
                channel["title"]
                or "Join Channel"
            )

            buttons.append(
                [
                    InlineKeyboardButton(
                        f"📢 {title}",
                        url=join_url
                    )
                ]
            )

    buttons.append(
        [
            InlineKeyboardButton(
                "✅ Check Subscription",
                callback_data="check_fsub"
            )
        ]
    )

    await send_text(
        update,
        (
            "🔒 Please join the required "
            "channel(s) first.\n\n"
            "After joining, press "
            "Check Subscription."
        ),
        reply_markup=InlineKeyboardMarkup(
            buttons
        )
    )

    return False


# =========================================================
# START
# =========================================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    uid = update.effective_user.id

    # Register user
    db.add_user(
        uid,
        update.effective_user.username or "",
        update.effective_user.first_name or ""
    )

    # Check ban
    if db.is_banned(uid):

        await send_text(
            update,
            "🚫 You are blocked from using this bot."
        )

        return

    arg = (
        context.args[0]
        if context.args
        else ""
    )

    # ---------------------------------------------
    # VERIFICATION
    # ---------------------------------------------

    if arg.startswith("verify_"):

        await verify_token(
            update,
            context,
            arg[7:]
        )

        return

    # ---------------------------------------------
    # FSUB
    # ---------------------------------------------

    if not await check_fsub(
        update,
        context
    ):

        return

    # ---------------------------------------------
    # FILE
    # ---------------------------------------------

    if arg.startswith("file_"):

        await deliver_file(
            update,
            context,
            arg[5:]
        )

        return

    # ---------------------------------------------
    # BATCH
    # ---------------------------------------------

    if arg.startswith("batch_"):

        await deliver_batch(
            update,
            context,
            arg[6:]
        )

        return

    # ---------------------------------------------
    # NORMAL START
    # ---------------------------------------------

    await send_text(
        update,
        (
            "👋 Welcome!\n\n"
            "Send a valid file link to receive a file.\n\n"
            "Use /help to see available commands."
        )
    )


# =========================================================
# HELP
# =========================================================

async def help_cmd(
    update,
    context
):

    await send_text(
        update,
        (
            "📚 Commands\n\n"

            "/start - Wake up the bot and load active file links\n"
            "/my_plan - Check your premium membership\n"
            "/request - Submit a movie or series request\n"
            "/auto_del - Adjust auto-delete timer (Admins Only)\n"
            "/fsub_chnl - View force-sub channels (Admins Only)\n"
            "/add_banuser - Ban user (Admins Only)\n"
            "/del_banuser - Unban user (Admins Only)\n"
            "/banuser_list - List banned users (Admins Only)\n"
            "/add_premium - Add premium (Admins Only)\n"
            "/remove_premium - Remove premium (Admins Only)\n"
            "/list_premium - List premium users (Admins Only)\n"
            "/add_fsub - Add force-sub channel (Admins Only)\n"
            "/del_fsub - Remove force-sub channel (Admins Only)\n"
            "/add_admins - Add admin (Owner Only)\n"
            "/del_admins - Remove admin (Owner Only)\n"
            "/admin_list - List admins (Owner Only)\n"
            "/broadcast - Broadcast (Admins Only)\n"
            "/pbroadcast - Broadcast and pin (Admins Only)\n"
            "/batch - Create batch link (Admins Only)\n"
            "/genlink - Create single file link (Admins Only)\n"
            "/cancel - Cancel setup\n"
            "/users - Total users (Owner Only)\n\n"

            "Legacy commands:\n"
            "/save\n"
            "/get\n"
            "/verify\n"
            "/addsub\n"
            "/remsub\n"
            "/addadmin\n"
            "/removeadmin\n"
            "/admins\n"
            "/stats"
        )
    )


# =========================================================
# SAVE
# =========================================================

async def save_cmd(
    update,
    context
):

    if not admin_only(update):

        return

    msg = update.message.reply_to_message

    if not msg:

        await send_text(
            update,
            "Reply to a file/post with /save."
        )

        return

    try:

        copied = await context.bot.copy_message(
            chat_id=DB_CHANNEL_ID,
            from_chat_id=msg.chat_id,
            message_id=msg.message_id
        )

        file_id = db.add_file(
            DB_CHANNEL_ID,
            copied.message_id,
            msg.caption or ""
        )

        link = bot_link(
            f"file_{file_id}"
        )

        share_url = (
            "https://telegram.me/share/url?url="
            +
            quote(
                link,
                safe=""
            )
        )

        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "📤 Share Link",
                        url=share_url
                    )
                ]
            ]
        )

        try:

            await context.bot.edit_message_reply_markup(
                chat_id=DB_CHANNEL_ID,
                message_id=copied.message_id,
                reply_markup=keyboard
            )

        except TelegramError as e:

            log.warning(
                "Could not add share button: %s",
                e
            )

        await send_text(
            update,
            (
                "✅ File saved successfully!\n\n"
                f"🆔 File ID: {file_id}\n\n"
                f"🤖 Bot Link:\n{link}\n\n"
                "📤 Share Link button added to DB channel."
            )
        )

    except Exception as e:

        log.exception(
            "SAVE ERROR"
        )

        await send_text(
            update,
            f"❌ Could not save file:\n{e}"
        )


# =========================================================
# GENLINK
# =========================================================

async def genlink_cmd(
    update,
    context
):

    if not admin_only(update):

        return

    if len(context.args) != 1:

        await send_text(
            update,
            "Usage: /genlink <file_id>"
        )

        return

    file_id = context.args[0]

    if not db.get_file(file_id):

        await send_text(
            update,
            "❌ File not found."
        )

        return

    await send_text(
        update,
        (
            "🔗 Bot Link:\n"
            f"{bot_link('file_' + file_id)}"
        )
    )


# =========================================================
# GET
# =========================================================

async def get_cmd(
    update,
    context
):

    if len(context.args) != 1:

        await send_text(
            update,
            "Usage: /get <file_id>"
        )

        return

    await deliver_file(
        update,
        context,
        context.args[0]
    )


# =========================================================
# DELIVER FILE
# =========================================================

async def deliver_file(
    update,
    context,
    file_id
):

    if not await check_fsub(
        update,
        context
    ):

        return

    row = db.get_file(
        file_id
    )

    if not row:

        await send_text(
            update,
            "❌ File not found."
        )

        return

    uid = update.effective_user.id

    # PREMIUM
    if db.is_premium(uid):

        await send_file(
            context,
            update.effective_chat.id,
            row
        )

        return

    # SHORTENER
    short_url = shortener.create(
        file_id,
        uid
    )

    if not short_url:

        await send_text(
            update,
            "⚠️ Shortener is not configured correctly."
        )

        return

    await send_text(
        update,
        (
            "🔐 Continue to unlock this file.\n\n"
            "Complete the shortener, then "
            "Telegram will bring you back to the bot."
        ),
        reply_markup=InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "🔗 Continue",
                        url=short_url
                    )
                ]
            ]
        )
    )


# =========================================================
# DELIVER BATCH
# =========================================================

async def deliver_batch(
    update,
    context,
    batch_id
):

    if not await check_fsub(
        update,
        context
    ):

        return

    items = db.get_batch_items(
        batch_id
    )

    if not items:

        await send_text(
            update,
            "❌ Batch not found or empty."
        )

        return

    uid = update.effective_user.id

    if db.is_premium(uid):

        await send_batch(
            context,
            update.effective_chat.id,
            items
        )

        return

    short_url = shortener.create(
        f"batch:{batch_id}",
        uid
    )

    if not short_url:

        await send_text(
            update,
            "⚠️ Shortener is not configured correctly."
        )

        return

    await send_text(
        update,
        (
            f"📦 This batch contains {len(items)} files.\n\n"
            "Complete the shortener to unlock the batch."
        ),
        reply_markup=InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "🔗 Continue",
                        url=short_url
                    )
                ]
            ]
        )
    )


# =========================================================
# VERIFY TOKEN
# =========================================================

async def verify_token(
    update,
    context,
    token
):

    payload = shortener.verify(
        token
    )

    if not payload:

        await send_text(
            update,
            "❌ Invalid, expired, or already-used token."
        )

        return

    token_uid, target = payload

    current_uid = (
        update.effective_user.id
    )

    if (
        token_uid != 0
        and token_uid != current_uid
    ):

        await send_text(
            update,
            "❌ This verification link belongs to another user."
        )

        return

    # BATCH
    if target.startswith("batch:"):

        items = db.get_batch_items(
            target[6:]
        )

        if not items:

            await send_text(
                update,
                "❌ Batch not found or empty."
            )

            return

        await send_batch(
            context,
            update.effective_chat.id,
            items
        )

        return

    # FILE
    row = db.get_file(
        target
    )

    if not row:

        await send_text(
            update,
            "❌ File not found."
        )

        return

    await send_file(
        context,
        update.effective_chat.id,
        row
    )


# =========================================================
# VERIFY COMMAND
# =========================================================

async def verify_cmd(
    update,
    context
):

    if len(context.args) != 1:

        await send_text(
            update,
            "Usage: /verify <token>"
        )

        return

    await verify_token(
        update,
        context,
        context.args[0]
    )


# =========================================================
# BATCH
# =========================================================

async def batch_cmd(
    update,
    context
):

    if not admin_only(update):

        return

    if not context.args:

        await send_text(
            update,
            "Usage: /batch <file_id> <file_id> ..."
        )

        return

    valid = []

    for file_id in context.args:

        if db.get_file(file_id):

            valid.append(
                file_id
            )

    if not valid:

        await send_text(
            update,
            "❌ No valid file IDs."
        )

        return

    batch_id = db.create_batch(
        valid
    )

    await send_text(
        update,
        (
            f"📦 Batch created: {len(valid)} files\n\n"
            f"🆔 {batch_id}\n\n"
            "🤖 Bot Link:\n"
            f"{bot_link('batch_' + batch_id)}"
        )
    )


# =========================================================
# MY PLAN
# =========================================================

async def my_plan_cmd(
    update,
    context
):

    uid = update.effective_user.id

    plan = db.get_premium(
        uid
    )

    if not plan:

        await send_text(
            update,
            (
                "📋 Plan: Free\n\n"
                "No active premium membership."
            )
        )

        return

    await send_text(
        update,
        (
            "💎 Plan: Premium\n\n"
            f"👤 User ID: {uid}\n"
            f"⏳ Expires: {plan['expires_at']}"
        )
    )


# =========================================================
# REQUEST
# =========================================================

async def request_cmd(
    update,
    context
):

    text = " ".join(
        context.args
    ).strip()

    if not text:

        await send_text(
            update,
            "Usage: /request <movie or series name>"
        )

        return

    request_id = db.add_request(
        update.effective_user.id,
        text
    )

    await send_text(
        update,
        (
            "✅ Request submitted.\n"
            f"🆔 Request ID: {request_id}"
        )
    )

    try:

        await context.bot.send_message(
            OWNER_ID,
            (
                f"📩 New request #{request_id}\n"
                f"User: {update.effective_user.id}\n"
                f"Request: {text}"
            )
        )

    except TelegramError:

        pass


# =========================================================
# AUTO DELETE
# =========================================================

async def auto_del_cmd(
    update,
    context
):

    if not admin_only(update):

        return

    if not context.args:

        await send_text(
            update,
            (
                f"⏳ Current auto-delete: "
                f"{DEFAULT_DELETE_SECONDS} seconds\n\n"
                "Usage: /auto_del <seconds>"
            )
        )

        return

    try:

        seconds = max(
            30,
            int(context.args[0])
        )

    except ValueError:

        await send_text(
            update,
            "❌ Seconds must be a number."
        )

        return

    db.set_setting(
        "delete_seconds",
        str(seconds)
    )

    await send_text(
        update,
        (
            f"✅ Auto-delete saved: {seconds} seconds.\n\n"
            "⚠️ Restart the bot to apply the new setting."
        )
    )


# =========================================================
# FSUB LIST
# =========================================================

async def fsub_chnl_cmd(
    update,
    context
):

    if not admin_only(update):

        return

    channels = db.list_fsub()

    if not channels:

        await send_text(
            update,
            "📢 No force-sub channels configured."
        )

        return

    text = (
        "📢 Force-sub channels:\n\n"
    )

    for i, channel in enumerate(
        channels,
        1
    ):

        title = (
            channel["title"]
            or "Unknown Channel"
        )

        text += (
            f"{i}. 📢 {title}\n"
            f"   🆔 {channel['channel_id']}\n\n"
        )

    await send_text(
        update,
        text
    )


# =========================================================
# ADD FSUB
# =========================================================
# ONLY CHANNEL ID REQUIRED
# =========================================================

async def add_fsub_cmd(
    update,
    context
):

    if not admin_only(update):

        return

    if len(context.args) != 1:

        await send_text(
            update,
            (
                "Usage:\n"
                "/add_fsub <channel_id>\n\n"
                "Example:\n"
                "/add_fsub -1001234567890"
            )
        )

        return

    channel_id = context.args[0]

    try:

        # ---------------------------------------------
        # FETCH CHANNEL
        # ---------------------------------------------

        chat = await context.bot.get_chat(
            channel_id
        )

        title = (
            chat.title
            or "Unknown Channel"
        )

        username = getattr(
            chat,
            "username",
            None
        )

        # ---------------------------------------------
        # PUBLIC CHANNEL
        # ---------------------------------------------

        if username:

            join_link = (
                f"https://t.me/{username}"
            )

            channel_type = "Public"

        # ---------------------------------------------
        # PRIVATE CHANNEL
        # ---------------------------------------------

        else:

            join_link = None
            channel_type = "Private"

        # ---------------------------------------------
        # SAVE
        # ---------------------------------------------

        db.add_fsub(
            str(channel_id),
            join_link,
            title
        )

        await send_text(
            update,
            (
                "✅ Force-sub channel added!\n\n"
                f"📢 Name: {title}\n"
                f"🆔 ID: {channel_id}\n"
                f"🔗 Type: {channel_type}\n\n"
                "⚠️ Make sure the bot is an admin "
                "in this channel."
            )
        )

    except TelegramError as e:

        log.exception(
            "ADD FSUB ERROR"
        )

        await send_text(
            update,
            (
                "❌ Channel access failed.\n\n"
                "Make sure:\n"
                "• Channel ID is correct\n"
                "• Bot is added to the channel\n"
                "• Bot is an admin in the channel\n\n"
                f"Telegram error: {e}"
            )
        )

    except Exception as e:

        log.exception(
            "ADD FSUB ERROR"
        )

        await send_text(
            update,
            f"❌ Could not add channel:\n{e}"
        )


# =========================================================
# DELETE FSUB
# =========================================================

async def del_fsub_cmd(
    update,
    context
):

    if not admin_only(update):

        return

    if len(context.args) != 1:

        await send_text(
            update,
            "Usage: /del_fsub <channel_id>"
        )

        return

    db.del_fsub(
        context.args[0]
    )

    await send_text(
        update,
        "✅ Force-sub channel removed."
    )


# =========================================================
# BAN USER
# =========================================================

async def add_banuser_cmd(
    update,
    context
):

    if not admin_only(update):

        return

    if len(context.args) != 1:

        await send_text(
            update,
            "Usage: /add_banuser <user_id>"
        )

        return

    try:

        uid = int(
            context.args[0]
        )

        db.ban_user(
            uid,
            update.effective_user.id
        )

        await send_text(
            update,
            f"🚫 User {uid} banned."
        )

    except ValueError:

        await send_text(
            update,
            "❌ User ID must be a number."
        )


# =========================================================
# UNBAN
# =========================================================

async def del_banuser_cmd(
    update,
    context
):

    if not admin_only(update):

        return

    if len(context.args) != 1:

        await send_text(
            update,
            "Usage: /del_banuser <user_id>"
        )

        return

    try:

        uid = int(
            context.args[0]
        )

        db.unban_user(
            uid
        )

        await send_text(
            update,
            "✅ User unbanned."
        )

    except ValueError:

        await send_text(
            update,
            "❌ User ID must be a number."
        )


# =========================================================
# BAN LIST
# =========================================================

async def banuser_list_cmd(
    update,
    context
):

    if not admin_only(update):

        return

    rows = db.list_banned()

    if not rows:

        await send_text(
            update,
            "🚫 No banned users."
        )

        return

    await send_text(
        update,
        (
            "🚫 Banned users:\n\n"
            +
            "\n".join(
                f"• {row['user_id']}"
                for row in rows
            )
        )
    )


# =========================================================
# ADD PREMIUM
# =========================================================

async def add_premium_cmd(
    update,
    context
):

    if not admin_only(update):

        return

    if len(context.args) != 2:

        await send_text(
            update,
            "Usage: /add_premium <user_id> <days>"
        )

        return

    try:

        uid = int(
            context.args[0]
        )

        days = int(
            context.args[1]
        )

        db.add_premium(
            uid,
            days
        )

        await send_text(
            update,
            (
                f"💎 Premium added for {uid}\n"
                f"📅 Days: {days}"
            )
        )

    except ValueError:

        await send_text(
            update,
            "❌ User ID and days must be numbers."
        )


# =========================================================
# REMOVE PREMIUM
# =========================================================

async def remove_premium_cmd(
    update,
    context
):

    if not admin_only(update):

        return

    if len(context.args) != 1:

        await send_text(
            update,
            "Usage: /remove_premium <user_id>"
        )

        return

    try:

        uid = int(
            context.args[0]
        )

        db.remove_premium(
            uid
        )

        await send_text(
            update,
            "✅ Premium removed."
        )

    except ValueError:

        await send_text(
            update,
            "❌ User ID must be a number."
        )


# =========================================================
# PREMIUM LIST
# =========================================================

async def list_premium_cmd(
    update,
    context
):

    if not admin_only(update):

        return

    rows = db.list_premium()

    if not rows:

        await send_text(
            update,
            "💎 No active premium accounts."
        )

        return

    text = "💎 Active premium accounts:\n\n"

    for row in rows:

        text += (
            f"• {row['user_id']}\n"
            f"  ⏳ {row['expires_at']}\n\n"
        )

    await send_text(
        update,
        text
    )


# =========================================================
# ADD ADMIN
# =========================================================

async def add_admins_cmd(
    update,
    context
):

    if not owner_only(update):

        return

    if len(context.args) != 1:

        await send_text(
            update,
            "Usage: /add_admins <user_id>"
        )

        return

    try:

        uid = int(
            context.args[0]
        )

        db.add_admin(
            uid
        )

        await send_text(
            update,
            f"✅ Admin added: {uid}"
        )

    except ValueError:

        await send_text(
            update,
            "❌ User ID must be a number."
        )


# =========================================================
# DELETE ADMIN
# =========================================================

async def del_admins_cmd(
    update,
    context
):

    if not owner_only(update):

        return

    if len(context.args) != 1:

        await send_text(
            update,
            "Usage: /del_admins <user_id>"
        )

        return

    try:

        uid = int(
            context.args[0]
        )

        db.remove_admin(
            uid
        )

        await send_text(
            update,
            "✅ Admin removed."
        )

    except ValueError:

        await send_text(
            update,
            "❌ User ID must be a number."
        )


# =========================================================
# ADMIN LIST
# =========================================================

async def admin_list_cmd(
    update,
    context
):

    if not owner_only(update):

        return

    admins = db.list_admins()

    text = (
        f"👑 Owner: {OWNER_ID}\n\n"
    )

    if admins:

        text += "\n".join(
            f"• {uid}"
            for uid in admins
        )

    else:

        text += "• No secondary admins"

    await send_text(
        update,
        text
    )


# =========================================================
# BROADCAST
# =========================================================

async def broadcast_cmd(
    update,
    context
):

    if not admin_only(update):

        return

    text = " ".join(
        context.args
    ).strip()

    if not text:

        await send_text(
            update,
            "Usage: /broadcast <message>"
        )

        return

    users = db.list_users()

    success = 0
    failed = 0

    for uid in users:

        try:

            await context.bot.send_message(
                chat_id=uid,
                text=text
            )

            success += 1

        except TelegramError:

            failed += 1

        await asyncio.sleep(
            0.05
        )

    await send_text(
        update,
        (
            "📣 Broadcast complete.\n\n"
            f"✅ Sent: {success}\n"
            f"❌ Failed: {failed}"
        )
    )


# =========================================================
# PINNED BROADCAST
# =========================================================

async def pbroadcast_cmd(
    update,
    context
):

    if not admin_only(update):

        return

    text = " ".join(
        context.args
    ).strip()

    if not text:

        await send_text(
            update,
            "Usage: /pbroadcast <message>"
        )

        return

    users = db.list_users()

    success = 0
    failed = 0

    for uid in users:

        try:

            msg = await context.bot.send_message(
                chat_id=uid,
                text=text
            )

            try:

                await context.bot.pin_chat_message(
                    chat_id=uid,
                    message_id=msg.message_id,
                    disable_notification=True
                )

            except TelegramError:

                pass

            success += 1

        except TelegramError:

            failed += 1

        await asyncio.sleep(
            0.05
        )

    await send_text(
        update,
        (
            "📌 Broadcast complete.\n\n"
            f"✅ Sent: {success}\n"
            f"❌ Failed: {failed}"
        )
    )


# =========================================================
# CANCEL
# =========================================================

async def cancel_cmd(
    update,
    context
):

    context.user_data.clear()

    await send_text(
        update,
        "✅ All active setup/configuration state has been reset."
    )


# =========================================================
# USERS
# =========================================================

async def users_cmd(
    update,
    context
):

    if not owner_only(update):

        return

    await send_text(
        update,
        f"👥 Total users: {db.user_count()}"
    )


# =========================================================
# OLD SUB COMMANDS
# =========================================================

async def addsub_cmd(
    update,
    context
):

    if not admin_only(update):

        return

    if len(context.args) != 2:

        await send_text(
            update,
            "Usage: /addsub <user_id> <days>"
        )

        return

    try:

        uid = int(
            context.args[0]
        )

        days = int(
            context.args[1]
        )

        db.add_subscription(
            uid,
            days
        )

        await send_text(
            update,
            "✅ Subscription added."
        )

    except ValueError:

        await send_text(
            update,
            "❌ Invalid numbers."
        )


async def remsub_cmd(
    update,
    context
):

    if not admin_only(update):

        return

    if len(context.args) != 1:

        await send_text(
            update,
            "Usage: /remsub <user_id>"
        )

        return

    try:

        db.remove_subscription(
            int(context.args[0])
        )

        await send_text(
            update,
            "✅ Subscription removed."
        )

    except ValueError:

        await send_text(
            update,
            "❌ Invalid user ID."
        )


async def addadmin_cmd(
    update,
    context
):

    await add_admins_cmd(
        update,
        context
    )


async def removeadmin_cmd(
    update,
    context
):

    await del_admins_cmd(
        update,
        context
    )


async def admins_cmd(
    update,
    context
):

    await admin_list_cmd(
        update,
        context
    )


async def stats_cmd(
    update,
    context
):

    if not admin_only(update):

        return

    stats = db.stats()

    await send_text(
        update,
        (
            "📊 Statistics\n\n"
            f"📁 Files: {stats['files']}\n"
            f"📦 Batches: {stats['batches']}\n"
            f"👑 Admins: {stats['admins']}\n"
            f"💎 Premium: {stats['premium']}\n"
            f"👥 Users: {stats['users']}\n"
            f"🚫 Banned: {stats['banned']}"
        )
    )


# =========================================================
# FSUB CALLBACK
# =========================================================

async def fsub_callback(
    update,
    context
):

    query = update.callback_query

    await query.answer()

    # Delete old FSUB message
    try:

        await query.message.delete()

    except TelegramError:

        pass

    # Check again
    ok = await check_fsub(
        update,
        context
    )

    if ok:

        await context.bot.send_message(
            chat_id=query.message.chat.id,
            text=(
                "✅ Subscription verified!\n\n"
                "Now open your file link again."
            )
        )


# =========================================================
# MAIN
# =========================================================

def main():

    # ---------------------------------------------
    # RENDER HEALTH SERVER
    # ---------------------------------------------

    threading.Thread(
        target=start_health_server,
        daemon=True
    ).start()

    # ---------------------------------------------
    # TELEGRAM APP
    # ---------------------------------------------

    app = (
        Application
        .builder()
        .token(BOT_TOKEN)
        .build()
    )

    # ---------------------------------------------
    # COMMANDS
    # ---------------------------------------------

    handlers = {

        "start": start,
        "help": help_cmd,

        "save": save_cmd,
        "get": get_cmd,
        "verify": verify_cmd,

        "batch": batch_cmd,
        "genlink": genlink_cmd,

        "my_plan": my_plan_cmd,
        "request": request_cmd,
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

        # Legacy
        "addsub": addsub_cmd,
        "remsub": remsub_cmd,
        "addadmin": addadmin_cmd,
        "removeadmin": removeadmin_cmd,
        "admins": admins_cmd,
        "stats": stats_cmd,
    }

    for command, handler in handlers.items():

        app.add_handler(
            CommandHandler(
                command,
                handler
            )
        )

    # ---------------------------------------------
    # FSUB BUTTON CALLBACK
    # ---------------------------------------------

    app.add_handler(
        CallbackQueryHandler(
            fsub_callback,
            pattern="^check_fsub$"
        )
    )

    # ---------------------------------------------
    # START
    # ---------------------------------------------

    log.info(
        "🔥 Bot starting on Render..."
    )

    app.run_polling(
        drop_pending_updates=True
    )


# =========================================================
# ENTRY POINT
# =========================================================

if __name__ == "__main__":

    main()
