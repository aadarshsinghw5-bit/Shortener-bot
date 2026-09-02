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
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)

from database import Database
from shortener import Shortener


# =========================================================
# CONFIG
# =========================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)

log = logging.getLogger(__name__)

BOT_TOKEN = os.environ["BOT_TOKEN"]
OWNER_ID = int(os.environ["OWNER_ID"])
DB_CHANNEL_ID = int(os.environ["DB_CHANNEL_ID"])
BOT_USERNAME = os.environ["BOT_USERNAME"].lstrip("@")

db = Database(
    os.getenv("DATABASE_PATH", "bot.db")
)

shortener = Shortener(
    os.getenv(
        "SHORTENER_API_URL",
        "https://arolinks.com/api"
    ),
    os.getenv("SHORTENER_API_KEY", ""),
    BOT_USERNAME,
    db,
)

DEFAULT_DELETE_SECONDS = int(
    os.getenv(
        "DELETE_AFTER_SECONDS",
        "600"
    )
)


# =========================================================
# HEALTH SERVER
# =========================================================

class HealthHandler(BaseHTTPRequestHandler):

    def do_GET(self):
        self.send_response(200)
        self.send_header(
            "Content-Type",
            "text/plain"
        )
        self.end_headers()
        self.wfile.write(
            b"Bot is running!"
        )

    def log_message(self, *args):
        return


def health_server():

    port = int(
        os.getenv("PORT", "10000")
    )

    server = HTTPServer(
        ("0.0.0.0", port),
        HealthHandler
    )

    log.info(
        "Health server running on %s",
        port
    )

    server.serve_forever()


# =========================================================
# HELPERS
# =========================================================

def is_owner(uid):
    return uid == OWNER_ID


def is_admin(uid):
    return (
        uid == OWNER_ID
        or db.is_admin(uid)
    )


def bot_link(target):
    return (
        f"https://t.me/{BOT_USERNAME}"
        f"?start={target}"
    )


async def reply(update, text, **kwargs):

    msg = update.effective_message

    if msg:
        return await msg.reply_text(
            text,
            **kwargs
        )


# =========================================================
# AUTO DELETE
# =========================================================

def get_delete_seconds():

    try:
        value = db.get_setting(
            "delete_seconds"
        )

        if value:
            return max(
                30,
                int(value)
            )

    except Exception:
        pass

    return DEFAULT_DELETE_SECONDS


async def delete_later(
    context,
    chat_id,
    message_ids,
    seconds
):

    await asyncio.sleep(seconds)

    if not isinstance(
        message_ids,
        list
    ):
        message_ids = [message_ids]

    for mid in message_ids:

        try:
            await context.bot.delete_message(
                chat_id=chat_id,
                message_id=mid
            )

        except TelegramError:
            pass


# =========================================================
# FILE DELIVERY
# =========================================================

async def copy_file(
    context,
    chat_id,
    row
):

    return await context.bot.copy_message(
        chat_id=chat_id,
        from_chat_id=row["channel_id"],
        message_id=row["message_id"]
    )


async def send_file(
    context,
    chat_id,
    row
):

    msg = await copy_file(
        context,
        chat_id,
        row
    )

    seconds = get_delete_seconds()

    warning = await context.bot.send_message(
        chat_id,
        "⏳ This file will be automatically "
        f"deleted in {seconds // 60} minutes."
    )

    asyncio.create_task(
        delete_later(
            context,
            chat_id,
            [
                msg.message_id,
                warning.message_id
            ],
            seconds
        )
    )


async def send_batch(
    context,
    chat_id,
    items
):

    ids = []

    for row in items:

        try:

            msg = await copy_file(
                context,
                chat_id,
                row
            )

            ids.append(
                msg.message_id
            )

        except TelegramError:
            log.exception(
                "Batch delivery failed"
            )

    if not ids:

        await context.bot.send_message(
            chat_id,
            "❌ Could not deliver the batch."
        )

        return

    seconds = get_delete_seconds()

    warning = await context.bot.send_message(
        chat_id,
        f"⏳ These {len(ids)} files will be "
        f"automatically deleted in "
        f"{seconds // 60} minutes."
    )

    asyncio.create_task(
        delete_later(
            context,
            chat_id,
            ids + [
                warning.message_id
            ],
            seconds
        )
    )


# =========================================================
# FORCE SUB
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

    for ch in channels:

        try:

            member = await context.bot.get_chat_member(
                ch["channel_id"],
                uid
            )

            if member.status in (
                "left",
                "kicked"
            ):
                missing.append(ch)

        except TelegramError as e:

            log.warning(
                "FSUB error %s: %s",
                ch["channel_id"],
                e
            )

            # If Telegram cannot check the channel,
            # don't block the user completely.
            continue

    if not missing:
        return True

    buttons = []

    for ch in missing:

        # Prefer username/invite link
        url = (
            ch["username"]
            or ch["invite_link"]
        )

        # IMPORTANT:
        # Button text uses CHANNEL NAME,
        # not channel ID.
        title = (
            ch["title"]
            or "Join Channel"
        )

        if url:

            buttons.append(
                [
                    InlineKeyboardButton(
                        f"📢 {title}",
                        url=url
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

    await reply(
        update,
        "🔒 Please join the required channel(s) "
        "first, then press Check Subscription.",
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

    user = update.effective_user
    uid = user.id

    try:

        db.add_user(
            uid,
            user.username or "",
            user.first_name or ""
        )

    except Exception:
        pass

    if db.is_banned(uid):

        await reply(
            update,
            "🚫 You are blocked from using this bot."
        )

        return

    arg = (
        context.args[0]
        if context.args
        else ""
    )

    # Verification link
    if arg.startswith("verify_"):

        await verify_token(
            update,
            context,
            arg[7:]
        )

        return

    # FSUB
    if not await check_fsub(
        update,
        context
    ):
        return

    # File
    if arg.startswith("file_"):

        await deliver_file(
            update,
            context,
            arg[5:]
        )

        return

    # Batch
    if arg.startswith("batch_"):

        await deliver_batch(
            update,
            context,
            arg[6:]
        )

        return

    await reply(
        update,
        "👋 Welcome!\n\n"
        "Send a valid file link to receive "
        "your file.\n\n"
        "Use /help to see commands."
    )


# =========================================================
# HELP
# =========================================================

async def help_cmd(
    update,
    context
):

    await reply(
        update,
        "📚 Commands\n\n"

        "/start - Wake up the bot\n"
        "/my_plan - Check premium status\n"
        "/request - Request movie/series\n\n"

        "👑 ADMIN COMMANDS\n"
        "/auto_del - Auto-delete settings\n"
        "/fsub_chnl - Force-sub channels\n"
        "/add_banuser - Ban user\n"
        "/del_banuser - Unban user\n"
        "/banuser_list - Banned users\n"
        "/add_premium - Add premium\n"
        "/remove_premium - Remove premium\n"
        "/list_premium - Premium users\n"
        "/add_fsub - Add force-sub channel\n"
        "/del_fsub - Remove force-sub channel\n"
        "/broadcast - Broadcast\n"
        "/pbroadcast - Broadcast + pin\n"
        "/batch - Create batch link\n"
        "/genlink - Create file link\n"
        "/cancel - Cancel setup\n\n"

        "👑 OWNER COMMANDS\n"
        "/add_admins - Add admin\n"
        "/del_admins - Remove admin\n"
        "/admin_list - Admin list\n"
        "/users - Total users\n"
    )


# =========================================================
# SAVE
# =========================================================

async def save_cmd(
    update,
    context
):

    if not is_admin(
        update.effective_user.id
    ):
        return

    msg = update.message.reply_to_message

    if not msg:

        await reply(
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
            + quote(
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
                "Share button error: %s",
                e
            )

        # NO MARKDOWN HERE
        await reply(
            update,
            "✅ File saved successfully!\n\n"
            f"🆔 File ID: {file_id}\n\n"
            f"🤖 Bot Link:\n{link}\n\n"
            "📤 Share Link button added."
        )

    except Exception as e:

        log.exception(
            "SAVE ERROR"
        )

        await reply(
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

    if not is_admin(
        update.effective_user.id
    ):
        return

    if len(context.args) != 1:

        await reply(
            update,
            "Usage: /genlink <file_id>"
        )

        return

    fid = context.args[0]

    if not db.get_file(fid):

        await reply(
            update,
            "❌ File not found."
        )

        return

    await reply(
        update,
        "🔗 Bot Link:\n"
        + bot_link(
            "file_" + fid
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

        await reply(
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

    row = db.get_file(
        file_id
    )

    if not row:

        await reply(
            update,
            "❌ File not found."
        )

        return

    uid = update.effective_user.id

    if not db.is_premium(uid):

        short_url = shortener.create(
            file_id,
            uid
        )

        if not short_url:

            await reply(
                update,
                "⚠️ Shortener is not configured correctly."
            )

            return

        await reply(
            update,
            "🔐 Continue to unlock this file.\n\n"
            "Complete the shortener and you will "
            "automatically return to the bot.",
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

        return

    await send_file(
        context,
        update.effective_chat.id,
        row
    )


# =========================================================
# DELIVER BATCH
# =========================================================

async def deliver_batch(
    update,
    context,
    batch_id
):

    items = db.get_batch_items(
        batch_id
    )

    if not items:

        await reply(
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

        await reply(
            update,
            "⚠️ Shortener is not configured correctly."
        )

        return

    await reply(
        update,
        f"📦 This batch contains "
        f"{len(items)} files.\n\n"
        "Complete the shortener to unlock.",
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
# VERIFY
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

        await reply(
            update,
            "❌ Invalid, expired, or already-used link."
        )

        return

    token_uid, target = payload
    current_uid = update.effective_user.id

    if (
        token_uid != 0
        and token_uid != current_uid
    ):

        await reply(
            update,
            "❌ This verification link belongs "
            "to another user."
        )

        return

    # Batch
    if target.startswith("batch:"):

        items = db.get_batch_items(
            target[6:]
        )

        if not items:

            await reply(
                update,
                "❌ Batch not found."
            )

            return

        await send_batch(
            context,
            update.effective_chat.id,
            items
        )

        return

    # File
    row = db.get_file(
        target
    )

    if not row:

        await reply(
            update,
            "❌ File not found."
        )

        return

    await send_file(
        context,
        update.effective_chat.id,
        row
    )


async def verify_cmd(
    update,
    context
):

    if len(context.args) != 1:

        await reply(
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

    if not is_admin(
        update.effective_user.id
    ):
        return

    if not context.args:

        await reply(
            update,
            "Usage: /batch <file_id> <file_id> ..."
        )

        return

    valid = [
        fid
        for fid in context.args
        if db.get_file(fid)
    ]

    if not valid:

        await reply(
            update,
            "❌ No valid file IDs."
        )

        return

    bid = db.create_batch(
        valid
    )

    await reply(
        update,
        f"📦 Batch created: {len(valid)} files\n\n"
        f"🆔 {bid}\n\n"
        "🤖 Bot Link:\n"
        + bot_link(
            "batch_" + bid
        )
    )


# =========================================================
# PREMIUM
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

        await reply(
            update,
            "📋 Plan: Free\n\n"
            "No active premium membership."
        )

        return

    await reply(
        update,
        "💎 Plan: Premium\n\n"
        f"👤 User ID: {uid}\n"
        f"⏳ Expires: {plan['expires_at']}"
    )


async def add_premium_cmd(
    update,
    context
):

    if not is_admin(
        update.effective_user.id
    ):
        return

    if len(context.args) != 2:

        await reply(
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

        await reply(
            update,
            f"💎 Premium added for {uid} "
            f"for {days} days."
        )

    except ValueError:

        await reply(
            update,
            "❌ User ID and days must be numbers."
        )


async def remove_premium_cmd(
    update,
    context
):

    if not is_admin(
        update.effective_user.id
    ):
        return

    if len(context.args) != 1:

        await reply(
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

        await reply(
            update,
            "✅ Premium removed."
        )

    except ValueError:

        await reply(
            update,
            "❌ Invalid user ID."
        )


async def list_premium_cmd(
    update,
    context
):

    if not is_admin(
        update.effective_user.id
    ):
        return

    rows = db.list_premium()

    if not rows:

        await reply(
            update,
            "💎 No active premium users."
        )

        return

    text = "💎 Active Premium Users\n\n"

    text += "\n".join(
        f"• {r['user_id']} — {r['expires_at']}"
        for r in rows
    )

    await reply(
        update,
        text
    )


# =========================================================
# FSUB
# =========================================================

async def add_fsub_cmd(
    update,
    context
):

    if not is_admin(
        update.effective_user.id
    ):
        return

    if len(context.args) < 1:

        await reply(
            update,
            "Usage:\n"
            "/add_fsub <channel_id>\n\n"
            "Example:\n"
            "/add_fsub -1001234567890"
        )

        return

    channel_id = context.args[0]

    # Optional title
    title = (
        " ".join(
            context.args[1:]
        )
        if len(context.args) > 1
        else ""
    )

    try:

        chat = await context.bot.get_chat(
            channel_id
        )

        # Automatically get channel name
        real_title = (
            chat.title
            or title
            or "Join Channel"
        )

        username = None

        if chat.username:
            username = (
                "https://t.me/"
                + chat.username
            )

        # If private channel, admin can later
        # provide invite link through DB.
        db.add_fsub(
            channel_id,
            username or "",
            real_title
        )

        await reply(
            update,
            "✅ Force-sub channel added.\n\n"
            f"📢 Name: {real_title}\n"
            f"🆔 ID: {channel_id}\n\n"
            "The FSUB button will show the "
            "channel name, not the ID."
        )

    except TelegramError as e:

        await reply(
            update,
            "❌ Could not access this channel.\n\n"
            "Make sure the bot is an admin in "
            "the channel."
        )

        log.warning(
            "FSUB add error: %s",
            e
        )


async def del_fsub_cmd(
    update,
    context
):

    if not is_admin(
        update.effective_user.id
    ):
        return

    if len(context.args) != 1:

        await reply(
            update,
            "Usage: /del_fsub <channel_id>"
        )

        return

    db.del_fsub(
        context.args[0]
    )

    await reply(
        update,
        "✅ Force-sub channel removed."
    )


async def fsub_chnl_cmd(
    update,
    context
):

    if not is_admin(
        update.effective_user.id
    ):
        return

    channels = db.list_fsub()

    if not channels:

        await reply(
            update,
            "📢 No force-sub channels configured."
        )

        return

    text = "📢 Force-sub Channels\n\n"

    for ch in channels:

        text += (
            f"• {ch['title']}\n"
            f"  ID: {ch['channel_id']}\n\n"
        )

    await reply(
        update,
        text
    )


async def fsub_callback(
    update,
    context
):

    query = update.callback_query

    await query.answer()

    try:
        await query.message.delete()
    except TelegramError:
        pass

    if not await check_fsub(
        update,
        context
    ):
        return

    await query.message.chat.send_message(
        "✅ Subscription verified.\n\n"
        "Now open your file link again."
    )


# =========================================================
# AUTO DELETE
# =========================================================

async def auto_del_cmd(
    update,
    context
):

    if not is_admin(
        update.effective_user.id
    ):
        return

    if not context.args:

        current = get_delete_seconds()

        await reply(
            update,
            "⏳ Current auto-delete: "
            f"{current} seconds\n\n"
            "Change it with:\n"
            "/auto_del <seconds>\n\n"
            "Example:\n"
            "/auto_del 300"
        )

        return

    try:

        seconds = max(
            30,
            int(context.args[0])
        )

    except ValueError:

        await reply(
            update,
            "❌ Seconds must be a number."
        )

        return

    db.set_setting(
        "delete_seconds",
        str(seconds)
    )

    await reply(
        update,
        "✅ Auto-delete updated.\n\n"
        f"⏳ New timer: {seconds} seconds"
    )


# =========================================================
# BAN
# =========================================================

async def add_banuser_cmd(
    update,
    context
):

    if not is_admin(
        update.effective_user.id
    ):
        return

    if len(context.args) != 1:

        await reply(
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

        await reply(
            update,
            f"🚫 User {uid} banned."
        )

    except ValueError:

        await reply(
            update,
            "❌ Invalid user ID."
        )


async def del_banuser_cmd(
    update,
    context
):

    if not is_admin(
        update.effective_user.id
    ):
        return

    if len(context.args) != 1:

        await reply(
            update,
            "Usage: /del_banuser <user_id>"
        )

        return

    try:

        db.unban_user(
            int(context.args[0])
        )

        await reply(
            update,
            "✅ User unbanned."
        )

    except ValueError:

        await reply(
            update,
            "❌ Invalid user ID."
        )


async def banuser_list_cmd(
    update,
    context
):

    if not is_admin(
        update.effective_user.id
    ):
        return

    rows = db.list_banned()

    if not rows:

        await reply(
            update,
            "🚫 No banned users."
        )

        return

    await reply(
        update,
        "🚫 Banned Users\n\n"
        + "\n".join(
            f"• {r['user_id']}"
            for r in rows
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

        await reply(
            update,
            "Usage:\n"
            "/request <movie or series name>"
        )

        return

    rid = db.add_request(
        update.effective_user.id,
        text
    )

    await reply(
        update,
        f"✅ Request submitted.\n"
        f"🆔 Request ID: {rid}"
    )

    try:

        await context.bot.send_message(
            OWNER_ID,
            f"📩 New Request #{rid}\n\n"
            f"👤 User: {update.effective_user.id}\n"
            f"🎬 Request: {text}"
        )

    except TelegramError:
        pass


# =========================================================
# ADMINS
# =========================================================

async def add_admins_cmd(
    update,
    context
):

    if not is_owner(
        update.effective_user.id
    ):
        return

    if len(context.args) != 1:

        await reply(
            update,
            "Usage: /add_admins <user_id>"
        )

        return

    try:

        db.add_admin(
            int(context.args[0])
        )

        await reply(
            update,
            "✅ Admin added."
        )

    except ValueError:

        await reply(
            update,
            "❌ Invalid user ID."
        )


async def del_admins_cmd(
    update,
    context
):

    if not is_owner(
        update.effective_user.id
    ):
        return

    if len(context.args) != 1:

        await reply(
            update,
            "Usage: /del_admins <user_id>"
        )

        return

    try:

        db.remove_admin(
            int(context.args[0])
        )

        await reply(
            update,
            "✅ Admin removed."
        )

    except ValueError:

        await reply(
            update,
            "❌ Invalid user ID."
        )


async def admin_list_cmd(
    update,
    context
):

    if not is_owner(
        update.effective_user.id
    ):
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

    await reply(
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

    if not is_admin(
        update.effective_user.id
    ):
        return

    text = " ".join(
        context.args
    ).strip()

    if not text:

        await reply(
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
                uid,
                text
            )

            success += 1

        except TelegramError:

            failed += 1

        await asyncio.sleep(
            0.05
        )

    await reply(
        update,
        "📣 Broadcast Complete\n\n"
        f"✅ Sent: {success}\n"
        f"❌ Failed: {failed}"
    )


async def pbroadcast_cmd(
    update,
    context
):

    if not is_admin(
        update.effective_user.id
    ):
        return

    text = " ".join(
        context.args
    ).strip()

    if not text:

        await reply(
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
                uid,
                text
            )

            try:

                await context.bot.pin_chat_message(
                    uid,
                    msg.message_id,
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

    await reply(
        update,
        "📌 Broadcast Complete\n\n"
        f"✅ Sent: {success}\n"
        f"❌ Failed: {failed}"
    )


# =========================================================
# USERS
# =========================================================

async def users_cmd(
    update,
    context
):

    if not is_owner(
        update.effective_user.id
    ):
        return

    await reply(
        update,
        f"👥 Total users: "
        f"{db.user_count()}"
    )


# =========================================================
# CANCEL
# =========================================================

async def cancel_cmd(
    update,
    context
):

    context.user_data.clear()

    await reply(
        update,
        "✅ All active setup state has been reset."
    )


# =========================================================
# LEGACY COMMANDS
# =========================================================

async def addsub_cmd(
    update,
    context
):

    await add_premium_cmd(
        update,
        context
    )


async def remsub_cmd(
    update,
    context
):

    await remove_premium_cmd(
        update,
        context
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

    if not is_admin(
        update.effective_user.id
    ):
        return

    s = db.stats()

    await reply(
        update,
        "📊 Statistics\n\n"
        f"📁 Files: {s['files']}\n"
        f"📦 Batches: {s['batches']}\n"
        f"👑 Admins: {s['admins']}\n"
        f"💎 Premium: {s['premium']}\n"
        f"👥 Users: {s['users']}\n"
        f"🚫 Banned: {s['banned']}"
    )


# =========================================================
# MAIN
# =========================================================

def main():

    threading.Thread(
        target=health_server,
        daemon=True
    ).start()

    app = (
        Application
        .builder()
        .token(BOT_TOKEN)
        .build()
    )

    commands = {

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

    for name, handler in commands.items():

        app.add_handler(
            CommandHandler(
                name,
                handler
            )
        )

    app.add_handler(
        CallbackQueryHandler(
            fsub_callback,
            pattern="^check_fsub$"
        )
    )

    log.info(
        "🔥 Bot starting on Render..."
    )

    app.run_polling(
        drop_pending_updates=True
    )


# =========================================================
# START
# =========================================================

if __name__ == "__main__":
    main()
