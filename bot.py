import os
import asyncio
import logging
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import quote

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, ContextTypes

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

print("🔥 BOT.PY STARTED", flush=True)


# =========================================================
# ENVIRONMENT VARIABLES
# =========================================================

try:
    BOT_TOKEN = os.environ["BOT_TOKEN"]
    OWNER_ID = int(os.environ["OWNER_ID"])
    DB_CHANNEL_ID = int(os.environ["DB_CHANNEL_ID"])
    BOT_USERNAME = os.environ["BOT_USERNAME"].lstrip("@")

    print("🔥 Environment variables loaded", flush=True)

except Exception as e:
    print("❌ ENVIRONMENT ERROR:", repr(e), flush=True)
    raise


# =========================================================
# DATABASE
# =========================================================

try:
    db = Database(
        os.getenv("DATABASE_PATH", "bot.db")
    )

    print("🔥 Database initialized", flush=True)

except Exception as e:
    print("❌ DATABASE ERROR:", repr(e), flush=True)
    raise


# =========================================================
# SHORTENER
# =========================================================

try:
    shortener = Shortener(
        os.getenv("SHORTENER_API_URL", ""),
        os.getenv("SHORTENER_API_KEY", ""),
        BOT_USERNAME,
        db,
    )

    print("🔥 Shortener initialized", flush=True)

except Exception as e:
    print("❌ SHORTENER INIT ERROR:", repr(e), flush=True)
    raise


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

        print(
            f"🔥 Health server running on port {port}",
            flush=True
        )

        server.serve_forever()

    except Exception as e:

        print(
            "❌ HEALTH SERVER ERROR:",
            repr(e),
            flush=True
        )

        raise


# =========================================================
# ADMIN
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
                "Deleted message %s",
                message_id
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
            "⏳ This file will be automatically "
            "deleted in 10 minutes."
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

    # FILE
    if arg.startswith("file_"):

        await deliver_file(
            update,
            context,
            arg[5:]
        )

        return

    # BATCH
    if arg.startswith("batch_"):

        await deliver_batch(
            update,
            context,
            arg[6:]
        )

        return

    # VERIFY
    if arg.startswith("verify_"):

        await verify_token(
            update,
            context,
            arg[7:]
        )

        return

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
        "/save — save replied file/post (admin)\n"
        "/batch <file_id> ... — create batch (admin)\n"
        "/addsub <user_id> <days> — add subscription\n"
        "/remsub <user_id> — remove subscription\n"
        "/addadmin <user_id> — owner only\n"
        "/removeadmin <user_id> — owner only\n"
        "/admins — list admins\n"
        "/stats — statistics (admin)"
    )


# =========================================================
# SAVE
# =========================================================

async def save_cmd(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not is_admin(update.effective_user.id):
        return

    msg = update.message.reply_to_message

    if not msg:

        await update.message.reply_text(
            "Reply to a file/post with /save."
        )

        return

    try:

        # =================================================
        # COPY POST TO DATABASE CHANNEL
        # =================================================

        copied = await context.bot.copy_message(
            chat_id=DB_CHANNEL_ID,
            from_chat_id=msg.chat_id,
            message_id=msg.message_id
        )

        # =================================================
        # SAVE FILE IN DATABASE
        # =================================================

        file_id = db.add_file(
            DB_CHANNEL_ID,
            copied.message_id,
            msg.caption or ""
        )

        print(
            f"🔥 File saved: {file_id}",
            flush=True
        )

        # =================================================
        # CREATE BOT DEEP LINK
        # =================================================
        #
        # IMPORTANT:
        #
        # Share button contains ONLY the BOT link.
        #
        # User clicks:
        #
        # Telegram Share
        #       ↓
        # Bot
        #       ↓
        # /start file_xxx
        #       ↓
        # AroLinks created
        #       ↓
        # Continue button
        #
        # =================================================

        bot_link = (
            f"https://t.me/"
            f"{BOT_USERNAME}"
            f"?start=file_{file_id}"
        )

        print(
            f"🔥 Bot deep link: {bot_link}",
            flush=True
        )

        # =================================================
        # TELEGRAM SHARE URL
        # =================================================

        share_url = (
            "https://telegram.me/share/url"
            "?url="
            + quote(
                bot_link,
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

        # =================================================
        # ADD SHARE BUTTON TO DB CHANNEL POST
        # =================================================

        try:

            await context.bot.edit_message_reply_markup(
                chat_id=DB_CHANNEL_ID,
                message_id=copied.message_id,
                reply_markup=keyboard
            )

            print(
                "🔥 Share button added to DB post",
                flush=True
            )

        except Exception as e:

            log.warning(
                "Could not add button to DB post: %s",
                e
            )

        # =================================================
        # ADMIN CONFIRMATION
        # =================================================
        #
        # NO MARKDOWN
        #
        # This fixes:
        #
        # BadRequest:
        # Can't parse entities
        #
        # =================================================

        await update.message.reply_text(
            f"✅ File saved successfully!\n\n"
            f"🆔 File ID: {file_id}\n\n"
            f"🤖 Bot Link:\n{bot_link}\n\n"
            f"📤 Share Link button added to DB channel."
        )

    except Exception as e:

        log.exception(
            "SAVE ERROR"
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

    # =================================================
    # SUBSCRIBED USER
    # =================================================

    if db.is_subscribed(uid):

        await send_file_with_auto_delete(
            context,
            chat_id,
            row
        )

        return

    # =================================================
    # NON-SUBSCRIBED USER
    # =================================================

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

    # =================================================
    # SUBSCRIBED USER
    # =================================================

    if db.is_subscribed(uid):

        await send_batch_with_auto_delete(
            context,
            chat_id,
            items
        )

        return

    # =================================================
    # CREATE AROLINKS
    # =================================================

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
        f"📦 This batch contains {len(items)} files.\n\n"
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

    # =================================================
    # PERSONAL TOKEN CHECK
    # =================================================

    if (
        token_user_id != 0
        and token_user_id != current_user_id
    ):

        await update.effective_message.reply_text(
            "❌ This token belongs to another user."
        )

        return

    chat_id = update.effective_chat.id

    # =================================================
    # BATCH
    # =================================================

    if target.startswith("batch:"):

        items = db.get_batch_items(
            target[6:]
        )

        if not items:

            await update.effective_message.reply_text(
                "❌ Batch not found or empty."
            )

            return

        await send_batch_with_auto_delete(
            context,
            chat_id,
            items
        )

        return

    # =================================================
    # FILE
    # =================================================

    row = db.get_file(
        target
    )

    if not row:

        await update.effective_message.reply_text(
            "❌ File not found."
        )

        return

    await send_file_with_auto_delete(
        context,
        chat_id,
        row
    )


# =========================================================
# BATCH COMMAND
# =========================================================

async def batch_cmd(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not is_admin(
        update.effective_user.id
    ):
        return

    if not context.args:

        await update.message.reply_text(
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

        await update.message.reply_text(
            "❌ No valid file IDs."
        )

        return

    try:

        batch_id = db.create_batch(
            valid
        )

        link = (
            f"https://t.me/"
            f"{BOT_USERNAME}"
            f"?start=batch_{batch_id}"
        )

        await update.message.reply_text(
            f"📦 Batch created: {len(valid)} files\n\n"
            f"🆔 {batch_id}\n"
            f"🔗 {link}"
        )

    except Exception as e:

        log.exception(
            "BATCH ERROR"
        )

        await update.message.reply_text(
            f"❌ Could not create batch:\n{e}"
        )


# =========================================================
# ADD SUBSCRIPTION
# =========================================================

async def addsub_cmd(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not is_admin(
        update.effective_user.id
    ):
        return

    if len(context.args) != 2:

        await update.message.reply_text(
            "Usage: /addsub <user_id> <days>"
        )

        return

    try:

        user_id = int(
            context.args[0]
        )

        days = int(
            context.args[1]
        )

        db.add_subscription(
            user_id,
            days
        )

        await update.message.reply_text(
            f"✅ Subscription added.\n\n"
            f"👤 User: {user_id}\n"
            f"📅 Days: {days}"
        )

    except ValueError:

        await update.message.reply_text(
            "❌ User ID and days must be numbers."
        )

    except Exception as e:

        log.exception(
            "ADDSUB ERROR"
        )

        await update.message.reply_text(
            f"❌ Error:\n{e}"
        )


# =========================================================
# REMOVE SUBSCRIPTION
# =========================================================

async def remsub_cmd(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not is_admin(
        update.effective_user.id
    ):
        return

    if len(context.args) != 1:

        await update.message.reply_text(
            "Usage: /remsub <user_id>"
        )

        return

    try:

        user_id = int(
            context.args[0]
        )

        db.remove_subscription(
            user_id
        )

        await update.message.reply_text(
            "✅ Subscription removed."
        )

    except ValueError:

        await update.message.reply_text(
            "❌ User ID must be a number."
        )

    except Exception as e:

        log.exception(
            "REMSUB ERROR"
        )

        await update.message.reply_text(
            f"❌ Error:\n{e}"
        )


# =========================================================
# ADD ADMIN
# =========================================================

async def addadmin_cmd(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not is_owner(
        update.effective_user.id
    ):
        return

    if len(context.args) != 1:

        await update.message.reply_text(
            "Usage: /addadmin <user_id>"
        )

        return

    try:

        user_id = int(
            context.args[0]
        )

        db.add_admin(
            user_id
        )

        await update.message.reply_text(
            f"✅ Admin added: {user_id}"
        )

    except ValueError:

        await update.message.reply_text(
            "❌ User ID must be a number."
        )

    except Exception as e:

        log.exception(
            "ADDADMIN ERROR"
        )

        await update.message.reply_text(
            f"❌ Error:\n{e}"
        )


# =========================================================
# REMOVE ADMIN
# =========================================================

async def removeadmin_cmd(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not is_owner(
        update.effective_user.id
    ):
        return

    if len(context.args) != 1:

        await update.message.reply_text(
            "Usage: /removeadmin <user_id>"
        )

        return

    try:

        user_id = int(
            context.args[0]
        )

        db.remove_admin(
            user_id
        )

        await update.message.reply_text(
            f"✅ Admin removed: {user_id}"
        )

    except ValueError:

        await update.message.reply_text(
            "❌ User ID must be a number."
        )

    except Exception as e:

        log.exception(
            "REMOVEADMIN ERROR"
        )

        await update.message.reply_text(
            f"❌ Error:\n{e}"
        )


# =========================================================
# ADMINS
# =========================================================

async def admins_cmd(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not is_admin(
        update.effective_user.id
    ):
        return

    admins = db.list_admins()

    text = (
        f"👑 Owner: {OWNER_ID}\n\n"
    )

    if admins:

        text += "\n".join(
            f"• {user_id}"
            for user_id in admins
        )

    else:

        text += "• No additional admins"

    await update.message.reply_text(
        text
    )


# =========================================================
# STATS
# =========================================================

async def stats_cmd(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not is_admin(
        update.effective_user.id
    ):
        return

    try:

        stats = db.stats()

        await update.message.reply_text(
            "📊 Statistics\n\n"
            f"📁 Files: {stats['files']}\n"
            f"📦 Batches: {stats['batches']}\n"
            f"👑 Admins: {stats['admins']}\n"
            f"👤 Active subscriptions: "
            f"{stats['subscriptions']}"
        )

    except Exception as e:

        log.exception(
            "STATS ERROR"
        )

        await update.message.reply_text(
            f"❌ Error:\n{e}"
        )


# =========================================================
# MAIN
# =========================================================

def main():

    # =====================================================
    # START RENDER HEALTH SERVER
    # =====================================================

    health_thread = threading.Thread(
        target=start_health_server,
        daemon=True
    )

    health_thread.start()

    # =====================================================
    # CREATE TELEGRAM APPLICATION
    # =====================================================

    app = (
        Application
        .builder()
        .token(BOT_TOKEN)
        .build()
    )

    # =====================================================
    # COMMAND HANDLERS
    # =====================================================

    app.add_handler(
        CommandHandler(
            "start",
            start
        )
    )

    app.add_handler(
        CommandHandler(
            "help",
            help_cmd
        )
    )

    app.add_handler(
        CommandHandler(
            "save",
            save_cmd
        )
    )

    app.add_handler(
        CommandHandler(
            "get",
            get_cmd
        )
    )

    app.add_handler(
        CommandHandler(
            "verify",
            verify_cmd
        )
    )

    app.add_handler(
        CommandHandler(
            "batch",
            batch_cmd
        )
    )

    app.add_handler(
        CommandHandler(
            "addsub",
            addsub_cmd
        )
    )

    app.add_handler(
        CommandHandler(
            "remsub",
            remsub_cmd
        )
    )

    app.add_handler(
        CommandHandler(
            "addadmin",
            addadmin_cmd
        )
    )

    app.add_handler(
        CommandHandler(
            "removeadmin",
            removeadmin_cmd
        )
    )

    app.add_handler(
        CommandHandler(
            "admins",
            admins_cmd
        )
    )

    app.add_handler(
        CommandHandler(
            "stats",
            stats_cmd
        )
    )

    # =====================================================
    # START BOT
    # =====================================================

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
