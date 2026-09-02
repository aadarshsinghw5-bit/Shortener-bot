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


# =========================
# CONFIG
# =========================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)

log = logging.getLogger(__name__)

BOT_TOKEN = os.environ["BOT_TOKEN"]
OWNER_ID = int(os.environ["OWNER_ID"])
DB_CHANNEL_ID = int(os.environ["DB_CHANNEL_ID"])
BOT_USERNAME = os.environ["BOT_USERNAME"].lstrip("@")

db = Database()

shortener = Shortener(
    os.getenv("SHORTENER_API_URL", "https://arolinks.com/api"),
    os.getenv("SHORTENER_API_KEY", ""),
    BOT_USERNAME,
    db,
)

DELETE_SECONDS = int(
    os.getenv("DELETE_AFTER_SECONDS", "600")
)


# =========================
# HEALTH SERVER
# =========================

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
        pass


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


# =========================
# HELPERS
# =========================

def is_owner(uid):
    return uid == OWNER_ID


def is_admin(uid):
    return (
        uid == OWNER_ID
        or db.is_admin(uid)
    )


def link(target):
    return (
        f"https://t.me/"
        f"{BOT_USERNAME}?start={target}"
    )


async def reply(update, text, **kwargs):

    msg = update.effective_message

    if msg:
        return await msg.reply_text(
            text,
            **kwargs
        )


async def delete_later(
    context,
    chat_id,
    ids,
    seconds
):

    await asyncio.sleep(seconds)

    if not isinstance(ids, list):
        ids = [ids]

    for mid in ids:

        try:
            await context.bot.delete_message(
                chat_id,
                mid
            )
        except TelegramError:
            pass


# =========================
# FILE DELIVERY
# =========================

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

    warning = await context.bot.send_message(
        chat_id,
        f"⏳ This file will be deleted "
        f"in {DELETE_SECONDS // 60} minutes."
    )

    asyncio.create_task(
        delete_later(
            context,
            chat_id,
            [
                msg.message_id,
                warning.message_id
            ],
            DELETE_SECONDS
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
                "Batch delivery error"
            )

    if not ids:

        await context.bot.send_message(
            chat_id,
            "❌ Could not deliver batch."
        )

        return

    warning = await context.bot.send_message(
        chat_id,
        f"⏳ These {len(ids)} files "
        f"will be deleted in "
        f"{DELETE_SECONDS // 60} minutes."
    )

    asyncio.create_task(
        delete_later(
            context,
            chat_id,
            ids + [warning.message_id],
            DELETE_SECONDS
        )
    )


# =========================
# FORCE SUB
# =========================

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
                chat_id=ch["channel_id"],
                user_id=uid
            )

            if member.status in (
                "left",
                "kicked"
            ):
                missing.append(ch)

        except TelegramError as e:

            log.warning(
                "FSUB check failed: %s",
                e
            )

    if not missing:
        return True

    buttons = []

    for ch in missing:

        url = (
            ch["invite_link"]
            or ch["username"]
        )

        if url:

            buttons.append(
                [
                    InlineKeyboardButton(
                        f"📢 {ch['title']}",
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
        "🔒 Please join the required channel(s), "
        "then press Check Subscription.",
        reply_markup=InlineKeyboardMarkup(
            buttons
        )
    )

    return False


# =========================
# START
# =========================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user = update.effective_user
    uid = user.id

    db.add_user(
        uid,
        user.username or "",
        user.first_name or ""
    )

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

    if arg.startswith("verify_"):

        await verify_token(
            update,
            context,
            arg[7:]
        )

        return

    if not await check_fsub(
        update,
        context
    ):
        return

    if arg.startswith("file_"):

        await deliver_file(
            update,
            context,
            arg[5:]
        )

        return

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
        "🔗 Send a valid file link to receive a file.\n\n"
        "📚 Use /help for commands."
    )


# =========================
# HELP
# =========================

async def help_cmd(update, context):

    await reply(
        update,
        """
📚 BOT COMMANDS

👤 USER
/start - Start bot
/my_plan - Premium status
/request - Request movie/series
/cancel - Cancel setup

🔐 ADMIN
/auto_del - Auto delete settings
/fsub_chnl - FSUB channels
/add_banuser - Ban user
/del_banuser - Unban user
/banuser_list - Banned users
/add_premium - Add premium
/remove_premium - Remove premium
/list_premium - Premium users
/add_fsub - Add force-sub channel
/del_fsub - Remove force-sub

👑 OWNER
/add_admins - Add admin
/del_admins - Remove admin
/admin_list - Admin list
/users - Total users

📢 BROADCAST
/broadcast - Broadcast
/pbroadcast - Broadcast + pin

📁 FILES
/save - Save replied file
/get - Get file
/genlink - Generate link
/batch - Create batch

Legacy commands:
 /verify /addsub /remsub /addadmin
 /removeadmin /admins /stats
"""
    )


# =========================
# SAVE
# =========================

async def save_cmd(update, context):

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

        fid = db.add_file(
            DB_CHANNEL_ID,
            copied.message_id,
            msg.caption or ""
        )

        bot_url = link(
            "file_" + fid
        )

        share_url = (
            "https://telegram.me/share/url?url="
            + quote(
                bot_url,
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

        await reply(
            update,
            f"✅ File saved!\n\n"
            f"🆔 ID: {fid}\n\n"
            f"🤖 Bot Link:\n{bot_url}"
        )

    except Exception as e:

        log.exception(
            "SAVE ERROR"
        )

        await reply(
            update,
            f"❌ Save error:\n{e}"
        )


# =========================
# GENLINK
# =========================

async def genlink_cmd(update, context):

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
        f"🔗 Bot Link:\n{link('file_' + fid)}"
    )


# =========================
# GET
# =========================

async def get_cmd(update, context):

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


async def deliver_file(
    update,
    context,
    fid
):

    if not await check_fsub(
        update,
        context
    ):
        return

    row = db.get_file(fid)

    if not row:

        await reply(
            update,
            "❌ File not found."
        )

        return

    uid = update.effective_user.id

    if db.is_premium(uid):

        await send_file(
            context,
            update.effective_chat.id,
            row
        )

        return

    short_url = shortener.create(
        fid,
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
        "Complete the shortener and "
        "you will be brought back to the bot.",
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


# =========================
# BATCH
# =========================

async def batch_cmd(update, context):

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
        x for x in context.args
        if db.get_file(x)
    ]

    if not valid:

        await reply(
            update,
            "❌ No valid files."
        )

        return

    bid = db.create_batch(valid)

    await reply(
        update,
        f"📦 Batch created!\n\n"
        f"📁 Files: {len(valid)}\n"
        f"🆔 Batch: {bid}\n\n"
        f"🤖 Bot Link:\n"
        f"{link('batch_' + bid)}"
    )


async def deliver_batch(
    update,
    context,
    bid
):

    if not await check_fsub(
        update,
        context
    ):
        return

    items = db.get_batch_items(bid)

    if not items:

        await reply(
            update,
            "❌ Batch not found."
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
        "batch:" + bid,
        uid
    )

    if not short_url:

        await reply(
            update,
            "⚠️ Shortener error."
        )

        return

    await reply(
        update,
        f"📦 Batch contains {len(items)} files.\n\n"
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


# =========================
# VERIFY
# =========================

async def verify_cmd(update, context):

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


async def verify_token(
    update,
    context,
    token
):

    payload = shortener.verify(token)

    if not payload:

        await reply(
            update,
            "❌ Invalid, expired, or used link."
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
            "❌ This link belongs to another user."
        )

        return

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

    row = db.get_file(target)

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


# =========================
# PREMIUM
# =========================

async def my_plan_cmd(update, context):

    uid = update.effective_user.id
    plan = db.get_premium(uid)

    if not plan:

        await reply(
            update,
            "📋 Plan: Free\n\n"
            "No active premium membership."
        )

        return

    await reply(
        update,
        f"💎 Premium Active\n\n"
        f"👤 User ID: {uid}\n"
        f"⏳ Expires: {plan['expires_at']}"
    )


async def add_premium_cmd(update, context):

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

        uid = int(context.args[0])
        days = int(context.args[1])

        db.add_premium(uid, days)

        await reply(
            update,
            f"💎 Premium added.\n"
            f"👤 {uid}\n"
            f"📅 {days} days"
        )

    except ValueError:

        await reply(
            update,
            "❌ Invalid numbers."
        )


async def remove_premium_cmd(update, context):

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

        db.remove_premium(
            int(context.args[0])
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


async def list_premium_cmd(update, context):

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

    text = "💎 PREMIUM USERS\n\n"

    text += "\n".join(
        f"• {r['user_id']} — {r['expires_at']}"
        for r in rows
    )

    await reply(
        update,
        text
    )


# =========================
# FSUB
# =========================

async def add_fsub_cmd(update, context):

    if not is_admin(
        update.effective_user.id
    ):
        return

    if not context.args:

        await reply(
            update,
            "Usage:\n"
            "/add_fsub <channel_id> [invite_link]"
        )

        return

    channel_id = context.args[0]
    invite = (
        context.args[1]
        if len(context.args) > 1
        else ""
    )

    try:

        chat = await context.bot.get_chat(
            channel_id
        )

        title = chat.title or str(channel_id)

        # Public username
        username = (
            f"https://t.me/{chat.username}"
            if chat.username
            else ""
        )

        db.add_fsub(
            channel_id,
            invite or username,
            title
        )

        await reply(
            update,
            f"✅ FSUB added!\n\n"
            f"📢 Channel: {title}\n"
            f"🆔 ID: {channel_id}\n\n"
            f"Bot will show the channel NAME in the button."
        )

    except TelegramError as e:

        await reply(
            update,
            "❌ Bot cannot access this channel.\n\n"
            "Make sure the bot is an admin there."
        )

        log.warning(
            "FSUB add error: %s",
            e
        )


async def del_fsub_cmd(update, context):

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
        "✅ FSUB channel removed."
    )


async def fsub_chnl_cmd(update, context):

    if not is_admin(
        update.effective_user.id
    ):
        return

    channels = db.list_fsub()

    if not channels:

        await reply(
            update,
            "📢 No FSUB channels."
        )

        return

    text = "📢 FORCE SUB CHANNELS\n\n"

    for ch in channels:

        text += (
            f"• {ch['title']}\n"
            f"  ID: {ch['channel_id']}\n\n"
        )

    await reply(
        update,
        text
    )


async def fsub_callback(update, context):

    query = update.callback_query

    await query.answer()

    try:
        await query.message.delete()
    except TelegramError:
        pass

    if await check_fsub(
        update,
        context
    ):
        await query.message.chat.send_message(
            "✅ Subscription verified!\n\n"
            "Now open your file link again."
        )


# =========================
# BAN
# =========================

async def add_banuser_cmd(update, context):

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

        uid = int(context.args[0])

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


async def del_banuser_cmd(update, context):

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


async def banuser_list_cmd(update, context):

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
        "🚫 BANNED USERS\n\n" +
        "\n".join(
            f"• {r['user_id']}"
            for r in rows
        )
    )


# =========================
# ADMIN
# =========================

async def add_admins_cmd(update, context):

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


async def del_admins_cmd(update, context):

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


async def admin_list_cmd(update, context):

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
            f"• {x}"
            for x in admins
        )

    else:

        text += "• No secondary admins"

    await reply(
        update,
        text
    )


# =========================
# BROADCAST
# =========================

async def broadcast_cmd(update, context):

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

    ok = fail = 0

    for uid in db.list_users():

        try:

            await context.bot.send_message(
                uid,
                text
            )

            ok += 1

        except TelegramError:

            fail += 1

        await asyncio.sleep(0.05)

    await reply(
        update,
        f"📣 Broadcast complete.\n\n"
        f"✅ Sent: {ok}\n"
        f"❌ Failed: {fail}"
    )


async def pbroadcast_cmd(update, context):

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

    ok = fail = 0

    for uid in db.list_users():

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

            ok += 1

        except TelegramError:

            fail += 1

        await asyncio.sleep(0.05)

    await reply(
        update,
        f"📌 Broadcast complete.\n\n"
        f"✅ Sent: {ok}\n"
        f"❌ Failed: {fail}"
    )


# =========================
# REQUEST
# =========================

async def request_cmd(update, context):

    text = " ".join(
        context.args
    ).strip()

    if not text:

        await reply(
            update,
            "Usage: /request <movie or series>"
        )

        return

    rid = db.add_request(
        update.effective_user.id,
        text
    )

    await reply(
        update,
        f"✅ Request submitted!\n"
        f"🆔 Request ID: {rid}"
    )

    try:

        await context.bot.send_message(
            OWNER_ID,
            f"📩 NEW REQUEST\n\n"
            f"🆔 {rid}\n"
            f"👤 {update.effective_user.id}\n"
            f"🎬 {text}"
        )

    except TelegramError:
        pass


# =========================
# SETTINGS
# =========================

async def auto_del_cmd(update, context):

    if not is_admin(
        update.effective_user.id
    ):
        return

    if not context.args:

        current = db.get_setting(
            "delete_seconds",
            str(DELETE_SECONDS)
        )

        await reply(
            update,
            f"⏳ Current: {current} seconds\n\n"
            f"Usage: /auto_del <seconds>"
        )

        return

    try:

        seconds = max(
            30,
            int(context.args[0])
        )

        db.set_setting(
            "delete_seconds",
            str(seconds)
        )

        await reply(
            update,
            f"✅ Auto-delete saved: {seconds} seconds."
        )

    except ValueError:

        await reply(
            update,
            "❌ Seconds must be a number."
        )


async def cancel_cmd(update, context):

    context.user_data.clear()

    await reply(
        update,
        "✅ Setup cancelled."
    )


# =========================
# USERS / STATS
# =========================

async def users_cmd(update, context):

    if not is_owner(
        update.effective_user.id
    ):
        return

    await reply(
        update,
        f"👥 Total users: {db.user_count()}"
    )


async def stats_cmd(update, context):

    if not is_admin(
        update.effective_user.id
    ):
        return

    s = db.stats()

    await reply(
        update,
        f"📊 STATISTICS\n\n"
        f"📁 Files: {s['files']}\n"
        f"📦 Batches: {s['batches']}\n"
        f"👑 Admins: {s['admins']}\n"
        f"💎 Premium: {s['premium']}\n"
        f"👥 Users: {s['users']}\n"
        f"🚫 Banned: {s['banned']}"
    )


# =========================
# LEGACY COMMANDS
# =========================

async def addsub_cmd(update, context):
    await add_premium_cmd(update, context)


async def remsub_cmd(update, context):
    await remove_premium_cmd(update, context)


async def addadmin_cmd(update, context):
    await add_admins_cmd(update, context)


async def removeadmin_cmd(update, context):
    await del_admins_cmd(update, context)


async def admins_cmd(update, context):
    await admin_list_cmd(update, context)


# =========================
# MAIN
# =========================

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
        "add_banuser": add_banuser_cmd,
        "del_banuser": del_banuser_cmd,
        "banuser_list": banuser_list_cmd,
        "add_premium": add_premium_cmd,
        "remove_premium": remove_premium_cmd,
        "list_premium": list_premium_cmd,
        "add_fsub": add_fsub_cmd,
        "del_fsub": del_fsub_cmd,
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

    for name, func in commands.items():

        app.add_handler(
            CommandHandler(
                name,
                func
            )
        )

    app.add_handler(
        CallbackQueryHandler(
            fsub_callback,
            pattern="^check_fsub$"
        )
    )

    log.info(
        "🔥 Bot starting..."
    )

    app.run_polling(
        drop_pending_updates=True
    )


if __name__ == "__main__":
    main()
