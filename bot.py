import os
import logging
import threading
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from http.server import BaseHTTPRequestHandler, HTTPServer
from html import escape
from urllib.parse import quote

from dotenv import load_dotenv

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LinkPreviewOptions,
)
from telegram.constants import ChatMemberStatus
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

from database import Database
from shortener import Shortener


# =========================================================
# CONFIG
# =========================================================

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
BOT_USERNAME = os.getenv("BOT_USERNAME", "").strip().lstrip("@")
OWNER_ID = int(os.getenv("OWNER_ID", "0"))
DB_CHANNEL_ID = int(os.getenv("DB_CHANNEL_ID", "0"))

PORT = int(os.getenv("PORT", "10000"))

IST = ZoneInfo("Asia/Kolkata")

BYPASS_MIN_SECONDS = 90

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is not configured")

if not BOT_USERNAME:
    raise RuntimeError("BOT_USERNAME is not configured")

if not OWNER_ID:
    raise RuntimeError("OWNER_ID is not configured")

if not DB_CHANNEL_ID:
    raise RuntimeError("DB_CHANNEL_ID is not configured")


# =========================================================
# DATABASE
# =========================================================

db = Database()
shortener = Shortener(db)


# =========================================================
# LOGGING
# =========================================================

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger("file-store-bot")


# =========================================================
# PENDING STATES
# =========================================================

_pending_image = set()
_pending_autodelete = set()
_pending_admin = set()
_pending_fsub = set()


# =========================================================
# HEALTH SERVER
# =========================================================

class HealthHandler(BaseHTTPRequestHandler):

    def do_GET(self):
        body = b'{"ok":true,"service":"telegram-file-store-bot"}'

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        return


def start_health_server():
    server = HTTPServer(
        ("0.0.0.0", PORT),
        HealthHandler,
    )

    logger.info("Health server started on port %s", PORT)
    server.serve_forever()


# =========================================================
# HELPERS
# =========================================================

def now_utc():
    return datetime.now(timezone.utc)


def format_dt(dt, tz_name="Asia/Kolkata"):
    if not dt:
        return "N/A"

    try:
        if isinstance(dt, str):
            dt = datetime.fromisoformat(
                dt.replace("Z", "+00:00")
            )

        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)

        tz = ZoneInfo(tz_name)
        return dt.astimezone(tz).strftime(
            "%d %b %Y, %I:%M %p"
        )

    except Exception:
        return str(dt)


def get_user_tz(user_id):
    try:
        return db.get_user_timezone(user_id)
    except Exception:
        return "Asia/Kolkata"


def is_owner(user_id):
    return int(user_id) == OWNER_ID


def is_admin(user_id):
    try:
        return is_owner(user_id) or db.is_admin(user_id)
    except Exception:
        return is_owner(user_id)


def is_mod(user_id):
    """
    Compatible with a database.py that may provide a mod system.
    If no mod methods exist, returns False.
    """
    try:
        method = getattr(db, "is_mod", None)

        if method:
            return bool(method(user_id))

    except Exception:
        pass

    return False


def can_genlink(user_id):
    return (
        is_owner(user_id)
        or is_admin(user_id)
        or is_mod(user_id)
    )


def main_link_url(token):
    return f"https://t.me/{BOT_USERNAME}?start=link_{token}"


def share_url(url):
    return (
        "https://t.me/share/url?"
        f"url={quote(url, safe='')}"
    )


def mention_user(user_id, name):
    return (
        f'<a href="tg://user?id={int(user_id)}">'
        f'{escape(name or "User")}'
        f'</a>'
    )


def display_user(row):
    uid = int(row.get("user_id", 0))
    username = row.get("username") or ""
    first_name = row.get("first_name") or "User"

    name = first_name

    if username:
        name = f"@{username}"

    return mention_user(uid, name)


def link_preview_disabled():
    return LinkPreviewOptions(
        is_disabled=True
    )


# =========================================================
# START UI
# =========================================================

def start_caption():
    return (
        "<b>Hi There....! 💥</b>\n\n"
        "This is a <b>File Store Bot</b>.\n"
        "Generate and access your files through secure links.\n\n"
        "Owner: <a href=\"tg://user?id=0\">@Its_Lozo</a>"
    )


def start_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "ABOUT",
                callback_data="about",
            ),
            InlineKeyboardButton(
                "CLOSE",
                callback_data="close",
            ),
        ]
    ])


def about_caption():
    return (
        "<b>ABOUT</b>\n\n"
        "Made for: "
        '<a href="https://t.me/Anime_Hub_94">'
        "Anime Hub"
        "</a>\n\n"
        "Owner: "
        '<a href="tg://user?id=0">@Its_Lozo</a>\n'
        "Developer: "
        '<a href="tg://user?id=0">@Its_Lozo</a>'
    )


def about_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "BACK",
                callback_data="back_start",
            )
        ],
        [
            InlineKeyboardButton(
                "CLOSE",
                callback_data="close",
            )
        ],
    ])


# =========================================================
# F-SUB
# =========================================================

async def get_fsub_status(bot, user_id):
    joined = []
    missing = []

    rows = db.list_fsub()

    for row in rows:
        channel_id = row.get("channel_id")
        title = row.get("title") or str(channel_id)
        invite = row.get("invite_link") or ""

        try:
            member = await bot.get_chat_member(
                chat_id=int(channel_id),
                user_id=int(user_id),
            )

            if member.status in (
                ChatMemberStatus.LEFT,
                ChatMemberStatus.BANNED,
            ):
                missing.append(row)
            else:
                joined.append(row)

        except Exception:
            missing.append(row)

    return joined, missing


def fsub_text(joined, missing):
    text = "<b>⚠️ Join Required Channels</b>\n\n"

    if joined:
        text += "<b>Joined:</b>\n"

        for row in joined:
            title = escape(
                row.get("title")
                or str(row.get("channel_id"))
            )

            text += f"✅ {title} — <b>JOINED</b>\n"

        text += "\n"

    if missing:
        text += "<b>Pending:</b>\n"

        for row in missing:
            title = escape(
                row.get("title")
                or str(row.get("channel_id"))
            )

            text += f"❌ {title}\n"

        text += "\nJoin all pending channels and press CHECK."

    return text


def fsub_keyboard(missing):
    buttons = []

    for row in missing:
        title = row.get("title") or "JOIN CHANNEL"
        invite = row.get("invite_link")

        if invite:
            buttons.append([
                InlineKeyboardButton(
                    f"JOIN {title}",
                    url=invite,
                )
            ])

    if missing:
        buttons.append([
            InlineKeyboardButton(
                "✅ CHECK",
                callback_data="check_fsub",
            )
        ])

    return InlineKeyboardMarkup(buttons)


async def ensure_fsub(bot, user_id):
    joined, missing = await get_fsub_status(
        bot,
        user_id,
    )

    if not missing:
        return True, joined, missing

    return False, joined, missing


# =========================================================
# DELIVERY
# =========================================================

async def delete_later(
    context,
    chat_id,
    message_id,
    delay,
):
    await context.bot.delete_message(
        chat_id=chat_id,
        message_id=message_id,
    )


async def deliver_target(
    bot,
    user_id,
    target,
):
    if not target:
        raise ValueError("Invalid target")

    if target.startswith("message:"):
        parts = target.split(":")

        if len(parts) != 3:
            raise ValueError("Invalid message target")

        channel_id = int(parts[1])
        message_id = int(parts[2])

        return await bot.copy_message(
            chat_id=user_id,
            from_chat_id=channel_id,
            message_id=message_id,
        )

    if target.startswith("batch:"):
        batch_id = target.split(":", 1)[1]

        rows = db.get_batch_items(batch_id)

        if not rows:
            raise ValueError("Batch is empty")

        sent = []

        for row in rows:
            msg = await bot.copy_message(
                chat_id=user_id,
                from_chat_id=int(row["channel_id"]),
                message_id=int(row["message_id"]),
            )

            sent.append(msg)

        return sent

    raise ValueError("Unknown target")


async def deliver_and_notify(
    update,
    context,
    target,
):
    user_id = update.effective_user.id

    # Placeholder
    wait_msg = None

    try:
        wait_msg = await context.bot.send_message(
            chat_id=user_id,
            text="Please Wait.....",
            link_preview_options=link_preview_disabled(),
        )

        sent = await deliver_target(
            context.bot,
            user_id,
            target,
        )

        try:
            minutes = int(
                db.get_setting(
                    "auto_delete_minutes",
                    "10",
                )
            )
        except Exception:
            minutes = 10

        notice = await context.bot.send_message(
            chat_id=user_id,
            text=(
                f"This File is deleting automatically in "
                f"{minutes} minutes. "
                "Forward in your Saved Messages..!"
            ),
            link_preview_options=link_preview_disabled(),
        )

        if isinstance(sent, list):
            messages = sent
        else:
            messages = [sent]

        delay = max(
            1,
            minutes * 60,
        )

        for msg in messages:
            context.job_queue.run_once(
                delete_job,
                delay,
                data={
                    "chat_id": user_id,
                    "message_id": msg.message_id,
                },
            )

        context.job_queue.run_once(
            delete_job,
            delay,
            data={
                "chat_id": user_id,
                "message_id": notice.message_id,
            },
        )

        return sent

    finally:
        if wait_msg:
            try:
                await wait_msg.delete()
            except Exception:
                pass


async def delete_job(context):
    data = context.job.data

    try:
        await context.bot.delete_message(
            chat_id=data["chat_id"],
            message_id=data["message_id"],
        )
    except Exception:
        pass


# =========================================================
# DOWNLOAD PAGE
# =========================================================

async def send_download_page(
    update,
    context,
    short_url,
):
    image = db.get_setting(
        "start_image",
        "",
    )

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "📥 DOWNLOAD",
                url=short_url,
            )
        ],
        [
            InlineKeyboardButton(
                "⭐ PREMIUM",
                callback_data="premium_info",
            ),
            InlineKeyboardButton(
                "📖 TUTORIAL",
                callback_data="tutorial",
            ),
        ],
    ])

    caption = (
        "<b>🔐 Secure Download</b>\n\n"
        "Your file is ready.\n"
        "Complete the verification to continue."
    )

    if image:
        try:
            await context.bot.send_photo(
                chat_id=update.effective_user.id,
                photo=image,
                caption=caption,
                reply_markup=keyboard,
            )
            return
        except Exception:
            pass

    await context.bot.send_message(
        chat_id=update.effective_user.id,
        text=caption,
        reply_markup=keyboard,
        link_preview_options=link_preview_disabled(),
    )


# =========================================================
# TOKEN VERIFICATION
# =========================================================

async def verify_token(
    update,
    context,
    token,
):
    user_id = update.effective_user.id

    if db.is_banned(user_id):
        await update.message.reply_text(
            "🚫 You are banned from using this bot."
        )
        return

    row = db.get_token(token)

    if not row:
        await update.message.reply_text(
            "❌ Invalid or expired verification link."
        )
        return

    if int(row.get("user_id", -1)) != int(user_id):
        await update.message.reply_text(
            "❌ This verification link belongs to another user."
        )
        return

    if row.get("used"):
        await update.message.reply_text(
            "❌ This verification link has already been used."
        )
        return

    try:
        expires = row.get("expires_at")

        if isinstance(expires, str):
            expires = datetime.fromisoformat(
                expires.replace("Z", "+00:00")
            )

        if expires.tzinfo is None:
            expires = expires.replace(
                tzinfo=timezone.utc
            )

        if expires <= now_utc():
            await update.message.reply_text(
                "❌ Verification link expired."
            )
            return

    except Exception:
        await update.message.reply_text(
            "❌ Invalid verification link."
        )
        return

    ok, joined, missing = await ensure_fsub(
        context.bot,
        user_id,
    )

    if not ok:
        await update.message.reply_text(
            fsub_text(joined, missing),
            reply_markup=fsub_keyboard(missing),
        )
        return

    age = db.get_token_age_seconds(token)

    if (
        age is not None
        and age < BYPASS_MIN_SECONDS
        and not is_owner(user_id)
    ):
        db.ban_user(
            user_id,
            reason="Bypass Detected",
        )

        db.consume_token(
            token,
            user_id,
        )

        await update.message.reply_text(
            "🚫 Verification bypass detected.\n"
            "You have been banned."
        )
        return

    target = db.consume_token(
        token,
        user_id,
    )

    if not target:
        await update.message.reply_text(
            "❌ Verification link is no longer valid."
        )
        return

    try:
        await deliver_and_notify(
            update,
            context,
            target,
        )

    except Exception as exc:
        logger.exception(
            "Delivery failed: %s",
            exc,
        )

        await update.message.reply_text(
            "❌ Unable to deliver the file right now."
        )


# =========================================================
# OPEN MAIN LINK
# =========================================================

async def open_main(
    update,
    context,
    main_token,
):
    user_id = update.effective_user.id

    row = db.get_main_link(main_token)

    if not row:
        await update.message.reply_text(
            "❌ Invalid or expired link."
        )
        return

    if db.is_banned(user_id):
        await update.message.reply_text(
            "🚫 You are banned from using this bot."
        )
        return

    ok, joined, missing = await ensure_fsub(
        context.bot,
        user_id,
    )

    if not ok:
        await update.message.reply_text(
            fsub_text(joined, missing),
            reply_markup=fsub_keyboard(missing),
        )
        return

    target = row.get("target")

    if not target:
        await update.message.reply_text(
            "❌ File target not found."
        )
        return

    # Owner and premium users get direct delivery.
    if (
        is_owner(user_id)
        or db.is_premium(user_id)
    ):
        try:
            await deliver_and_notify(
                update,
                context,
                target,
            )
        except Exception:
            logger.exception(
                "Direct delivery failed"
            )

            await update.message.reply_text(
                "❌ Unable to deliver the file."
            )

        return

    try:
        token = db.create_token(
            user_id,
            target,
            2,
        )

        short_url = shortener.create_from_token(
            token,
            BOT_USERNAME,
        )

        if not short_url:
            raise ValueError(
                "Shortener returned empty URL"
            )

        await send_download_page(
            update,
            context,
            short_url,
        )

    except Exception as exc:
        logger.exception(
            "Shortener/gateway error: %s",
            exc,
        )

        await update.message.reply_text(
            "❌ Unable to create the download link."
        )


# =========================================================
# START
# =========================================================

async def start_command(
    update,
    context,
):
    user = update.effective_user

    db.add_user(
        user.id,
        user.username or "",
        user.first_name or "",
    )

    if db.is_banned(user.id):
        await update.message.reply_text(
            "🚫 You are banned from using this bot."
        )
        return

    args = context.args

    if args:
        arg = args[0]

        if arg.startswith("verify_"):
            await verify_token(
                update,
                context,
                arg[7:],
            )
            return

        if arg.startswith("link_"):
            await open_main(
                update,
                context,
                arg[5:],
            )
            return

    ok, joined, missing = await ensure_fsub(
        context.bot,
        user.id,
    )

    if not ok:
        await update.message.reply_text(
            fsub_text(joined, missing),
            reply_markup=fsub_keyboard(missing),
        )
        return

    image = db.get_setting(
        "start_image",
        "",
    )

    if image:
        try:
            await update.message.reply_photo(
                photo=image,
                caption=start_caption(),
                reply_markup=start_keyboard(),
            )
            return
        except Exception:
            pass

    await update.message.reply_text(
        start_caption(),
        reply_markup=start_keyboard(),
        link_preview_options=link_preview_disabled(),
    )


# =========================================================
# GENLINK
# =========================================================

async def genlink_command(
    update,
    context,
):
    user_id = update.effective_user.id

    if not can_genlink(user_id):
        await update.message.reply_text(
            "❌ You are not allowed to use this command."
        )
        return

    if not update.message.reply_to_message:
        await update.message.reply_text(
            "Reply to any message/file and use /genlink."
        )
        return

    source = update.message.reply_to_message

    try:
        copied = await context.bot.copy_message(
            chat_id=DB_CHANNEL_ID,
            from_chat_id=source.chat_id,
            message_id=source.message_id,
        )

        file_id = db.add_file(
            DB_CHANNEL_ID,
            copied.message_id,
            source.caption or "",
        )

        target = (
            f"message:{DB_CHANNEL_ID}:"
            f"{copied.message_id}"
        )

        token = db.create_main_link(
            target
        )

        url = main_link_url(token)

        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "🔗 OPEN LINK",
                    url=url,
                ),
                InlineKeyboardButton(
                    "📤 SHARE",
                    url=share_url(url),
                ),
            ]
        ])

        sent = await update.message.reply_text(
            "<b>✅ Link Generated</b>\n\n"
            f"<code>{escape(url)}</code>",
            reply_markup=keyboard,
            link_preview_options=link_preview_disabled(),
        )

        # Remove the command message.
        try:
            await update.message.delete()
        except Exception:
            pass

        # Keep generated link for a reasonable time.
        context.job_queue.run_once(
            delete_job,
            300,
            data={
                "chat_id": sent.chat_id,
                "message_id": sent.message_id,
            },
        )

    except Exception as exc:
        logger.exception(
            "genlink error: %s",
            exc,
        )

        await update.message.reply_text(
            "❌ Failed to generate link."
        )


# =========================================================
# BATCH
# =========================================================

def parse_telegram_message_link(url):
    url = url.strip()

    if not url.startswith("http"):
        return None

    parts = url.rstrip("/").split("/")

    if len(parts) < 2:
        return None

    try:
        message_id = int(parts[-1])
    except Exception:
        return None

    # https://t.me/c/123456789/100
    if "t.me/c/" in url:
        try:
            internal_id = int(parts[-2])
            chat_id = int(
                f"-100{internal_id}"
            )

            return chat_id, message_id
        except Exception:
            return None

    # https://t.me/channel/100
    if "t.me/" in url:
        username = parts[-2]

        if username in (
            "c",
            "share",
        ):
            return None

        return f"@{username}", message_id

    return None


async def resolve_channel_id(
    bot,
    value,
):
    if isinstance(value, int):
        return value

    chat = await bot.get_chat(value)

    return chat.id


async def batch_command(
    update,
    context,
):
    user_id = update.effective_user.id

    if not can_genlink(user_id):
        await update.message.reply_text(
            "❌ You are not allowed to use this command."
        )
        return

    if len(context.args) != 2:
        await update.message.reply_text(
            "Usage:\n"
            "/batch FIRST_LINK LAST_LINK"
        )
        return

    first = parse_telegram_message_link(
        context.args[0]
    )

    last = parse_telegram_message_link(
        context.args[1]
    )

    if not first or not last:
        await update.message.reply_text(
            "❌ Invalid Telegram message links."
        )
        return

    try:
        first_chat = await resolve_channel_id(
            context.bot,
            first[0],
        )

        last_chat = await resolve_channel_id(
            context.bot,
            last[0],
        )

        if first_chat != last_chat:
            await update.message.reply_text(
                "❌ Both links must belong to the same channel."
            )
            return

        if first_chat != DB_CHANNEL_ID:
            await update.message.reply_text(
                "❌ Batch links must be from the database channel."
            )
            return

        lo = min(
            first[1],
            last[1],
        )

        hi = max(
            first[1],
            last[1],
        )

        rows = db.list_files_between(
            DB_CHANNEL_ID,
            lo,
            hi,
        )

        if not rows:
            await update.message.reply_text(
                "❌ No files/posts found in this range."
            )
            return

        file_ids = [
            row["file_id"]
            for row in rows
        ]

        batch_id = db.create_batch(
            file_ids
        )

        target = f"batch:{batch_id}"

        token = db.create_main_link(
            target
        )

        url = main_link_url(token)

        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "🔗 OPEN BATCH",
                    url=url,
                ),
                InlineKeyboardButton(
                    "📤 SHARE",
                    url=share_url(url),
                ),
            ]
        ])

        sent = await update.message.reply_text(
            "<b>✅ Batch Link Generated</b>\n\n"
            f"Files: <b>{len(rows)}</b>\n\n"
            f"<code>{escape(url)}</code>",
            reply_markup=keyboard,
            link_preview_options=link_preview_disabled(),
        )

        context.job_queue.run_once(
            delete_job,
            300,
            data={
                "chat_id": sent.chat_id,
                "message_id": sent.message_id,
            },
        )

    except Exception as exc:
        logger.exception(
            "batch error: %s",
            exc,
        )

        await update.message.reply_text(
            "❌ Failed to create batch."
        )


# =========================================================
# SETTINGS
# =========================================================

def settings_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "🖼 SET IMAGE",
                callback_data="set_image",
            ),
            InlineKeyboardButton(
                "⏱ AUTO DELETE",
                callback_data="set_autodelete",
            ),
        ],
        [
            InlineKeyboardButton(
                "👤 ADMINS",
                callback_data="admins",
            ),
            InlineKeyboardButton(
                "📢 F-SUB",
                callback_data="fsub",
            ),
        ],
        [
            InlineKeyboardButton(
                "❌ CLOSE",
                callback_data="close",
            )
        ],
    ])


async def settings_command(
    update,
    context,
):
    if not is_admin(
        update.effective_user.id
    ):
        await update.message.reply_text(
            "❌ Owner/Admin only."
        )
        return

    image = db.get_setting(
        "start_image",
        "Not set",
    )

    auto_delete = db.get_setting(
        "auto_delete_minutes",
        "10",
    )

    await update.message.reply_text(
        "<b>⚙️ Bot Settings</b>\n\n"
        f"Start Image: {'Set' if image else 'Not set'}\n"
        f"Auto Delete: <b>{auto_delete} minutes</b>",
        reply_markup=settings_keyboard(),
    )


# =========================================================
# BAN COMMANDS
# =========================================================

async def banuser_command(
    update,
    context,
):
    if not is_admin(
        update.effective_user.id
    ):
        return

    if not context.args:
        await update.message.reply_text(
            "Usage: /banuser USER_ID"
        )
        return

    try:
        uid = int(context.args[0])
    except Exception:
        await update.message.reply_text(
            "❌ Invalid user ID."
        )
        return

    if uid == OWNER_ID:
        await update.message.reply_text(
            "❌ Owner cannot be banned."
        )
        return

    reason = (
        " ".join(context.args[1:])
        if len(context.args) > 1
        else "Manual Ban"
    )

    db.ban_user(
        uid,
        reason=reason,
    )

    await update.message.reply_text(
        f"🚫 User <code>{uid}</code> banned.",
        parse_mode="HTML",
    )


async def unbanuser_command(
    update,
    context,
):
    if not is_admin(
        update.effective_user.id
    ):
        return

    if not context.args:
        await update.message.reply_text(
            "Usage: /unbanuser USER_ID"
        )
        return

    try:
        uid = int(context.args[0])
    except Exception:
        await update.message.reply_text(
            "❌ Invalid user ID."
        )
        return

    db.unban_user(uid)

    await update.message.reply_text(
        f"✅ User <code>{uid}</code> unbanned.",
        parse_mode="HTML",
    )


async def banuser_list_command(
    update,
    context,
):
    if not is_admin(
        update.effective_user.id
    ):
        return

    rows = db.list_banned_users()

    if not rows:
        await update.message.reply_text(
            "No banned users."
        )
        return

    text = "<b>🚫 Banned Users</b>\n\n"

    for row in rows:
        uid = row.get("user_id")
        reason = escape(
            row.get("reason")
            or "Manual Ban"
        )

        text += (
            f"• <code>{uid}</code> — "
            f"{reason}\n"
        )

    await update.message.reply_text(
        text,
        parse_mode="HTML",
    )


# =========================================================
# PREMIUM
# =========================================================

async def addsubs_command(
    update,
    context,
):
    if not is_admin(
        update.effective_user.id
    ):
        return

    if len(context.args) != 2:
        await update.message.reply_text(
            "Usage: /addsubs USER_ID DAYS"
        )
        return

    try:
        uid = int(context.args[0])
        days = int(context.args[1])

        start, expiry = db.add_premium(
            uid,
            days,
        )

        await update.message.reply_text(
            "✅ Premium added.\n\n"
            f"User: <code>{uid}</code>\n"
            f"Days: <b>{days}</b>\n"
            f"Expires: <b>{format_dt(expiry)}</b>",
            parse_mode="HTML",
        )

    except Exception:
        await update.message.reply_text(
            "❌ Invalid values."
        )


async def removesubs_command(
    update,
    context,
):
    if not is_admin(
        update.effective_user.id
    ):
        return

    if not context.args:
        await update.message.reply_text(
            "Usage: /removesubs USER_ID"
        )
        return

    try:
        uid = int(context.args[0])
    except Exception:
        await update.message.reply_text(
            "❌ Invalid user ID."
        )
        return

    old = db.remove_premium(uid)

    if old:
        await update.message.reply_text(
            f"✅ Premium removed from <code>{uid}</code>.",
            parse_mode="HTML",
        )
    else:
        await update.message.reply_text(
            "❌ Premium subscription not found."
        )


async def myplan_command(
    update,
    context,
):
    uid = update.effective_user.id

    premium = db.get_premium(uid)

    if not premium:
        await update.message.reply_text(
            "You don't have an active premium plan."
        )
        return

    tz = get_user_tz(uid)

    await update.message.reply_text(
        "<b>⭐ Your Premium Plan</b>\n\n"
        f"Started: <b>{format_dt(premium.get('starts_at'), tz)}</b>\n"
        f"Expires: <b>{format_dt(premium.get('expires_at'), tz)}</b>\n"
        f"Timezone: <b>{tz}</b>",
        parse_mode="HTML",
    )


async def list_premium_command(
    update,
    context,
):
    if not is_admin(
        update.effective_user.id
    ):
        return

    rows = db.list_premium()

    if not rows:
        await update.message.reply_text(
            "No active premium users."
        )
        return

    text = "<b>⭐ Premium Users</b>\n\n"

    for row in rows:
        uid = int(row["user_id"])

        text += (
            f"• <code>{uid}</code>\n"
            f"  Expires: "
            f"{format_dt(row.get('expires_at'), 'Asia/Kolkata')}\n\n"
        )

    await update.message.reply_text(
        text,
        parse_mode="HTML",
    )


# =========================================================
# USERS
# =========================================================

async def users_command(
    update,
    context,
):
    if not is_admin(
        update.effective_user.id
    ):
        return

    rows = db.list_users()

    if not rows:
        await update.message.reply_text(
            "No users found."
        )
        return

    text = (
        f"<b>👥 Users: {len(rows)}</b>\n\n"
    )

    for row in rows:
        uid = int(row["user_id"])
        name = row.get("first_name") or "User"
        username = row.get("username") or ""

        text += (
            f"{mention_user(uid, name)}\n"
            f"Username: "
            f"{('@' + username) if username else 'N/A'}\n"
            f"ID: <code>{uid}</code>\n\n"
        )

    # Telegram has message size limits.
    chunks = []

    current = ""

    for line in text.splitlines(True):
        if len(current) + len(line) > 3800:
            chunks.append(current)
            current = ""

        current += line

    if current:
        chunks.append(current)

    for chunk in chunks:
        await update.message.reply_text(
            chunk,
            parse_mode="HTML",
        )


# =========================================================
# ADMIN MANAGEMENT
# =========================================================

async def show_admins(
    query,
):
    admins = db.list_admins()

    text = "<b>👤 Admins</b>\n\n"

    owner_name = (
        '<a href="tg://user?id=0">'
        "@Its_Lozo"
        "</a>"
    )

    text += f"👑 Owner: {owner_name}\n"
    text += f"ID: <code>{OWNER_ID}</code>\n\n"

    if admins:
        for row in admins:
            uid = int(row["user_id"])
            name = row.get("first_name") or "Admin"
            username = row.get("username") or ""

            text += (
                f"{mention_user(uid, name)}"
                f"{(' @' + username) if username else ''}\n"
                f"ID: <code>{uid}</code>\n\n"
            )
    else:
        text += "No admins added.\n"

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "➕ ADD ADMIN",
                callback_data="add_admin",
            ),
            InlineKeyboardButton(
                "➖ REMOVE ADMIN",
                callback_data="remove_admin",
            ),
        ],
        [
            InlineKeyboardButton(
                "BACK",
                callback_data="settings",
            )
        ],
    ])

    await query.edit_message_text(
        text,
        reply_markup=keyboard,
        parse_mode="HTML",
        link_preview_options=link_preview_disabled(),
    )


# =========================================================
# F-SUB MANAGEMENT
# =========================================================

async def show_fsub(
    query,
):
    rows = db.list_fsub()

    text = "<b>📢 Force Subscription</b>\n\n"

    if not rows:
        text += "No FSub channels configured."
    else:
        for row in rows:
            cid = row.get("channel_id")
            title = escape(
                row.get("title")
                or str(cid)
            )

            invite = row.get("invite_link") or ""

            text += f"• <b>{title}</b>\n"
            text += f"ID: <code>{cid}</code>\n"

            if invite:
                text += f"Invite: {escape(invite)}\n"

            text += "\n"

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "➕ ADD CHANNEL",
                callback_data="add_fsub",
            ),
            InlineKeyboardButton(
                "➖ REMOVE CHANNEL",
                callback_data="remove_fsub",
            ),
        ],
        [
            InlineKeyboardButton(
                "BACK",
                callback_data="settings",
            )
        ],
    ])

    await query.edit_message_text(
        text,
        reply_markup=keyboard,
        parse_mode="HTML",
        link_preview_options=link_preview_disabled(),
    )


# =========================================================
# BROADCAST
# =========================================================

async def broadcast_command(
    update,
    context,
):
    if not is_admin(
        update.effective_user.id
    ):
        return

    if not update.message.reply_to_message:
        await update.message.reply_text(
            "Reply to a message and use /broadcast."
        )
        return

    source = update.message.reply_to_message

    delete_after = None

    if context.args:
        value = context.args[0].lower()

        try:
            if value.endswith("h"):
                hours = float(
                    value[:-1]
                )
                delete_after = hours * 3600

            elif value.endswith("m"):
                mins = float(
                    value[:-1]
                )
                delete_after = mins * 60

            elif value.endswith("d"):
                days = float(
                    value[:-1]
                )
                delete_after = days * 86400

        except Exception:
            delete_after = None

    users = db.list_users()

    if not users:
        await update.message.reply_text(
            "No users found."
        )
        return

    sent_count = 0
    failed_count = 0

    delete_at = (
        now_utc()
        + timedelta(seconds=delete_after)
        if delete_after
        else None
    )

    try:
        db.create_broadcast(
            source.message_id,
            delete_at,
        )
    except Exception:
        pass

    for row in users:
        uid = int(row["user_id"])

        if db.is_banned(uid):
            continue

        try:
            msg = await context.bot.copy_message(
                chat_id=uid,
                from_chat_id=source.chat_id,
                message_id=source.message_id,
            )

            sent_count += 1

            if delete_after:
                context.job_queue.run_once(
                    delete_job,
                    delete_after,
                    data={
                        "chat_id": uid,
                        "message_id": msg.message_id,
                    },
                )

        except Exception:
            failed_count += 1

    await update.message.reply_text(
        "<b>📢 Broadcast Complete</b>\n\n"
        f"Sent: <b>{sent_count}</b>\n"
        f"Failed: <b>{failed_count}</b>",
        parse_mode="HTML",
    )


# =========================================================
# TIMEZONE
# =========================================================

TIMEZONES = [
    ("🇮🇳 India", "Asia/Kolkata"),
    ("🇦🇪 Dubai", "Asia/Dubai"),
    ("🇸🇦 Riyadh", "Asia/Riyadh"),
    ("🇵🇰 Pakistan", "Asia/Karachi"),
    ("🇧🇩 Bangladesh", "Asia/Dhaka"),
    ("🇳🇵 Nepal", "Asia/Kathmandu"),
    ("🇯🇵 Japan", "Asia/Tokyo"),
    ("🇸🇬 Singapore", "Asia/Singapore"),
    ("🇬🇧 London", "Europe/London"),
    ("🇩🇪 Berlin", "Europe/Berlin"),
    ("🇺🇸 New York", "America/New_York"),
    ("🇺🇸 Los Angeles", "America/Los_Angeles"),
]


async def timezone_command(
    update,
    context,
):
    keyboard = []

    for title, tz in TIMEZONES:
        keyboard.append([
            InlineKeyboardButton(
                title,
                callback_data=f"tz:{tz}",
            )
        ])

    await update.message.reply_text(
        "<b>🌍 Select your timezone</b>",
        reply_markup=InlineKeyboardMarkup(
            keyboard
        ),
    )


# =========================================================
# CALLBACKS
# =========================================================

async def callback_handler(
    update,
    context,
):
    query = update.callback_query

    await query.answer()

    uid = query.from_user.id
    data = query.data or ""

    # ---------------------------------------------
    # BASIC UI
    # ---------------------------------------------

    if data == "close":
        try:
            await query.message.delete()
        except Exception:
            pass

        return

    if data == "about":
        await query.edit_message_text(
            about_caption(),
            reply_markup=about_keyboard(),
            parse_mode="HTML",
            link_preview_options=link_preview_disabled(),
        )
        return

    if data == "back_start":
        image = db.get_setting(
            "start_image",
            "",
        )

        if image:
            try:
                await query.message.delete()

                await context.bot.send_photo(
                    chat_id=uid,
                    photo=image,
                    caption=start_caption(),
                    reply_markup=start_keyboard(),
                )

                return

            except Exception:
                pass

        await query.edit_message_text(
            start_caption(),
            reply_markup=start_keyboard(),
            parse_mode="HTML",
            link_preview_options=link_preview_disabled(),
        )

        return

    # ---------------------------------------------
    # F-SUB
    # ---------------------------------------------

    if data == "check_fsub":
        ok, joined, missing = await ensure_fsub(
            context.bot,
            uid,
        )

        if not ok:
            await query.edit_message_text(
                fsub_text(joined, missing),
                reply_markup=fsub_keyboard(missing),
                parse_mode="HTML",
            )
            return

        await query.message.delete()

        image = db.get_setting(
            "start_image",
            "",
        )

        if image:
            try:
                await context.bot.send_photo(
                    chat_id=uid,
                    photo=image,
                    caption=start_caption(),
                    reply_markup=start_keyboard(),
                )
                return
            except Exception:
                pass

        await context.bot.send_message(
            chat_id=uid,
            text=start_caption(),
            reply_markup=start_keyboard(),
            parse_mode="HTML",
            link_preview_options=link_preview_disabled(),
        )

        return

    if data == "fsub_noop":
        return

    # ---------------------------------------------
    # TIMEZONE
    # ---------------------------------------------

    if data.startswith("tz:"):
        tz_name = data[3:]

        try:
            ZoneInfo(tz_name)
        except Exception:
            await query.answer(
                "Invalid timezone.",
                show_alert=True,
            )
            return

        db.set_user_timezone(
            uid,
            tz_name,
        )

        await query.edit_message_text(
            f"✅ Timezone updated to <b>{escape(tz_name)}</b>.",
            parse_mode="HTML",
        )

        return

    # ---------------------------------------------
    # PREMIUM INFO
    # ---------------------------------------------

    if data == "premium_info":
        await query.answer(
            "Premium users get direct file delivery.",
            show_alert=True,
        )
        return

    if data == "tutorial":
        await query.answer(
            "Complete the verification and press DOWNLOAD.",
            show_alert=True,
        )
        return

    # ---------------------------------------------
    # ADMIN PROTECTION
    # ---------------------------------------------

    if not is_admin(uid):
        await query.answer(
            "Owner/Admin only.",
            show_alert=True,
        )
        return

    # ---------------------------------------------
    # SETTINGS
    # ---------------------------------------------

    if data == "settings":
        image = db.get_setting(
            "start_image",
            "",
        )

        auto_delete = db.get_setting(
            "auto_delete_minutes",
            "10",
        )

        await query.edit_message_text(
            "<b>⚙️ Bot Settings</b>\n\n"
            f"Start Image: "
            f"{'Set' if image else 'Not set'}\n"
            f"Auto Delete: "
            f"<b>{auto_delete} minutes</b>",
            reply_markup=settings_keyboard(),
            parse_mode="HTML",
        )

        return

    if data == "set_image":
        _pending_image.add(uid)

        await query.message.reply_text(
            "🖼 Send the new start image now."
        )

        return

    if data == "set_autodelete":
        _pending_autodelete.add(uid)

        await query.message.reply_text(
            "⏱ Send auto-delete time in minutes.\n"
            "Example: <code>10</code>",
            parse_mode="HTML",
        )

        return

    if data == "admins":
        await show_admins(query)
        return

    if data == "add_admin":
        _pending_admin.add(uid)

        await query.message.reply_text(
            "Send the Telegram user ID to add as admin."
        )

        return

    if data == "remove_admin":
        _pending_admin.add(
            f"remove:{uid}"
        )

        await query.message.reply_text(
            "Send the Telegram user ID to remove from admins."
        )

        return

    if data == "fsub":
        await show_fsub(query)
        return

    if data == "add_fsub":
        _pending_fsub.add(uid)

        await query.message.reply_text(
            "Send the channel ID.\n\n"
            "The bot must be admin in that channel."
        )

        return

    if data == "remove_fsub":
        _pending_fsub.add(
            f"remove:{uid}"
        )

        await query.message.reply_text(
            "Send the channel ID to remove."
        )

        return


# =========================================================
# SETTINGS INPUT
# =========================================================

async def settings_input_handler(
    update,
    context,
):
    uid = update.effective_user.id

    if not is_admin(uid):
        return

    # ---------------------------------------------
    # IMAGE
    # ---------------------------------------------

    if uid in _pending_image:
        if not update.message.photo:
            await update.message.reply_text(
                "❌ Please send an image."
            )
            return

        file_id = update.message.photo[-1].file_id

        db.set_setting(
            "start_image",
            file_id,
        )

        _pending_image.discard(uid)

        await update.message.reply_text(
            "✅ Start image updated successfully."
        )

        return

    # ---------------------------------------------
    # AUTO DELETE
    # ---------------------------------------------

    if uid in _pending_autodelete:
        try:
            minutes = int(
                update.message.text.strip()
            )

            if minutes < 1:
                raise ValueError

        except Exception:
            await update.message.reply_text(
                "❌ Enter a valid number of minutes."
            )
            return

        db.set_setting(
            "auto_delete_minutes",
            minutes,
        )

        _pending_autodelete.discard(uid)

        await update.message.reply_text(
            f"✅ Auto-delete set to {minutes} minutes."
        )

        return

    # ---------------------------------------------
    # ADMIN
    # ---------------------------------------------

    admin_state = None

    if uid in _pending_admin:
        admin_state = "add"

    elif f"remove:{uid}" in _pending_admin:
        admin_state = "remove"

    if admin_state:
        try:
            target_uid = int(
                update.message.text.strip()
            )
        except Exception:
            await update.message.reply_text(
                "❌ Invalid user ID."
            )
            return

        if not is_owner(uid):
            await update.message.reply_text(
                "❌ Only owner can modify admins."
            )

            _pending_admin.discard(uid)
            _pending_admin.discard(
                f"remove:{uid}"
            )

            return

        if admin_state == "add":
            db.add_admin(target_uid)

            await update.message.reply_text(
                f"✅ <code>{target_uid}</code> added as admin.",
                parse_mode="HTML",
            )

        else:
            db.remove_admin(target_uid)

            await update.message.reply_text(
                f"✅ <code>{target_uid}</code> removed from admins.",
                parse_mode="HTML",
            )

        _pending_admin.discard(uid)
        _pending_admin.discard(
            f"remove:{uid}"
        )

        return

    # ---------------------------------------------
    # F-SUB
    # ---------------------------------------------

    fsub_state = None

    if uid in _pending_fsub:
        fsub_state = "add"

    elif f"remove:{uid}" in _pending_fsub:
        fsub_state = "remove"

    if fsub_state:
        if not is_owner(uid):
            await update.message.reply_text(
                "❌ Only owner can modify FSub."
            )

            _pending_fsub.discard(uid)
            _pending_fsub.discard(
                f"remove:{uid}"
            )

            return

        value = update.message.text.strip()

        if fsub_state == "remove":
            db.del_fsub(value)

            await update.message.reply_text(
                "✅ FSub channel removed."
            )

        else:
            try:
                channel_id = int(value)

                chat = await context.bot.get_chat(
                    channel_id
                )

                title = (
                    chat.title
                    or chat.username
                    or str(channel_id)
                )

                invite = ""

                try:
                    invite_obj = (
                        await context.bot.create_chat_invite_link(
                            chat_id=channel_id
                        )
                    )

                    invite = invite_obj.invite_link

                except Exception:
                    pass

                db.add_fsub(
                    channel_id,
                    invite,
                    title,
                )

                await update.message.reply_text(
                    "✅ FSub channel added.\n\n"
                    f"Name: <b>{escape(title)}</b>\n"
                    f"ID: <code>{channel_id}</code>",
                    parse_mode="HTML",
                )

            except Exception as exc:
                logger.exception(
                    "FSub add error: %s",
                    exc,
                )

                await update.message.reply_text(
                    "❌ Unable to add channel.\n"
                    "Make sure the bot is admin in the channel."
                )

        _pending_fsub.discard(uid)
        _pending_fsub.discard(
            f"remove:{uid}"
        )

        return


# =========================================================
# DB CHANNEL INDEXER
# =========================================================

async def channel_post_indexer(
    update,
    context,
):
    post = update.channel_post

    if not post:
        return

    if int(post.chat_id) != int(
        DB_CHANNEL_ID
    ):
        return

    try:
        caption = (
            post.caption
            or post.text
            or ""
        )

        file_id = db.add_file(
            DB_CHANNEL_ID,
            post.message_id,
            caption,
        )

        target = (
            f"message:{DB_CHANNEL_ID}:"
            f"{post.message_id}"
        )

        token = db.create_main_link(
            target
        )

        url = main_link_url(token)

        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "🔗 OPEN",
                    url=url,
                ),
                InlineKeyboardButton(
                    "📤 SHARE",
                    url=share_url(url),
                ),
            ]
        ])

        try:
            await context.bot.edit_message_reply_markup(
                chat_id=DB_CHANNEL_ID,
                message_id=post.message_id,
                reply_markup=keyboard,
            )
        except Exception:
            try:
                await context.bot.send_message(
                    chat_id=DB_CHANNEL_ID,
                    text="🔗 File Link",
                    reply_to_message_id=post.message_id,
                    reply_markup=keyboard,
                    link_preview_options=link_preview_disabled(),
                )
            except Exception:
                pass

    except Exception as exc:
        logger.exception(
            "Channel indexing failed: %s",
            exc,
        )


# =========================================================
# BAN GUARD
# =========================================================

async def ban_guard(
    update,
    context,
):
    user = update.effective_user

    if not user:
        return

    if is_owner(user.id):
        return

    if db.is_banned(user.id):
        if update.callback_query:
            try:
                await update.callback_query.answer(
                    "🚫 You are banned.",
                    show_alert=True,
                )
            except Exception:
                pass

        elif update.message:
            try:
                await update.message.reply_text(
                    "🚫 You are banned from using this bot."
                )
            except Exception:
                pass

        return


# =========================================================
# ERROR HANDLER
# =========================================================

async def error_handler(
    update,
    context,
):
    logger.exception(
        "Unhandled exception",
        exc_info=context.error,
    )


# =========================================================
# MAIN
# =========================================================

def main():
    threading.Thread(
        target=start_health_server,
        daemon=True,
    ).start()

    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .build()
    )

    # ---------------------------------------------
    # BAN GUARD
    # ---------------------------------------------

    application.add_handler(
        MessageHandler(
            filters.ALL,
            ban_guard,
        ),
        group=-10,
    )

    # ---------------------------------------------
    # COMMANDS
    # ---------------------------------------------

    application.add_handler(
        CommandHandler(
            "start",
            start_command,
        )
    )

    application.add_handler(
        CommandHandler(
            "genlink",
            genlink_command,
        )
    )

    application.add_handler(
        CommandHandler(
            "batch",
            batch_command,
        )
    )

    application.add_handler(
        CommandHandler(
            "settings",
            settings_command,
        )
    )

    application.add_handler(
        CommandHandler(
            "banuser",
            banuser_command,
        )
    )

    application.add_handler(
        CommandHandler(
            "unbanuser",
            unbanuser_command,
        )
    )

    application.add_handler(
        CommandHandler(
            "banuser_list",
            banuser_list_command,
        )
    )

    application.add_handler(
        CommandHandler(
            "addsubs",
            addsubs_command,
        )
    )

    application.add_handler(
        CommandHandler(
            "removesubs",
            removesubs_command,
        )
    )

    application.add_handler(
        CommandHandler(
            "list_premium",
            list_premium_command,
        )
    )

    application.add_handler(
        CommandHandler(
            "myplan",
            myplan_command,
        )
    )

    application.add_handler(
        CommandHandler(
            "users",
            users_command,
        )
    )

    application.add_handler(
        CommandHandler(
            "broadcast",
            broadcast_command,
        )
    )

    application.add_handler(
        CommandHandler(
            "timezone",
            timezone_command,
        )
    )

    # ---------------------------------------------
    # CALLBACKS
    # ---------------------------------------------

    application.add_handler(
        CallbackQueryHandler(
            callback_handler
        )
    )

    # ---------------------------------------------
    # SETTINGS INPUT
    # ---------------------------------------------

    application.add_handler(
        MessageHandler(
            filters.ALL
            & ~filters.COMMAND
            & ~filters.UpdateType.CHANNEL_POST,
            settings_input_handler,
        ),
        group=1,
    )

    # ---------------------------------------------
    # DB CHANNEL INDEXER
    # ---------------------------------------------

    application.add_handler(
        MessageHandler(
            filters.UpdateType.CHANNEL_POST,
            channel_post_indexer,
        ),
        group=10,
    )

    application.add_error_handler(
        error_handler
    )

    logger.info(
        "Starting bot @%s | BOT_ID=%s",
        BOT_USERNAME,
        getattr(db, "bot_id", "unknown"),
    )

    application.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True,
        close_loop=False,
    )


if __name__ == "__main__":
    main()