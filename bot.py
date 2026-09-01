import os
import asyncio
import logging
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import quote

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, ContextTypes
from telegram.constants import ParseMode

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
# DATABASE + SHORTENER
# =========================================================

db = Database(
    os.getenv("DATABASE_PATH", "bot.db")
)

shortener = Shortener(
    os.getenv("SHORTENER_API_URL", ""),
    os.getenv("SHORTENER_API_KEY", ""),
    BOT_USERNAME,
    db,
)


# =========================================================
# SETTINGS
# =========================================================

DELETE_AFTER_SECONDS = 600


# =========================================================
# RENDER HEALTH SERVER
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

    def log_message(self, format, *args):
        return


def start_health_server():

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


# =========================================================
# ADMIN CHECKS
# =========================================================

def is_admin(user_id: int) -> bool:
    return (
        user_id == OWNER_ID
        or db.is_admin(user_id)
    )


def is_owner(user_id: int) -> bool:
    return user_id == OWNER_ID


# =========================================================
# COPY STORED MESSAGE
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
# DELETE AFTER 10 MINUTES
# =========================================================

async def delete_after_10_minutes(
    context,
    chat_id,
    file_message_ids,
    warning_message_id
):

    await asyncio.sleep(
        DELETE_AFTER_SECONDS
    )

    if not isinstance(
        file_message_ids,
        list
    ):
        file_message_ids = [
            file_message_ids
        ]

    message_ids = (
        file_message_ids
        + [warning_message_id]
    )

    for message_id in message_ids:

        try:

            await context.bot.delete_message(
                chat_id=chat_id,
                message_id=message_id
            )

            log.info(
                "Deleted message %s from chat %s",
                message_id,
                chat_id
            )

        except Exception as e:

            log.warning(
                "Could not delete message %s: %s",
                message_id,
                e
            )


# =========================================================
# SEND FILE + AUTO DELETE
# =========================================================

async def send_file_with_auto_delete(
    context,
    chat_id,
    row
):

    file_message = await copy_stored_message(
        context,
        chat_id,
        row
    )

    warning_message = await context.bot.send_message(
        chat_id=chat_id,
        text=(
            "⏳ This file will be "
            "automatically deleted in 10 minutes."
        )
    )

    asyncio.create_task(
        delete_after_10_minutes(
            context,
            chat_id,
            file_message.message_id,
            warning_message.message_id
        )
    )


# =========================================================
# SEND BATCH + AUTO DELETE
# =========================================================

async def send_batch_with_auto_delete(
    context,
    chat_id,
    items
):

    file_message_ids = []

    for row in items:

        try:

            file_message = await copy_stored_message(
                context,
                chat_id,
                row
            )

            file_message_ids.append(
                file_message.message_id
            )

        except Exception:

            log.exception(
                "Batch delivery failed"
            )

    if not file_message_ids:

        await context.bot.send_message(
            chat_id=chat_id,
            text="❌ Could not deliver the batch."
        )

        return

    warning_message = await context.bot.send_message(
        chat_id=chat_id,
        text=(
            f"⏳ These {len(file_message_ids)} files "
            "will be automatically deleted in 10 minutes."
        )
    )

    asyncio.create_task(
        delete_after_10_minutes(
            context,
            chat_id,
            file_message_ids,
            warning_message.message_id
        )
    )


# =========================================================
# START
# =========================================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    arg = (
        context.args[0]
        if context.args
        else ""
    )

    # FILE LINK
    if arg.startswith("file_"):

        await deliver_file(
            update,
            context,
            arg[5:]
        )

        return

    # BATCH LINK
    if arg.startswith("batch_"):

        await deliver_batch(
            update,
            context,
            arg[6:]
        )

        return

    # VERIFY LINK
    if arg.startswith("verify_"):

        await verify_token(
            update,
            context,
            arg[7:]
        )

        return

    # NORMAL START
    await update.message.reply_text(
        "👋 Welcome!\n\n"
        "Use a file/batch link to receive files.\n\n"
        "Use /help to see commands."
    )


# =========================================================
# HELP
# =========================================================

async def help_cmd(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    await update.message.reply_text(
        "📚 Commands\n\n"
        "/get <file_id> — get a file\n"
        "/batch <file_id> ... — create a batch (admin)\n"
        "/save — save a replied file/post (admin)\n"
        "/addsub <user_id> <days> — add subscription\n"
        "/remsub <user_id> — remove subscription\n"
        "/addadmin <user_id> — owner only\n"
        "/removeadmin <user_id> — owner only\n"
        "/admins — list admins\n"
        "/stats — statistics (admin)\n\n"
        "To store a file/post, reply to it with /save."
    )


# =========================================================
# SAVE
# =========================================================

async def save_cmd(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not is_admin(
        update.effective_user.id
    ):
        return

    msg = update.message.reply_to_message

    if not msg:

        await update.message.reply_text(
            "Reply to a file/post with /save."
        )

        return

    try:

        # Copy original post to DB channel
        copied = await context.bot.copy_message(
            chat_id=DB_CHANNEL_ID,
            from_chat_id=msg.chat_id,
            message_id=msg.message_id
        )

        # Save in database
        file_id = db.add_file(
            DB_CHANNEL_ID,
            copied.message_id,
            msg.caption or ""
        )

        log.info(
            "File saved: %s",
            file_id
        )

        # Create SHAREABLE shortener token
        # user_id=0 means token can be used by anyone
        short_url = shortener.create(
            file_id=file_id,
            user_id=0
        )

        if not short_url:

            await update.message.reply_text(
                f"⚠️ File saved.\n\n"
                f"🆔 {file_id}\n\n"
                "❌ AroLinks shortener link "
                "could not be created."
            )

            return

        # Telegram Share URL
        share_url = (
            "https://telegram.me/share/url?url="
            + quote(
                short_url,
                safe=""
            )
        )

        # Button
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

        # Put button below DB channel post
        await context.bot.edit_message_reply_markup(
            chat_id=DB_CHANNEL_ID,
            message_id=copied.message_id,
            reply_markup=keyboard
        )

        # Normal direct file link
        direct_link = (
            f"https://t.me/"
            f"{BOT_USERNAME}"
            f"?start=file_{file_id}"
        )

        await update.message.reply_text(
            f"✅ File saved successfully!\n\n"
            f"🆔 {file_id}\n"
            f"🔗 {direct_link}\n\n"
            "📤 Share Link button added "
            "to DB channel."
        )

    except Exception as e:

        log.exception(
            "save failed"
        )

        await update.message.reply_text(
            f"❌ Could not save file:\n{e}"
        )


# =========================================================
# GET
# =========================================================

async def get_cmd(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if len(context.args) != 1:

        await update.message.reply_text(
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
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    file_id: str
):

    row = db.get_file(
        file_id
    )

    if not row:

        await update.effective_message.reply_text(
            "❌ File not found."
        )

        return

    uid = update.effective_user.id
    chat_id = update.effective_chat.id

    # SUBSCRIBED USER
    if db.is_subscribed(uid):

        await send_file_with_auto_delete(
            context,
            chat_id,
            row
        )

        return

    # NON-SUBSCRIBED USER
    short_url = shortener.create(
        file_id=file_id,
        user_id=uid
    )

    if not short_url:

        await update.effective_message.reply_text(
            "⚠️ Shortener is not configured correctly."
        )

        return

    await update.effective_message.reply_text(
        "🔐 You are not subscribed.\n\n"
        "Complete the shortener first, then open "
        "the verification link to receive the file.",
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
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    batch_id: str
):

    items = db.get_batch_items(
        batch_id
    )

    if not items:

        await update.effective_message.reply_text(
            "❌ Batch not found or empty."
        )

        return

    uid = update.effective_user.id
    chat_id = update.effective_chat.id

    # SUBSCRIBED USER
    if db.is_subscribed(uid):

        await send_batch_with_auto_delete(
            context,
            chat_id,
            items
        )

        return

    # NON-SUBSCRIBED USER
    short_url = shortener.create(
        file_id=f"batch:{batch_id}",
        user_id=uid
    )

    if not short_url:

        await update.effective_message.reply_text(
            "⚠️ Shortener is not configured correctly."
        )

        return

    await update.effective_message.reply_text(
        f"📦 This batch contains "
        f"{len(items)} files.\n\n"
        "Complete the shortener to unlock the batch.",
        reply_markup=InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "🔗 Unlock Batch",
                        url=short_url
                    )
                ]
            ]
        )
    )


# =========================================================
# VERIFY COMMAND
# =========================================================

async def verify_cmd(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if len(context.args) != 1:

        await update.message.reply_text(
            "Usage: /verify <token>"
        )

        return

    await verify_token(
        update,
        context,
        context.args[0]
    )


# =========================================================
# VERIFY TOKEN
# =========================================================

async def verify_token(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    token: str
):

    payload = shortener.verify(
        token
    )

    if not payload:

        await update.effective_message.reply_text(
            "❌ Invalid or expired token."
        )

        return

    token_user_id, target = payload

    current_user_id = (
        update.effective_user.id
    )

    # user_id=0 =
