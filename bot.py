import os
import asyncio
import logging
import threading
from datetime import datetime, timedelta, timezone
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

db = Database()

shortener = Shortener(
    os.getenv(
        "SHORTENER_API_URL",
        "https://arolinks.com/api",
    ),
    os.getenv("SHORTENER_API_KEY", ""),
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
        self.wfile.write(b"Bot is running!")

    def log_message(self, *args):
        pass


def start_health_server():

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


async def reply(update, text, **kwargs):

    msg = update.effective_message

    if msg:
        return await msg.reply_text(
            text,
            **kwargs,
        )


def bot_link(target):
    return (
        f"https://t.me/"
        f"{BOT_USERNAME}"
        f"?start={target}"
    )


def minutes_text(seconds):

    if seconds <= 0:
        return "OFF"

    if seconds % 3600 == 0:
        return f"{seconds // 3600} hour(s)"

    if seconds % 60 == 0:
        return f"{seconds // 60} minute(s)"

    return f"{seconds} seconds"


# =========================================================
# AUTO DELETE
# =========================================================

async def delete_message_later(
    context,
    delete_id,
    chat_id,
    message_id,
    seconds,
):

    await asyncio.sleep(
        max(0, seconds)
    )

    try:

        await context.bot.delete_message(
            chat_id=chat_id,
            message_id=message_id,
        )

    except TelegramError:
        pass

    try:
        db.mark_delete_done(
            delete_id
        )
    except Exception:
        log.exception(
            "Could not mark delete as completed"
        )


def schedule_delete(
    context,
    chat_id,
    message_id,
    seconds,
):

    if seconds <= 0:
        return

    delete_at = (
        datetime.now(timezone.utc)
        + timedelta(seconds=seconds)
    )

    delete_id = db.add_pending_delete(
        chat_id,
        message_id,
        delete_at,
    )

    asyncio.create_task(
        delete_message_later(
            context,
            delete_id,
            chat_id,
            message_id,
            seconds,
        )
    )


async def recover_pending_deletes(
    application,
):

    try:

        rows = db.get_pending_deletes()

        now = datetime.now(timezone.utc)

        for row in rows:

            try:

                delete_at = datetime.fromisoformat(
                    row["delete_at"].replace(
                        "Z",
                        "+00:00",
                    )
                )

                seconds = int(
                    (
                        delete_at - now
                    ).total_seconds()
                )

                asyncio.create_task(
                    delete_message_later(
                        application,
                        row["id"],
                        row["chat_id"],
                        row["message_id"],
                        max(0, seconds),
                    )
                )

            except Exception:

                log.exception(
                    "Pending delete recovery failed"
                )

    except Exception:

        log.exception(
            "Could not recover pending deletes"
        )


# =========================================================
# FILE DELIVERY
# =========================================================

async def copy_file(
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

    file_message = await copy_file(
        context,
        chat_id,
        row,
    )

    seconds = db.get_delete_seconds()

    if seconds <= 0:
        return

    warning = await context.bot.send_message(
        chat_id=chat_id,
        text=(
            "⏳ This file will be automatically "
            f"deleted in {minutes_text(seconds)}."
        ),
    )

    schedule_delete(
        context,
        chat_id,
        file_message.message_id,
        seconds,
    )

    schedule_delete(
        context,
        chat_id,
        warning.message_id,
        seconds,
    )


async def send_batch(
    context,
    chat_id,
    items,
):

    ids = []

    for row in items:

        try:

            msg = await copy_file(
                context,
                chat_id,
                row,
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
            chat_id=chat_id,
            text="❌ Could not deliver the batch.",
        )

        return

    seconds = db.get_delete_seconds()

    if seconds <= 0:
        return

    warning = await context.bot.send_message(
        chat_id=chat_id,
        text=(
            f"⏳ These {len(ids)} files will be "
            f"automatically deleted in "
            f"{minutes_text(seconds)}."
        ),
    )

    for message_id in ids:

        schedule_delete(
            context,
            chat_id,
            message_id,
            seconds,
        )

    schedule_delete(
        context,
        chat_id,
        warning.message_id,
        seconds,
    )


# =========================================================
# FSUB
# =========================================================

async def check_fsub(
    update,
    context,
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
                user_id=uid,
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

            # If bot cannot check the channel,
            # don't block everyone.
            continue

    if not missing:
        return True

    buttons = []

    for channel in missing:

        url = (
            channel.get("invite_link")
            or channel.get("username")
        )

        if not url:
            continue

        # IMPORTANT:
        # Button shows channel TITLE,
        # not channel ID.
        title = (
            channel.get("title")
            or "Join Channel"
        )

        buttons.append(
            [
                InlineKeyboardButton(
                    f"📢 {title}",
                    url=url,
                )
            ]
        )

    buttons.append(
        [
            InlineKeyboardButton(
                "✅ Check Subscription",
                callback_data="check_fsub",
            )
        ]
    )

    await reply(
        update,
        "🔒 Please join the required channel(s) first.",
        reply_markup=InlineKeyboardMarkup(
            buttons
        ),
    )

    return False


# =========================================================
# START
# =========================================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    user = update.effective_user

    uid = user.id

    db.add_user(
        uid,
        user.username or "",
        user.first_name or "",
    )

    if db.is_banned(uid):

        await reply(
            update,
            "🚫 You are blocked from using this bot.",
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

    await reply(
        update,
        "👋 Welcome!\n\n"
        "Send a valid file link to receive your file.\n\n"
        "Use /help to see commands.",
    )


# =========================================================
# HELP
# =========================================================

async def help_cmd(
    update,
    context,
):

    await reply(
        update,
        "📚 Commands\n\n"

        "/start - Start bot\n"
        "/my_plan - Premium status\n"
        "/request <name> - Movie/series request\n\n"

        "⚙️ ADMIN\n"
        "/auto_del - Auto-delete settings\n"
        "/fsub_chnl - Force-sub channels\n"
        "/add_banuser <id> - Ban user\n"
        "/del_banuser <id> - Unban user\n"
        "/banuser_list - Banned users\n"
        "/add_premium <id> <days> - Add premium\n"
        "/remove_premium <id> - Remove premium\n"
        "/list_premium - Premium users\n"
        "/add_fsub <id> - Add force-sub channel\n"
        "/del_fsub <id> - Remove force-sub\n"
        "/broadcast <text> - Broadcast\n"
        "/pbroadcast <text> - Broadcast + pin\n"
        "/batch <file_id> ... - Create batch\n"
        "/genlink <file_id> - Generate bot link\n\n"

        "👑 OWNER\n"
        "/add_admins <id>\n"
        "/del_admins <id>\n"
        "/admin_list\n"
        "/users\n\n"

        "/cancel - Reset setup",
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

        await reply(
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
            + quote(
                link,
                safe="",
            )
        )

        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "📤 Share Link",
                        url=share_url,
                    )
                ]
            ]
        )

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

        await reply(
            update,
            "✅ File saved successfully!\n\n"
            f"🆔 File ID: {file_id}\n\n"
            f"🤖 Bot Link:\n{link}",
        )

    except Exception as e:

        log.exception(
            "SAVE ERROR"
        )

        await reply(
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

        await reply(
            update,
            "Usage: /genlink <file_id>",
        )

        return

    fid = context.args[0]

    if not db.get_file(fid):

        await reply(
            update,
            "❌ File not found.",
        )

        return

    await reply(
        update,
        "🔗 Bot Link:\n"
        + bot_link(
            f"file_{fid}"
        ),
    )


# =========================================================
# GET
# =========================================================

async def get_cmd(
    update,
    context,
):

    if len(context.args) != 1:

        await reply(
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

    row = db.get_file(
        file_id
    )

    if not row:

        await reply(
            update,
            "❌ File not found.",
        )

        return

    uid = update.effective_user.id

    if db.is_premium(uid):

        await send_file(
            context,
            update.effective_chat.id,
            row,
        )

        return

    short_url = shortener.create(
        file_id,
        uid,
    )

    if not short_url:

        await reply(
            update,
            "⚠️ Shortener is not configured correctly.",
        )

        return

    await reply(
        update,
        "🔐 Continue to unlock this file.\n\n"
        "Complete the shortener and "
        "you will be returned to the bot.",
        reply_markup=InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "🔗 Continue",
                        url=short_url,
                    )
                ]
            ]
        ),
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

        await reply(
            update,
            "Usage: /batch <file_id> <file_id> ...",
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
            "❌ No valid file IDs.",
        )

        return

    bid = db.create_batch(
        valid
    )

    await reply(
        update,
        f"📦 Batch created: {len(valid)} files\n\n"
        f"🆔 {bid}\n\n"
        f"🤖 Bot Link:\n"
        f"{bot_link('batch_' + bid)}",
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

        await reply(
            update,
            "❌ Batch not found or empty.",
        )

        return

    uid = update.effective_user.id

    if db.is_premium(uid):

        await send_batch(
            context,
            update.effective_chat.id,
            items,
        )

        return

    short_url = shortener.create(
        f"batch:{batch_id}",
        uid,
    )

    if not short_url:

        await reply(
            update,
            "⚠️ Shortener is not configured correctly.",
        )

        return

    await reply(
        update,
        f"📦 This batch contains {len(items)} files.\n\n"
        "Complete the shortener to unlock the batch.",
        reply_markup=InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "🔗 Continue",
                        url=short_url,
                    )
                ]
            ]
        ),
    )


# =========================================================
# VERIFY
# =========================================================

async def verify_cmd(
    update,
    context,
):

    if len(context.args) != 1:

        await reply(
            update,
            "Usage: /verify <token>",
        )

        return

    await verify_token(
        update,
        context,
        context.args[0],
    )


async def verify_token(
    update,
    context,
    token,
):

    payload = shortener.verify(
        token
    )

    if not payload:

        await reply(
            update,
            "❌ Invalid, expired, or already-used token.",
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

        await reply(
            update,
            "❌ This verification link belongs to another user.",
        )

        return

    if not await check_fsub(
        update,
        context,
    ):
        return

    if target.startswith("batch:"):

        items = db.get_batch_items(
            target[6:]
        )

        if not items:

            await reply(
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

    row = db.get_file(
        target
    )

    if not row:

        await reply(
            update,
            "❌ File not found.",
        )

        return

    await send_file(
        context,
        update.effective_chat.id,
        row,
    )


# =========================================================
# PREMIUM
# =========================================================

async def my_plan_cmd(
    update,
    context,
):

    uid = update.effective_user.id

    plan = db.get_premium(
        uid
    )

    if not plan:

        await reply(
            update,
            "📋 Plan: Free\n\n"
            "No active premium membership.",
        )

        return

    await reply(
        update,
        "💎 Plan: Premium\n\n"
        f"👤 User ID: {uid}\n"
        f"⏳ Expires: {plan['expires_at']}",
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

        await reply(
            update,
            "Usage: /add_premium <user_id> <days>",
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
            days,
        )

        await reply(
            update,
            f"💎 Premium added for {uid} "
            f"for {days} days.",
        )

    except ValueError:

        await reply(
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

        await reply(
            update,
            "Usage: /remove_premium <user_id>",
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
            "✅ Premium removed.",
        )

    except ValueError:

        await reply(
            update,
            "❌ User ID must be a number.",
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

        await reply(
            update,
            "💎 No active premium accounts.",
        )

        return

    text = "💎 Active Premium:\n\n"

    text += "\n".join(
        f"• {r['user_id']} — {r['expires_at']}"
        for r in rows
    )

    await reply(
        update,
        text,
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

    if not context.args:

        seconds = db.get_delete_seconds()

        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "5 Minutes",
                        callback_data="del_300",
                    ),
                    InlineKeyboardButton(
                        "10 Minutes",
                        callback_data="del_600",
                    ),
                ],
                [
                    InlineKeyboardButton(
                        "15 Minutes",
                        callback_data="del_900",
                    ),
                    InlineKeyboardButton(
                        "30 Minutes",
                        callback_data="del_1800",
                    ),
                ],
                [
                    InlineKeyboardButton(
                        "1 Hour",
                        callback_data="del_3600",
                    ),
                    InlineKeyboardButton(
                        "OFF",
                        callback_data="del_0",
                    ),
                ],
            ]
        )

        await reply(
            update,
            "⚙️ Auto Delete Settings\n\n"
            f"Current: {minutes_text(seconds)}\n\n"
            "Choose a new timer:",
            reply_markup=keyboard,
        )

        return

    try:

        seconds = int(
            context.args[0]
        )

        if seconds < 0:
            raise ValueError

        db.set_setting(
            "delete_seconds",
            seconds,
        )

        await reply(
            update,
            "✅ Auto-delete saved.\n\n"
            f"New setting: {minutes_text(seconds)}",
        )

    except ValueError:

        await reply(
            update,
            "❌ Use seconds.\n\n"
            "Example:\n"
            "/auto_del 600\n"
            "/auto_del 300\n"
            "/auto_del 0",
        )


async def auto_delete_callback(
    update,
    context,
):

    query = update.callback_query

    await query.answer()

    if not is_admin(
        query.from_user.id
    ):
        return

    seconds = int(
        query.data.split("_")[1]
    )

    db.set_setting(
        "delete_seconds",
        seconds,
    )

    try:

        await query.edit_message_text(
            "⚙️ Auto Delete Settings\n\n"
            "✅ Setting saved!\n\n"
            f"Current: {minutes_text(seconds)}",
        )

    except TelegramError:
        pass


# =========================================================
# FSUB COMMANDS
# =========================================================

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

        await reply(
            update,
            "📢 No force-sub channels configured.",
        )

        return

    text = "📢 Force-sub channels:\n\n"

    for channel in channels:

        title = (
            channel.get("title")
            or "Unnamed Channel"
        )

        text += (
            f"• {title}\n"
            f"  ID: {channel['channel_id']}\n\n"
        )

    await reply(
        update,
        text,
    )


async def add_fsub_cmd(
    update,
    context,
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
            "Bot must be admin in the channel.",
        )

        return

    channel_id = context.args[0]

    try:

        chat = await context.bot.get_chat(
            channel_id
        )

        title = (
            chat.title
            or chat.first_name
            or "Channel"
        )

        username = chat.username

        invite_link = (
            f"https://t.me/{username}"
            if username
            else ""
        )

        db.add_fsub(
            channel_id,
            invite_link,
            title,
        )

        await reply(
            update,
            "✅ Force-sub channel added.\n\n"
            f"📢 Name: {title}\n"
            f"🆔 ID: {channel_id}\n\n"
            "The button will show the channel name.",
        )

    except TelegramError as e:

        await reply(
            update,
            "❌ Could not access this channel.\n\n"
            "Make sure the bot is an admin there.\n\n"
            f"Error: {e}",
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

        await reply(
            update,
            "Usage: /del_fsub <channel_id>",
        )

        return

    db.del_fsub(
        context.args[0]
    )

    await reply(
        update,
        "✅ Force-sub channel removed.",
    )


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

    if await check_fsub(
        update,
        context,
    ):

        await context.bot.send_message(
            query.message.chat_id,
            "✅ Subscription verified.\n\n"
            "Now open your file link again.",
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

        await reply(
            update,
            "Usage: /add_banuser <user_id>",
        )

        return

    try:

        uid = int(
            context.args[0]
        )

        db.ban_user(
            uid,
            update.effective_user.id,
        )

        await reply(
            update,
            f"🚫 User {uid} banned.",
        )

    except ValueError:

        await reply(
            update,
            "❌ Invalid user ID.",
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

        await reply(
            update,
            "Usage: /del_banuser <user_id>",
        )

        return

    try:

        db.unban_user(
            int(context.args[0])
        )

        await reply(
            update,
            "✅ User unbanned.",
        )

    except ValueError:

        await reply(
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

        await reply(
            update,
            "🚫 No banned users.",
        )

        return

    await reply(
        update,
        "🚫 Banned users:\n\n"
        + "\n".join(
            f"• {r['user_id']}"
            for r in rows
        ),
    )


# =========================================================
# REQUEST
# =========================================================

async def request_cmd(
    update,
    context,
):

    text = " ".join(
        context.args
    ).strip()

    if not text:

        await reply(
            update,
            "Usage: /request <movie or series>",
        )

        return

    rid = db.add_request(
        update.effective_user.id,
        text,
    )

    await reply(
        update,
        f"✅ Request submitted.\n🆔 Request ID: {rid}",
    )

    try:

        await context.bot.send_message(
            OWNER_ID,
            f"📩 New request #{rid}\n\n"
            f"👤 User: {update.effective_user.id}\n"
            f"🎬 Request: {text}",
        )

    except TelegramError:
        pass


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

        await reply(
            update,
            "Usage: /add_admins <user_id>",
        )

        return

    try:

        db.add_admin(
            int(context.args[0])
        )

        await reply(
            update,
            "✅ Admin added.",
        )

    except ValueError:

        await reply(
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

        await reply(
            update,
            "Usage: /del_admins <user_id>",
        )

        return

    try:

        db.remove_admin(
            int(context.args[0])
        )

        await reply(
            update,
            "✅ Admin removed.",
        )

    except ValueError:

        await reply(
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
        text,
    )


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

    text = " ".join(
        context.args
    ).strip()

    if not text:

        await reply(
            update,
            "Usage: /broadcast <message>",
        )

        return

    users = db.list_users()

    ok = 0
    fail = 0

    for uid in users:

        try:

            await context.bot.send_message(
                uid,
                text,
            )

            ok += 1

        except TelegramError:

            fail += 1

        await asyncio.sleep(
            0.05
        )

    await reply(
        update,
        f"📣 Broadcast complete.\n\n"
        f"✅ Sent: {ok}\n"
        f"❌ Failed: {fail}",
    )


async def pbroadcast_cmd(
    update,
    context,
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
            "Usage: /pbroadcast <message>",
        )

        return

    users = db.list_users()

    ok = 0
    fail = 0

    for uid in users:

        try:

            msg = await context.bot.send_message(
                uid,
                text,
            )

            try:

                await context.bot.pin_chat_message(
                    uid,
                    msg.message_id,
                    disable_notification=True,
                )

            except TelegramError:
                pass

            ok += 1

        except TelegramError:

            fail += 1

        await asyncio.sleep(
            0.05
        )

    await reply(
        update,
        f"📌 Broadcast complete.\n\n"
        f"✅ Sent: {ok}\n"
        f"❌ Failed: {fail}",
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

    await reply(
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

    await reply(
        update,
        "✅ All active setup/configuration state has been reset.",
    )


# =========================================================
# LEGACY COMMANDS
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


async def stats_cmd(
    update,
    context,
):

    if not is_admin(
        update.effective_user.id
    ):
        return

    s = db.stats()

    await reply(
        update,
        f"📊 Statistics\n\n"
        f"📁 Files: {s['files']}\n"
        f"📦 Batches: {s['batches']}\n"
        f"👑 Admins: {s['admins']}\n"
        f"💎 Premium: {s['premium']}\n"
        f"👥 Users: {s['users']}\n"
        f"🚫 Banned: {s['banned']}",
    )


# =========================================================
# STARTUP
# =========================================================

async def post_init(
    application,
):

    log.info(
        "Recovering pending auto-deletes..."
    )

    await recover_pending_deletes(
        application
    )

    log.info(
        "Pending auto-delete recovery complete."
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
        .post_init(post_init)
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

    for name, function in commands.items():

        app.add_handler(
            CommandHandler(
                name,
                function,
            )
        )

    app.add_handler(
        CallbackQueryHandler(
            fsub_callback,
            pattern="^check_fsub$",
        )
    )

    app.add_handler(
        CallbackQueryHandler(
            auto_delete_callback,
            pattern="^del_[0-9]+$",
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
