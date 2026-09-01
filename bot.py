import os
import asyncio
import logging
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import quote

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)
from telegram.constants import ParseMode

from database import Database
from shortener import Shortener


# =========================================================
# LOGGING
# =========================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
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
# DELETE FILE AFTER 10 MINUTES
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
# SEND SINGLE FILE
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
# SEND BATCH
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
# START COMMAND
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

    # ---------------------------------------------
    # FILE LINK
    # ---------------------------------------------

    if arg.startswith("file_"):

        await deliver_file(

            update,

            context,

            arg[5:]
        )

        return

    # ---------------------------------------------
    # BATCH LINK
    # ---------------------------------------------

    if arg.startswith("batch_"):

        await deliver_batch(

            update,

            context,

            arg[6:]
        )

        return

    # ---------------------------------------------
    # VERIFY LINK
    # ---------------------------------------------

    if arg.startswith("verify_"):

        await verify_cmd(

            update,

            context,

            arg[7:]
        )

        return

    # ---------------------------------------------
    # NORMAL START
    # ---------------------------------------------

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
   
