import os
import logging
import re
import threading
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import quote

from dotenv import load_dotenv

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    LinkPreviewOptions,
)
from telegram.constants import ChatMemberStatus

from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    ApplicationHandlerStop,
    filters,
)

from database import Database
from shortener import Shortener


# =========================================================
# CONFIG
# =========================================================

load_dotenv()

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)

log = logging.getLogger("file-store-bot")

for name in ("httpx", "httpcore", "telegram", "telegram.ext"):
    logging.getLogger(name).setLevel(logging.WARNING)


BOT_TOKEN = os.environ["BOT_TOKEN"]
BOT_USERNAME = os.environ["BOT_USERNAME"].lstrip("@")
OWNER_ID = int(os.environ["OWNER_ID"])
DB_CHANNEL_ID = int(os.environ["DB_CHANNEL_ID"])

IST = ZoneInfo("Asia/Kolkata")


# =========================================================
# DATABASE / SHORTENER
# =========================================================

db = Database()
shortener = Shortener(db)


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
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()

        self.wfile.write(
            b'{"ok":true,"service":"telegram-file-store-bot"}'
        )

    def do_HEAD(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", "49")
        self.end_headers()

    def log_message(self, format, *args):
        pass


def start_health_server():
    port = int(os.environ.get("PORT", "10000"))

    server = HTTPServer(
        ("0.0.0.0", port),
        HealthHandler
    )

    log.info("Health server running on port %s", port)

    server.serve_forever()


# =========================================================
# BAN GUARD
# =========================================================

async def ban_guard(update, context):

    user = update.effective_user

    # Channel posts / updates without user
    if not user:
        return

    # Owner bypass
    if user.id == OWNER_ID:
        return

    try:
        banned = db.is_banned(user.id)

    except Exception:
        log.exception("Ban check failed")
        return

    if not banned:
        return

    if update.message:
        try:
            await update.message.reply_text(
                "🚫You Are Banned From Using The Bot 🚫"
            )
        except Exception:
            pass

    raise ApplicationHandlerStop


# =========================================================
# START / ABOUT
# =========================================================

def start_image():
    return db.get_setting("start_image", "")


def start_caption():

    return (
        "<i>ʜɪ ᴛʜᴇʀᴇ....! 💥</i>\n\n"
        "ɪ ᴀᴍ ᴀ ꜰɪʟᴇ-ꜱᴛᴏʀᴇ ʙᴏᴛ.\n"
        "ɪ ᴄᴀɴ ɢᴇɴᴇʀᴀᴛᴇ ʟɪɴᴋꜱ ᴅɪʀᴇᴄᴛʟʏ ᴡɪᴛʜ ɴᴏ ᴘʀᴏʙʟᴇᴍꜱ.\n\n"
        "<b>ᴍʏ ᴏᴡɴᴇʀ:</b> "
        '<a href="https://t.me/Its_Lozo">@ɪᴛꜱ_ʟᴏᴢᴏ</a>'
    )


def about_caption():

    return (
        "<b>ᴀʙᴏᴜᴛ ᴜꜱ..</b>\n\n"
        '➤ ᴍᴀᴅᴇ ꜰᴏʀ : '
        '<a href="https://t.me/Anime_Hub_94">ᴀɴɪᴍᴇ ʜᴜʙ</a>\n'
        '➤ ᴏᴡɴᴇʀ : '
        '<a href="https://t.me/Its_Lozo">@ɪᴛꜱ_ʟᴏᴢᴏ</a>\n'
        '➤ ᴅᴇᴠᴇʟᴏᴘᴇʀ : '
        '<a href="https://t.me/Its_Lozo">@ɪᴛꜱ_ʟᴏᴢᴏ</a>\n\n'
        "ᴀᴅɪᴏꜱ !!"
    )


def start_keyboard():

    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "ᴀʙᴏᴜᴛ",
                    callback_data="about"
                ),
                InlineKeyboardButton(
                    "ᴄʟᴏꜱᴇ",
                    callback_data="close"
                ),
            ]
        ]
    )


def about_keyboard():

    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "ʙᴀᴄᴋ",
                    callback_data="back"
                ),
                InlineKeyboardButton(
                    "ᴄʟᴏꜱᴇ",
                    callback_data="close"
                ),
            ]
        ]
    )


async def render_start(message):

    image = start_image()

    if image:

        try:
            return await message.reply_photo(
                photo=image,
                caption=start_caption(),
                parse_mode="HTML",
                reply_markup=start_keyboard()
            )

        except Exception:
            log.exception("Could not send start image")

    await message.reply_text(
        start_caption(),
        parse_mode="HTML",
        reply_markup=start_keyboard()
    )


async def edit_start(q):

    image = start_image()

    if image:

        try:
            return await q.edit_message_media(
                media=InputMediaPhoto(
                    media=image,
                    caption=start_caption(),
                    parse_mode="HTML"
                ),
                reply_markup=start_keyboard()
            )

        except Exception:
            pass

    try:
        await q.edit_message_text(
            start_caption(),
            parse_mode="HTML",
            reply_markup=start_keyboard()
        )

    except Exception:
        pass


async def edit_about(q):

    try:
        await q.edit_message_caption(
            caption=about_caption(),
            parse_mode="HTML",
            reply_markup=about_keyboard()
        )

    except Exception:

        try:
            await q.edit_message_text(
                about_caption(),
                parse_mode="HTML",
                reply_markup=about_keyboard()
            )

        except Exception:
            pass


# =========================================================
# FORCE SUB
# =========================================================

async def is_fsub_member(bot, user_id):

    missing = []

    for row in db.list_fsub():

        try:

            member = await bot.get_chat_member(
                int(row["channel_id"]),
                user_id
            )

            if member.status in (
                ChatMemberStatus.LEFT,
                ChatMemberStatus.BANNED
            ):
                missing.append(row)

        except Exception:
            missing.append(row)

    return missing


def fsub_keyboard(rows):

    buttons = []

    for row in rows:

        if row.get("invite_link"):

            buttons.append(
                [
                    InlineKeyboardButton(
                        f"ᴊᴏɪɴ {row.get('title') or 'ᴄʜᴀɴɴᴇʟ'}",
                        url=row["invite_link"]
                    )
                ]
            )

    buttons.append(
        [
            InlineKeyboardButton(
                "✅ ᴄʜᴇᴄᴋ ᴊᴏɪɴ",
                callback_data="check_fsub"
            )
        ]
    )

    return InlineKeyboardMarkup(buttons)


# =========================================================
# LINKS
# =========================================================

def main_link_url(token):

    return (
        f"https://t.me/{BOT_USERNAME}"
        f"?start=link_{token}"
    )


def share_url(url):

    return (
        "https://t.me/share/url"
        f"?url={quote(url, safe='')}"
    )


def admin_ok(uid):

    return uid == OWNER_ID or db.is_admin(uid)


# =========================================================
# SETTINGS
# =========================================================

def settings_keyboard():

    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "🖼️ ꜱᴇᴛ ɪᴍᴀɢᴇ",
                    callback_data="set_image"
                ),
                InlineKeyboardButton(
                    "🗑️ ᴀᴜᴛᴏ ᴅᴇʟᴇᴛᴇ",
                    callback_data="auto_delete"
                ),
            ],
            [
                InlineKeyboardButton(
                    "👮 ᴀᴅᴍɪɴꜱ",
                    callback_data="admins"
                ),
                InlineKeyboardButton(
                    "📢 ꜰꜱᴜʙ",
                    callback_data="fsub"
                ),
            ],
            [
                InlineKeyboardButton(
                    "✖️ ᴄʟᴏꜱᴇ",
                    callback_data="settings_close"
                )
            ],
        ]
    )


def settings_text():

    return (
        "<b>⚙️ ꜱᴇᴛᴛɪɴɢꜱ</b>\n\n"
        "ᴄʜᴏᴏꜱᴇ ᴀɴ ᴏᴘᴛɪᴏɴ."
    )


async def settings(update, context):

    uid = update.effective_user.id

    if not admin_ok(uid):

        return await update.message.reply_text(
            "❌ ᴜɴᴀᴜᴛʜᴏʀɪᴢᴇᴅ"
        )

    return await update.message.reply_text(
        settings_text(),
        parse_mode="HTML",
        reply_markup=settings_keyboard()
    )


# =========================================================
# GENLINK
# =========================================================

async def genlink(update, context):

    if not admin_ok(update.effective_user.id):

        return await update.message.reply_text(
            "❌ ʏᴏᴜ ᴀʀᴇ ɴᴏᴛ ᴀᴜᴛʜᴏʀɪᴢᴇᴅ."
        )

    replied = update.message.reply_to_message

    if not replied:

        return await update.message.reply_text(
            "ʀᴇᴘʟʏ ᴛᴏ ᴀɴʏ ᴍᴇꜱꜱᴀɢᴇ ᴀɴᴅ ᴜꜱᴇ /ɢᴇɴʟɪɴᴋ."
        )

    try:

        copied = await context.bot.copy_message(
            chat_id=DB_CHANNEL_ID,
            from_chat_id=replied.chat_id,
            message_id=replied.message_id
        )

        file_id = db.add_file(
            DB_CHANNEL_ID,
            copied.message_id,
            getattr(replied, "caption", None)
            or getattr(replied, "text", None)
            or ""
        )

        main = db.create_main_link(
            f"message:{DB_CHANNEL_ID}:{copied.message_id}"
        )

        url = main_link_url(main)

        markup = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "↗ ꜱʜᴀʀᴇ ᴜʀʟ",
                        url=share_url(url)
                    )
                ]
            ]
        )

        try:

            await context.bot.edit_message_reply_markup(
                chat_id=DB_CHANNEL_ID,
                message_id=copied.message_id,
                reply_markup=markup
            )

        except Exception:
            pass

        await update.message.reply_text(
            f"✅ <b>ɢᴇɴʟɪɴᴋ ɢᴇɴᴇʀᴀᴛᴇᴅ</b>\n\n{url}",
            parse_mode="HTML",
            disable_web_page_preview=True,
            reply_markup=markup
        )

    except Exception:

        log.exception("Genlink failed")

        await update.message.reply_text(
            "❌ ɢᴇɴʟɪɴᴋ ɢᴇɴᴇʀᴀᴛᴇ ɴᴀʜɪ ʜᴜᴀ."
        )


# =========================================================
# BATCH
# =========================================================

def parse_message_link(link):

    m = re.fullmatch(
        r"https?://t\.me/c/(\d+)/(\d+)",
        link.strip()
    )

    if m:
        return (
            int("-100" + m.group(1)),
            int(m.group(2))
        )

    m = re.fullmatch(
        r"https?://t\.me/([^/]+)/(\d+)",
        link.strip()
    )

    if m:
        return (
            m.group(1),
            int(m.group(2))
        )

    return None


async def batch(update, context):

    if not admin_ok(update.effective_user.id):

        return await update.message.reply_text(
            "❌ ʏᴏᴜ ᴀʀᴇ ɴᴏᴛ ᴀᴜᴛʜᴏʀɪᴢᴇᴅ."
        )

    if len(context.args) != 2:

        return await update.message.reply_text(
            "ᴜꜱᴀɢᴇ:\n"
            "/ʙᴀᴛᴄʜ <ꜰɪʀꜱᴛ ᴅʙ ʟɪɴᴋ> <ʟᴀꜱᴛ ᴅʙ ʟɪɴᴋ>"
        )

    first = parse_message_link(context.args[0])
    last = parse_message_link(context.args[1])

    if not first or not last:

        return await update.message.reply_text(
            "❌ ɪɴᴠᴀʟɪᴅ ᴛᴇʟᴇɢʀᴀᴍ ᴘᴏꜱᴛ ʟɪɴᴋ."
        )

    try:

        fc = (
            await context.bot.get_chat(first[0])
        ).id if isinstance(first[0], str) else first[0]

        lc = (
            await context.bot.get_chat(last[0])
        ).id if isinstance(last[0], str) else last[0]

    except Exception:

        return await update.message.reply_text(
            "❌ ᴄᴏᴜʟᴅ ɴᴏᴛ ʀᴇꜱᴏʟᴠᴇ ᴛʜᴇ ᴅʙ ᴄʜᴀɴɴᴇʟ."
        )

    if fc != DB_CHANNEL_ID or lc != DB_CHANNEL_ID:

        return await update.message.reply_text(
            "❌ ʙᴏᴛʜ ʟɪɴᴋꜱ ᴍᴜꜱᴛ ʙᴇ ꜰʀᴏᴍ ᴛʜᴇ ᴅʙ ᴄʜᴀɴɴᴇʟ."
        )

    lo, hi = sorted(
        (first[1], last[1])
    )

    rows = db.list_files_between(
        DB_CHANNEL_ID,
        lo,
        hi
    )

    if not rows:

        return await update.message.reply_text(
            "❌ ɴᴏ ᴅʙ ᴘᴏꜱᴛꜱ ꜰᴏᴜɴᴅ ɪɴ ᴛʜɪꜱ ʀᴀɴɢᴇ."
        )

    bid = db.create_batch(
        [r["file_id"] for r in rows]
    )

    main = db.create_main_link(
        f"batch:{bid}"
    )

    url = main_link_url(main)

    markup = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "↗ ꜱʜᴀʀᴇ ᴜʀʟ",
                    url=share_url(url)
                )
            ]
        ]
    )

    await context.bot.send_message(
        DB_CHANNEL_ID,
        "📦 <b>ʙᴀᴛᴄʜ ꜱʜᴀʀᴇ ᴜʀʟ</b>",
        parse_mode="HTML",
        reply_markup=markup
    )

    await update.message.reply_text(
        f"✅ <b>ʙᴀᴛᴄʜ ʟɪɴᴋ ɢᴇɴᴇʀᴀᴛᴇᴅ</b>\n\n"
        f"ɪᴛᴇᴍꜱ: <b>{len(rows)}</b>\n\n"
        f"{url}",
        parse_mode="HTML",
        disable_web_page_preview=True,
        reply_markup=markup
    )


# =========================================================
# DOWNLOAD PAGE
# =========================================================

async def send_download_page(message, short_url):

    caption = (
        "<i>📊 ʜᴇʏ ʙʀᴏ/ꜱɪꜱ,</i>\n\n"
        "➜ ʏᴏᴜʀ ʟɪɴᴋ ɪꜱ ʀᴇᴀᴅʏ, ᴋɪɴᴅʟʏ ᴄʟɪᴄᴋ ᴏɴ\n"
        "ᴅᴏᴡɴʟᴏᴀᴅ ʙᴜᴛᴛᴏɴ! 👇\n\n"
        "ᴛᴏ ʙᴜʏ ᴘʀᴇᴍɪᴜᴍ, ᴄᴏɴᴛᴀᴄᴛ: "
        '<a href="https://t.me/Its_Lozo">@ɪᴛꜱ_ʟᴏᴢᴏ</a>'
    )

    kb = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "• ᴄʟɪᴄᴋ ʜᴇʀᴇ ᴛᴏ ᴅᴏᴡɴʟᴏᴀᴅ •",
                    url=short_url
                )
            ],
            [
                InlineKeyboardButton(
                    "ᴘʀᴇᴍɪᴜᴍ",
                    url="https://t.me/PremiumHub094"
                ),
                InlineKeyboardButton(
                    "ᴛᴜᴛᴏʀɪᴀʟ",
                    url="https://t.me/Tutorial_Hub_94/4"
                )
            ]
        ]
    )

    image = start_image()

    if image:

        try:

            return await message.reply_photo(
                photo=image,
                caption=caption,
                parse_mode="HTML",
                reply_markup=kb
            )

        except Exception:
            pass

    await message.reply_text(
        caption,
        parse_mode="HTML",
        reply_markup=kb
    )


# =========================================================
# DELIVERY
# =========================================================

async def deliver_target(update, target):

    ids = []

    bot = update.get_bot()
    chat_id = update.effective_chat.id

    if target.startswith("message:"):

        _, cid, mid = target.split(":", 2)

        message = await bot.copy_message(
            chat_id=chat_id,
            from_chat_id=int(cid),
            message_id=int(mid)
        )

        ids.append(message.message_id)

    elif target.startswith("batch:"):

        bid = target.split(":", 1)[1]

        rows = db.get_batch_items(bid)

        if not rows:
            return []

        for row in rows:

            try:

                message = await bot.copy_message(
                    chat_id=chat_id,
                    from_chat_id=int(row["channel_id"]),
                    message_id=int(row["message_id"])
                )

                ids.append(message.message_id)

            except Exception:

                log.exception("Batch delivery failed")

    return ids


async def delete_delivered(context):

    data = context.job.data

    for mid in data["message_ids"]:

        try:

            await context.bot.delete_message(
                data["chat_id"],
                mid
            )

        except Exception:
            pass


def auto_delete_minutes():

    try:

        return max(
            0,
            int(
                db.get_setting(
                    "auto_delete_minutes",
                    "10"
                )
            )
        )

    except Exception:

        return 10


async def deliver_and_notify(update, context, target):

    ids = await deliver_target(
        update,
        target
    )

    if not ids:
        return

    mins = auto_delete_minutes()

    if mins > 0:

        msg = await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=(
                f"ᴛʜɪꜱ ꜰɪʟᴇ ɪꜱ ᴅᴇʟᴇᴛɪɴɢ "
                f"ᴀᴜᴛᴏᴍᴀᴛɪᴄᴀʟʟʏ ɪɴ {mins} ᴍɪɴᴜᴛᴇꜱ.\n\n"
                "ꜰᴏʀᴡᴀʀᴅ ɪᴛ ᴛᴏ ʏᴏᴜʀ ꜱᴀᴠᴇᴅ "
                "ᴍᴇꜱꜱᴀɢᴇꜱ..!"
            )
        )

        ids.append(msg.message_id)

        context.job_queue.run_once(
            delete_delivered,
            mins * 60,
            data={
                "chat_id": update.effective_chat.id,
                "message_ids": ids
            }
        )


# =========================================================
# VERIFY / OPEN MAIN LINK
# =========================================================

async def verify(update, context, token):

    uid = update.effective_user.id

    if uid == OWNER_ID:

        row = db.get_token(token)

        if row:

            target = (
                db.consume_token(token, uid)
                if int(row["user_id"]) == uid
                else row["target"]
            )

            if target:

                return await deliver_and_notify(
                    update,
                    context,
                    target
                )

        return await update.message.reply_text(
            "❌ ᴛʜɪꜱ ʟɪɴᴋ ɪꜱ ɴᴏᴛ ᴠᴀʟɪᴅ."
        )

    missing = await is_fsub_member(
        context.bot,
        uid
    )

    if missing:

        return await update.message.reply_text(
            "⚡ <b>ᴊᴏɪɴ ʀᴇǫᴜɪʀᴇᴅ</b>\n\n"
            "ᴊᴏɪɴ ᴀʟʟ ʀᴇǫᴜɪʀᴇᴅ ᴄʜᴀɴɴᴇʟꜱ "
            "ᴛʜᴇɴ ᴛᴀᴘ ᴄʜᴇᴄᴋ ᴊᴏɪɴ.",
            parse_mode="HTML",
            reply_markup=fsub_keyboard(missing)
        )

    row = db.get_token(token)

    if not row or int(row["user_id"]) != uid:

        return await update.message.reply_text(
            "❌ ᴛʜɪꜱ ꜱʜᴏʀᴛᴇɴᴇʀ ʟɪɴᴋ ɪꜱ ɴᴏᴛ ᴍᴀᴅᴇ ꜰᴏʀ ʏᴏᴜ."
        )

    target = db.consume_token(
        token,
        uid
    )

    if not target:

        return await update.message.reply_text(
            "❌ ᴛʜɪꜱ ꜱʜᴏʀᴛᴇɴᴇʀ ʟɪɴᴋ ɪꜱ ᴇxᴘɪʀᴇᴅ "
            "ᴏʀ ᴀʟʀᴇᴀᴅʏ ᴜꜱᴇᴅ."
        )

    await deliver_and_notify(
        update,
        context,
        target
    )


async def open_main(update, context, token):

    uid = update.effective_user.id

    row = db.get_main_link(token)

    if not row:

        return await update.message.reply_text(
            "❌ ᴛʜɪꜱ ʟɪɴᴋ ɪꜱ ɴᴏᴛ ᴠᴀʟɪᴅ."
        )

    missing = await is_fsub_member(
        context.bot,
        uid
    )

    if missing:

        return await update.message.reply_text(
            "⚡ <b>ᴊᴏɪɴ ʀᴇǫᴜɪʀᴇᴅ</b>\n\n"
            "ᴊᴏɪɴ ᴀʟʟ ʀᴇǫᴜɪʀᴇᴅ ᴄʜᴀɴɴᴇʟꜱ "
            "ᴛʜᴇɴ ᴛᴀᴘ ᴄʜᴇᴄᴋ ᴊᴏɪɴ.",
            parse_mode="HTML",
            reply_markup=fsub_keyboard(missing)
        )

    # Premium / Owner bypass
    if uid == OWNER_ID or db.is_premium(uid):

        return await deliver_and_notify(
            update,
            context,
            row["target"]
        )

    # Create one-time shortener token
    tok = db.create_token(
        uid,
        row["target"],
        2
    )

    short_url = shortener.create_from_token(
        tok,
        BOT_USERNAME
    )

    if not short_url:

        return await update.message.reply_text(
            "⚠️ ꜱʜᴏʀᴛᴇɴᴇʀ ɪꜱ ɴᴏᴛ ᴄᴏɴꜰɪɢᴜʀᴇᴅ ᴄᴏʀʀᴇᴄᴛʟʏ."
        )

    await send_download_page(
        update.message,
        short_url
    )


# =========================================================
# START
# =========================================================

async def start(update, context):

    user = update.effective_user

    db.add_user(
        user.id,
        user.username or "",
        user.first_name or ""
    )

    if db.is_banned(user.id):

        return await update.message.reply_text(
            "🚫 ʏᴏᴜ ᴀʀᴇ ʙᴀɴɴᴇᴅ."
        )

    if context.args:

        arg = context.args[0]

        if arg.startswith("verify_"):

            return await verify(
                update,
                context,
                arg[7:]
            )

        if arg.startswith("link_"):

            return await open_main(
                update,
                context,
                arg[5:]
            )

    await render_start(
        update.message
    )


# =========================================================
# CALLBACKS
# =========================================================

async def callback(update, context):

    q = update.callback_query
    uid = q.from_user.id

    if not admin_ok(uid):

        return await q.answer(
            "❌ ᴜɴᴀᴜᴛʜᴏʀɪᴢᴇᴅ",
            show_alert=True
        )

    if q.data in (
        "add_admin",
        "remove_admin"
    ) and uid != OWNER_ID:

        return await q.answer(
            "🚫 ᴏɴʟʏ ᴏᴡɴᴇʀ ᴄᴀɴ ᴀᴅᴅ/ʀᴇᴍᴏᴠᴇ ᴀᴅᴍɪɴꜱ",
            show_alert=True
        )

    if q.data in (
        "add_fsub",
        "remove_fsub"
    ) and uid != OWNER_ID:

        return await q.answer(
            "🚫 ᴏɴʟʏ ᴏᴡɴᴇʀ ᴄᴀɴ ᴀᴅᴅ/ʀᴇᴍᴏᴠᴇ ꜰꜱᴜʙ",
            show_alert=True
        )

    await q.answer()

    # =====================================================
    # CLOSE
    # =====================================================

    if q.data in (
        "close",
        "settings_close"
    ):

        try:
            await q.message.delete()
        except Exception:
            pass

        return

    # =====================================================
    # ABOUT
    # =====================================================

    if q.data == "about":

        return await edit_about(q)

    # =====================================================
    # BACK
    # =====================================================

    if q.data == "back":

        return await edit_start(q)

    # =====================================================
    # F-SUB CHECK
    # =====================================================

    if q.data == "check_fsub":

        missing = await is_fsub_member(
            context.bot,
            uid
        )

        if missing:

            return await q.answer(
                "❌ ᴊᴏɪɴ ᴀʟʟ ᴄʜᴀɴɴᴇʟꜱ ꜰɪʀꜱᴛ.",
                show_alert=True
            )

        try:
            await q.message.delete()
        except Exception:
            pass

        return await render_start(
            q.message
        )

    # =====================================================
    # ADMIN CHECK
    # =====================================================

    if not admin_ok(uid):

        return await q.answer(
            "❌ ᴜɴᴀᴜᴛʜᴏʀɪᴢᴇᴅ",
            show_alert=True
        )

    # =====================================================
    # SET START IMAGE
    # =====================================================

    if q.data == "set_image":

        _pending_image.add(uid)

        return await q.message.reply_text(
            "🖼️ ʟᴇᴛ ᴍᴇ ʜᴀᴠᴇ ᴛʜᴇ ɪᴍᴀɢᴇ "
            "ʏᴏᴜ ᴡᴀɴᴛ ᴛᴏ ᴜꜱᴇ ᴀꜱ ꜱᴛᴀʀᴛ ɪᴍᴀɢᴇ."
        )

    # =====================================================
    # AUTO DELETE
    # =====================================================

    if q.data == "auto_delete":

        cur = auto_delete_minutes()

        _pending_autodelete.add(uid)

        return await q.message.reply_text(
            f"🗑️ ᴀᴜᴛᴏ ᴅᴇʟᴇᴛᴇ ᴛɪᴍᴇ: "
            f"<b>{cur} ᴍɪɴᴜᴛᴇꜱ</b>\n\n"
            "ꜱᴇɴᴅ ᴛʜᴇ ɴᴇᴡ ᴛɪᴍᴇ ɪɴ ᴍɪɴᴜᴛᴇꜱ.\n"
            "ꜱᴇɴᴅ <code>0</code> ᴛᴏ ᴅɪꜱᴀʙʟᴇ.",
            parse_mode="HTML"
        )

    # =====================================================
    # ADMINS LIST
    # =====================================================

    if q.data == "admins":

        lines = [
            "<b>👮 ᴀᴅᴍɪɴꜱ</b>",
            "",
            f'• <a href="tg://user?id={OWNER_ID}">'
            f"ᴏᴡɴᴇʀ</a> — <code>{OWNER_ID}</code>"
        ]

        for a in db.list_admins():

            name = (
                a["first_name"]
                or (
                    "@" + a["username"]
                    if a["username"]
                    else "ᴀᴅᴍɪɴ"
                )
            )

            lines.append(
                f'• <a href="tg://user?id={a["user_id"]}">'
                f"{name}</a> — "
                f"<code>{a['user_id']}</code>"
            )

        return await q.message.edit_text(
            "\n".join(lines),
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "➕ ᴀᴅᴅ ᴀᴅᴍɪɴ",
                            callback_data="add_admin"
                        ),
                        InlineKeyboardButton(
                            "➖ ʀᴇᴍᴏᴠᴇ",
                            callback_data="remove_admin"
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            "↩️ ʙᴀᴄᴋ",
                            callback_data="settings_back"
                        )
                    ]
                ]
            )
        )

    # =====================================================
    # ADD ADMIN
    # =====================================================

    if q.data == "add_admin":

        if uid != OWNER_ID:

            return await q.answer(
                "❌ ᴏɴʟʏ ᴏᴡɴᴇʀ ᴄᴀɴ ᴀᴅᴅ ᴀᴅᴍɪɴꜱ.",
                show_alert=True
            )

        _pending_admin.add(uid)

        return await q.message.reply_text(
            "➕ ꜱᴇɴᴅ ᴛʜᴇ ᴜꜱᴇʀ ɪᴅ "
            "ᴛᴏ ᴀᴅᴅ ᴀꜱ ᴀᴅᴍɪɴ."
        )

    # =====================================================
    # REMOVE ADMIN
    # =====================================================

    if q.data == "remove_admin":

        if uid != OWNER_ID:

            return await q.answer(
                "❌ ᴏɴʟʏ ᴏᴡɴᴇʀ ᴄᴀɴ ʀᴇᴍᴏᴠᴇ ᴀᴅᴍɪɴꜱ.",
                show_alert=True
            )

        _pending_admin.add(-uid)

        return await q.message.reply_text(
            "➖ ꜱᴇɴᴅ ᴛʜᴇ ᴜꜱᴇʀ ɪᴅ "
            "ᴛᴏ ʀᴇᴍᴏᴠᴇ ᴀɴ ᴀᴅᴍɪɴ."
        )

    # =====================================================
    # F-SUB LIST
    # =====================================================

    if q.data == "fsub":

        rows = db.list_fsub()

        lines = [
            "<b>📢 ꜰꜱᴜʙ ᴄʜᴀɴɴᴇʟꜱ</b>",
            ""
        ]

        for r in rows:

            lines.append(
                f'• <a href="{r.get("invite_link", "")}">'
                f'{r.get("title", "ᴄʜᴀɴɴᴇʟ")}</a> — '
                f'<code>{r["channel_id"]}</code>'
            )

        kb = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "➕ ᴀᴅᴅ ꜰꜱᴜʙ",
                        callback_data="add_fsub"
                    )
                ],
                [
                    InlineKeyboardButton(
                        "➖ ʀᴇᴍᴏᴠᴇ ꜰꜱᴜʙ",
                        callback_data="remove_fsub"
                    )
                ],
                [
                    InlineKeyboardButton(
                        "↩️ ʙᴀᴄᴋ",
                        callback_data="settings_back"
                    )
                ]
            ]
        )

        return await q.message.edit_text(
            "\n".join(lines),
            parse_mode="HTML",
            reply_markup=kb
        )

    # =====================================================
    # ADD F-SUB
    # =====================================================

    if q.data == "add_fsub":

        if uid != OWNER_ID:

            return await q.answer(
                "❌ ᴏɴʟʏ ᴏᴡɴᴇʀ ᴄᴀɴ ᴀᴅᴅ ꜰꜱᴜʙ.",
                show_alert=True
            )

        _pending_fsub.add(uid)

        return await q.message.reply_text(
            "📢 ꜱᴇɴᴅ ᴛʜᴇ ᴄʜᴀɴɴᴇʟ ɪᴅ ᴏɴʟʏ.\n"
            "ᴛʜᴇ ʙᴏᴛ ᴡɪʟʟ ɢᴇᴛ ᴛʜᴇ ᴄʜᴀɴɴᴇʟ ɴᴀᴍᴇ ᴀɴᴅ "
            "ɪɴᴠɪᴛᴇ ʟɪɴᴋ ᴀᴜᴛᴏᴍᴀᴛɪᴄᴀʟʟʏ."
        )

    # =====================================================
    # REMOVE F-SUB
    # =====================================================

    if q.data == "remove_fsub":

        if uid != OWNER_ID:

            return await q.answer(
                "❌ ᴏɴʟʏ ᴏᴡɴᴇʀ ᴄᴀɴ ʀᴇᴍᴏᴠᴇ ꜰꜱᴜʙ.",
                show_alert=True
            )

        _pending_fsub.add(
            uid * 1000000000 + 1
        )

        return await q.message.reply_text(
            "➖ ꜱᴇɴᴅ ᴛʜᴇ ꜰꜱᴜʙ ᴄʜᴀɴɴᴇʟ ɪᴅ ᴛᴏ ʀᴇᴍᴏᴠᴇ."
        )

    # =====================================================
    # SETTINGS BACK
    # =====================================================

    if q.data == "settings_back":

        return await q.message.edit_text(
            settings_text(),
            parse_mode="HTML",
            reply_markup=settings_keyboard()
        )


# =========================================================
# SETTINGS INPUT
# =========================================================

async def settings_input(update, context):

    uid = update.effective_user.id

    if not admin_ok(uid):
        return

    # =====================================================
    # START IMAGE
    # =====================================================

    if uid in _pending_image and update.message.photo:

        db.set_setting(
            "start_image",
            update.message.photo[-1].file_id
        )

        _pending_image.discard(uid)

        return await update.message.reply_text(
            "✅ ꜱᴛᴀʀᴛ ɪᴍᴀɢᴇ ᴜᴘᴅᴀᴛᴇᴅ ꜱᴜᴄᴄᴇꜱꜱꜰᴜʟʟʏ."
        )

    # =====================================================
    # AUTO DELETE
    # =====================================================

    if uid in _pending_autodelete:

        try:

            minutes = max(
                0,
                int(update.message.text.strip())
            )

            db.set_setting(
                "auto_delete_minutes",
                minutes
            )

            _pending_autodelete.discard(uid)

            return await update.message.reply_text(
                f"✅ ᴀᴜᴛᴏ ᴅᴇʟᴇᴛᴇ ꜱᴇᴛ ᴛᴏ "
                f"{minutes} ᴍɪɴᴜᴛᴇꜱ."
            )

        except Exception:

            return await update.message.reply_text(
                "❌ ꜱᴇɴᴅ ᴀ ᴠᴀʟɪᴅ ɴᴜᴍʙᴇʀ."
            )

    # =====================================================
    # ADD / REMOVE ADMIN
    # =====================================================

    if (
        uid in _pending_admin
        or -uid in _pending_admin
    ):

        if uid != OWNER_ID:

            _pending_admin.discard(uid)
            _pending_admin.discard(-uid)

            return await update.message.reply_text(
                "❌ ᴏɴʟʏ ᴏᴡɴᴇʀ ᴄᴀɴ ᴀᴅᴅ "
                "ᴏʀ ʀᴇᴍᴏᴠᴇ ᴀᴅᴍɪɴꜱ."
            )

        try:

            target_uid = int(
                update.message.text.strip()
            )

        except Exception:

            return await update.message.reply_text(
                "❌ ᴇɴᴛᴇʀ ᴀ ᴠᴀʟɪᴅ ᴜꜱᴇʀ ɪᴅ."
            )

        remove_mode = (
            -uid in _pending_admin
        )

        _pending_admin.discard(uid)
        _pending_admin.discard(-uid)

        if remove_mode:

            db.remove_admin(
                target_uid
            )

            return await update.message.reply_text(
                "✅ ᴀᴅᴍɪɴ ʀᴇᴍᴏᴠᴇᴅ."
            )

        db.add_admin(
            target_uid
        )

        return await update.message.reply_text(
            "✅ ᴀᴅᴍɪɴ ᴀᴅᴅᴇᴅ."
        )

    # =====================================================
    # ADD F-SUB
    # =====================================================

    if uid in _pending_fsub:

        if uid != OWNER_ID:

            _pending_fsub.discard(uid)

            return await update.message.reply_text(
                "❌ ᴏɴʟʏ ᴏᴡɴᴇʀ ᴄᴀɴ ᴀᴅᴅ ꜰꜱᴜʙ."
            )

        try:

            cid = int(
                update.message.text.strip()
            )

            chat = await context.bot.get_chat(cid)

            invite = await context.bot.create_chat_invite_link(
                cid,
                name="File Store FSub"
            )

            db.add_fsub(
                cid,
                invite.invite_link,
                chat.title or chat.username or str(cid)
            )

            _pending_fsub.discard(uid)

            return await update.message.reply_text(
                f"✅ <b>ꜰꜱᴜʙ ᴀᴅᴅᴇᴅ</b>\n\n"
                f"📢 <a href=\"{invite.invite_link}\">"
                f"{chat.title or 'Channel'}</a>\n"
                f"🆔 <code>{cid}</code>\n"
                f"🔗 <a href=\"{invite.invite_link}\">"
                f"ɪɴᴠɪᴛᴇ ʟɪɴᴋ</a>",
                parse_mode="HTML"
            )

        except Exception as e:

            return await update.message.reply_text(
                "❌ ᴄᴏᴜʟᴅ ɴᴏᴛ ᴀᴅᴅ ᴛʜɪꜱ ᴄʜᴀɴɴᴇʟ.\n"
                "ᴍᴀᴋᴇ ꜱᴜʀᴇ ᴛʜᴇ ʙᴏᴛ ɪꜱ ᴀᴅᴍɪɴ.\n\n"
                f"<code>{e}</code>",
                parse_mode="HTML"
            )

    # =====================================================
    # REMOVE F-SUB
    # =====================================================

    remkey = uid * 1000000000 + 1

    if remkey in _pending_fsub:

        if uid != OWNER_ID:

            _pending_fsub.discard(remkey)

            return await update.message.reply_text(
                "❌ ᴏɴʟʏ ᴏᴡɴᴇʀ ᴄᴀɴ ʀᴇᴍᴏᴠᴇ ꜰꜱᴜʙ."
            )

        try:

            cid = update.message.text.strip()

            db.del_fsub(cid)

            _pending_fsub.discard(remkey)

            return await update.message.reply_text(
                "✅ ꜰꜱᴜʙ ᴄʜᴀɴɴᴇʟ ʀᴇᴍᴏᴠᴇᴅ."
            )

        except Exception:

            return await update.message.reply_text(
                "❌ ᴄᴏᴜʟᴅ ɴᴏᴛ ʀᴇᴍᴏᴠᴇ."
            )


# =========================================================
# PREMIUM
# =========================================================

async def addsubs(update, context):

    if not admin_ok(update.effective_user.id):

        return await update.message.reply_text(
            "❌ ᴜɴᴀᴜᴛʜᴏʀɪᴢᴇᴅ."
        )

    if len(context.args) != 2:

        return await update.message.reply_text(
            "ᴜꜱᴀɢᴇ: /ᴀᴅᴅꜱᴜʙꜱ USER_ID DAYS"
        )

    try:

        uid = int(context.args[0])
        days = int(context.args[1])

        assert days > 0

    except Exception:

        return await update.message.reply_text(
            "❌ ᴜꜱᴇ: /ᴀᴅᴅꜱᴜʙꜱ USER_ID DAYS"
        )

    try:

        try:

            u = await context.bot.get_chat(uid)

            db.add_user(
                uid,
                u.username or "",
                u.first_name or ""
            )

        except Exception:

            u = None

        start, expiry = db.add_premium(
            uid,
            days
        )

        name = (
            u.first_name
            if u
            else None
        ) or "ᴜsᴇʀ"

        await context.bot.send_message(
            uid,
            f"🎉 <b>Congratulations!</b>\n\n"
            f"Your account has been upgraded to the "
            f"Premium Ad-Free Tier for the next {days} Days.\n"
            f"Enjoy high-speed bypass-free file downloads!",
            parse_mode="HTML"
        )

        await update.message.reply_text(
            f"<b>✅ Premium Tier Activated Successfully!</b>\n\n"
            f"👤 Name: {name}\n"
            f"🆔 User ID: {uid}\n"
            f"⏳ Duration Allocated: {days} Days",
            parse_mode="HTML"
        )

    except Exception as e:

        await update.message.reply_text(
            f"❌ {e}"
        )


async def removesubs(update, context):

    if not admin_ok(update.effective_user.id):

        return await update.message.reply_text(
            "❌ ᴜɴᴀᴜᴛʜᴏʀɪᴢᴇᴅ."
        )

    if len(context.args) != 1:

        return await update.message.reply_text(
            "ᴜꜱᴀɢᴇ: /ʀᴇᴍᴏᴠᴇꜱᴜʙꜱ USER_ID"
        )

    uid = int(context.args[0])

    old = db.remove_premium(uid)

    if old:

        try:

            await context.bot.send_message(
                uid,
                "🚨 <b>Notification:</b> "
                "Your premium subscription package has "
                "been manually revoked by the management team.",
                parse_mode="HTML"
            )

        except Exception:
            pass

    await update.message.reply_text(
        f"✅ ᴘʀᴇᴍɪᴜᴍ ʀᴇᴍᴏᴠᴇᴅ.\n\n"
        f"ᴜꜱᴇʀ: <code>{uid}</code>",
        parse_mode="HTML"
    )


async def myplan(update, context):

    uid = update.effective_user.id

    plan = db.get_premium(uid)

    if not plan:

        return await update.message.reply_text(
            "💎 <b>ᴍʏ ᴘʟᴀɴ</b>\n\n"
            "🔴 ꜱᴛᴀᴛᴜꜱ: ꜰʀᴇᴇ ᴘʟᴀɴ\n\n"
            "⚡ ꜱʜᴏʀᴛᴇɴᴇʀ ʙʏᴘᴀꜱꜱ: ᴅɪꜱᴀʙʟᴇᴅ\n\n"
            "💎 ɢᴇᴛ ᴘʀᴇᴍɪᴜᴍ ᴛᴏ ᴇɴᴊᴏʏ "
            "ꜱʜᴏʀᴛᴇɴᴇʀ-ꜰʀᴇᴇ ᴅᴏᴡɴʟᴏᴀᴅꜱ.\n\n"
            'ᴄᴏɴᴛᴀᴄᴛ: <a href="https://t.me/Its_Lozo">'
            "@ɪᴛꜱ_ʟᴏᴢᴏ</a>",
            parse_mode="HTML",
            link_preview_options=LinkPreviewOptions(
                is_disabled=True
            )
        )

    try:

        start = datetime.fromisoformat(
            plan["starts_at"].replace(
                "Z",
                "+00:00"
            )
        )

        expiry = datetime.fromisoformat(
            plan["expires_at"].replace(
                "Z",
                "+00:00"
            )
        )

        if start.tzinfo is None:
            start = start.replace(
                tzinfo=timezone.utc
            )

        if expiry.tzinfo is None:
            expiry = expiry.replace(
                tzinfo=timezone.utc
            )

        now = datetime.now(
            timezone.utc
        )

        remaining = expiry - now

        total_seconds = max(
            0,
            int(
                remaining.total_seconds()
            )
        )

        days = total_seconds // 86400

        hours = (
            total_seconds % 86400
        ) // 3600

        minutes = (
            total_seconds % 3600
        ) // 60

        start_text = start.astimezone(
            IST
        ).strftime(
            "%d-%m-%Y %I:%M:%S %p"
        )

        expiry_text = expiry.astimezone(
            IST
        ).strftime(
            "%d-%m-%Y %I:%M:%S %p"
        )

        if total_seconds <= 0:

            return await update.message.reply_text(
                "💎 <b>ᴍʏ ᴘʟᴀɴ</b>\n\n"
                "🔴 ꜱᴛᴀᴛᴜꜱ: ᴘʀᴇᴍɪᴜᴍ ᴇxᴘɪʀᴇᴅ\n\n"
                "⚡ ꜱʜᴏʀᴛᴇɴᴇʀ ʙʏᴘᴀꜱꜱ: ᴅɪꜱᴀʙʟᴇᴅ",
                parse_mode="HTML"
            )

        remaining_text = (
            f"{days} ᴅᴀʏꜱ, "
            f"{hours} ʜᴏᴜʀꜱ, "
            f"{minutes} ᴍɪɴᴜᴛᴇꜱ"
        )

        text = (
            "💎 <b>ᴍʏ ᴘʟᴀɴ</b>\n\n"
            "🟢 ꜱᴛᴀᴛᴜꜱ: ᴘʀᴇᴍɪᴜᴍ ᴀᴄᴛɪᴠᴇ\n\n"
            f"📅 ᴀᴄᴛɪᴠᴀᴛᴇᴅ ᴏɴ:\n"
            f"<code>{start_text} IST</code>\n\n"
            f"⏳ ᴇxᴘɪʀᴇꜱ ᴏɴ:\n"
            f"<code>{expiry_text} IST</code>\n\n"
            f"📆 ᴛɪᴍᴇ ʀᴇᴍᴀɪɴɪɴɢ:\n"
            f"<code>{remaining_text}</code>\n\n"
            "⚡ ꜱʜᴏʀᴛᴇɴᴇʀ ʙʏᴘᴀꜱꜱ: ᴇɴᴀʙʟᴇᴅ\n\n"
            "ᴛʜᴀɴᴋ ʏᴏᴜ ꜰᴏʀ ᴜꜱɪɴɢ ᴏᴜʀ ꜱᴇʀᴠɪᴄᴇ ❤️"
        )

        return await update.message.reply_text(
            text,
            parse_mode="HTML"
        )

    except Exception:

        log.exception("Myplan failed")

        return await update.message.reply_text(
            "❌ ᴄᴏᴜʟᴅ ɴᴏᴛ ʟᴏᴀᴅ ʏᴏᴜʀ ᴘʟᴀɴ ᴅᴇᴛᴀɪʟꜱ."
        )


async def list_premium(update, context):

    if not admin_ok(update.effective_user.id):

        return await update.message.reply_text(
            "❌ ᴜɴᴀᴜᴛʜᴏʀɪᴢᴇᴅ."
        )

    rows = db.list_premium()

    if not rows:

        return await update.message.reply_text(
            "📋 ɴᴏ ᴀᴄᴛɪᴠᴇ ᴘʀᴇᴍɪᴜᴍ ꜱᴜʙꜱᴄʀɪᴘᴛɪᴏɴꜱ."
        )

    lines = [
        "<b>💎 ᴘʀᴇᴍɪᴜᴍ ꜱᴜʙꜱᴄʀɪʙᴇʀꜱ</b>",
        ""
    ]

    users_cache = db.list_users()

    for i, r in enumerate(rows, 1):

        uid = int(r["user_id"])

        u = next(
            (
                x for x in users_cache
                if int(x["user_id"]) == uid
            ),
            {}
        )

        def fmt(v):

            return datetime.fromisoformat(
                v.replace("Z", "+00:00")
            ).astimezone(
                IST
            ).strftime(
                "%d-%m-%Y %I:%M:%S %p"
            )

        lines.append(
            f"<b>#{i}</b>\n"
            f'👤 <a href="tg://user?id={uid}">'
            f'{u.get("first_name") or "User"}</a>\n'
            f"🔹 Username: @{u.get('username')}\n"
            f"🆔 User ID: <code>{uid}</code>\n"
            f"🟢 Start: <code>"
            f"{fmt(r.get('starts_at', r['expires_at']))}"
            f" IST</code>\n"
            f"🔴 End: <code>"
            f"{fmt(r['expires_at'])}"
            f" IST</code>\n"
        )

    text = "\n".join(lines)

    for pos in range(0, len(text), 3900):

        await update.message.reply_text(
            text[pos:pos + 3900],
            parse_mode="HTML",
            disable_web_page_preview=True
        )


# =========================================================
# BAN / UNBAN
# =========================================================

async def banuser(update, context):

    uid = update.effective_user.id

    if not admin_ok(uid):

        return await update.message.reply_text(
            "❌ ᴜɴᴀᴜᴛʜᴏʀɪᴢᴇᴅ."
        )

    if len(context.args) != 1:

        return await update.message.reply_text(
            "❌ ᴜꜱᴀɢᴇ:\n/banuser USER_ID"
        )

    try:

        target_id = int(
            context.args[0]
        )

    except ValueError:

        return await update.message.reply_text(
            "❌ ᴘʟᴇᴀꜱᴇ ᴇɴᴛᴇʀ ᴀ ᴠᴀʟɪᴅ ᴜꜱᴇʀ ɪᴅ."
        )

    if target_id == OWNER_ID:

        return await update.message.reply_text(
            "❌ ᴏᴡɴᴇʀ ᴄᴀɴɴᴏᴛ ʙᴇ ʙᴀɴɴᴇᴅ."
        )

    if db.is_banned(target_id):

        return await update.message.reply_text(
            "⚠️ ᴛʜɪꜱ ᴜꜱᴇʀ ɪꜱ ᴀʟʀᴇᴀᴅʏ ʙᴀɴɴᴇᴅ."
        )

    db.ban_user(target_id)

    try:

        await context.bot.send_message(
            target_id,
            "🚫 <b>You Are Banned From Using The Bot</b> 🚫",
            parse_mode="HTML"
        )

    except Exception:
        pass

    await update.message.reply_text(
        f"✅ <b>ᴜꜱᴇʀ ʙᴀɴɴᴇᴅ ꜱᴜᴄᴄᴇꜱꜱꜰᴜʟʟʏ.</b>\n\n"
        f"🆔 <code>{target_id}</code>",
        parse_mode="HTML"
    )


async def unbanuser(update, context):

    uid = update.effective_user.id

    if not admin_ok(uid):

        return await update.message.reply_text(
            "❌ ᴜɴᴀᴜᴛʜᴏʀɪᴢᴇᴅ."
        )

    if len(context.args) != 1:

        return await update.message.reply_text(
            "❌ ᴜꜱᴀɢᴇ:\n/unbanuser USER_ID"
        )

    try:

        target_id = int(
            context.args[0]
        )

    except ValueError:

        return await update.message.reply_text(
            "❌ ᴘʟᴇᴀꜱᴇ ᴇɴᴛᴇʀ ᴀ ᴠᴀʟɪᴅ ᴜꜱᴇʀ ɪᴅ."
        )

    if not db.is_banned(target_id):

        return await update.message.reply_text(
            "⚠️ ᴛʜɪꜱ ᴜꜱᴇʀ ɪꜱ ɴᴏᴛ ʙᴀɴɴᴇᴅ."
        )

    db.unban_user(target_id)

    try:

        await context.bot.send_message(
            target_id,
            "✅ <b>Your ban has been removed.</b>\n\n"
            "You can use the bot again.",
            parse_mode="HTML"
        )

    except Exception:
        pass

    await update.message.reply_text(
        f"✅ <b>ᴜꜱᴇʀ ᴜɴʙᴀɴɴᴇᴅ ꜱᴜᴄᴄᴇꜱꜰᴜʟʟʏ.</b>\n\n"
        f"🆔 <code>{target_id}</code>",
        parse_mode="HTML"
    )


async def banuser_list(update, context):

    uid = update.effective_user.id

    if not admin_ok(uid):

        return await update.message.reply_text(
            "❌ ᴜɴᴀᴜᴛʜᴏʀɪᴢᴇᴅ."
        )

    banned_users = db.list_banned_users()

    if not banned_users:

        return await update.message.reply_text(
            "📋 <b>ᴜɴɢᴀɴɢ ᴜꜱᴇʀ ʟɪꜱᴛ</b>\n\n"
            "✅ ɴᴏ ᴜꜱᴇʀ ɪꜱ ᴄᴜʀʀᴇɴᴛʟʏ ʙᴀɴɴᴇᴅ.",
            parse_mode="HTML"
        )

    lines = [
        "🚫 <b>ʙᴀɴɴᴇᴅ ᴜꜱᴇʀꜱ</b>",
        ""
    ]

    for i, row in enumerate(
        banned_users,
        1
    ):

        user_id = int(
            row["user_id"]
        )

        try:

            user = await context.bot.get_chat(
                user_id
            )

            name = (
                user.full_name
                or "Unknown"
            )

            username = (
                f"@{user.username}"
                if user.username
                else "No Username"
            )

            profile_link = (
                f'<a href="tg://user?id={user_id}">'
                f'{name}</a>'
            )

        except Exception:

            name = "Unknown"
            username = "No Username"

            profile_link = (
                f'<a href="tg://user?id={user_id}">'
                f"Unknown User</a>"
            )

        lines.append(
            f"<b>{i}.</b> {profile_link}\n"
            f"👤 Name: <code>{name}</code>\n"
            f"🔗 Username: {username}\n"
            f"🆔 UID: <code>{user_id}</code>\n"
            f"━━━━━━━━━━━━━━"
        )

    text = "\n".join(lines)

    if len(text) <= 4000:

        return await update.message.reply_text(
            text,
            parse_mode="HTML",
            disable_web_page_preview=True
        )

    chunk = ""

    for line in lines:

        if len(chunk) + len(line) + 1 > 4000:

            await update.message.reply_text(
                chunk,
                parse_mode="HTML",
                disable_web_page_preview=True
            )

            chunk = ""

        chunk += line + "\n"

    if chunk:

        await update.message.reply_text(
            chunk,
            parse_mode="HTML",
            disable_web_page_preview=True
        )


# =========================================================
# USERS
# =========================================================

async def users(update, context):

    if not admin_ok(update.effective_user.id):

        return await update.message.reply_text(
            "❌ ᴜɴᴀᴜᴛʜᴏʀɪᴢᴇᴅ."
        )

    rows = db.list_users()

    lines = [
        f"<b>👥 ᴜꜱᴇʀꜱ: {len(rows)}</b>",
        ""
    ]

    for r in rows:

        uid = int(r["user_id"])

        name = (
            r.get("first_name")
            or "User"
        )

        uname = (
            "@" + r["username"]
            if r.get("username")
            else "—"
        )

        lines.append(
            f'• <a href="tg://user?id={uid}">'
            f"{name}</a> | "
            f"{uname} | "
            f"<code>{uid}</code>"
        )

    text = "\n".join(lines)

    for pos in range(
        0,
        len(text),
        3900
    ):

        await update.message.reply_text(
            text[pos:pos + 3900],
            parse_mode="HTML",
            disable_web_page_preview=True
        )


# =========================================================
# BROADCAST
# =========================================================

async def broadcast(update, context):

    if not admin_ok(update.effective_user.id):

        return await update.message.reply_text(
            "❌ ᴜɴᴀᴜᴛʜᴏʀɪᴢᴇᴅ."
        )

    replied = update.message.reply_to_message

    if not replied:

        return await update.message.reply_text(
            "ʀᴇᴘʟʏ ᴛᴏ ᴛʜᴇ ᴍᴇꜱꜱᴀɢᴇ "
            "ʏᴏᴜ ᴡᴀɴᴛ ᴛᴏ ʙʀᴏᴀᴅᴄᴀꜱᴛ.\n\n"
            "/ʙʀᴏᴀᴅᴄᴀꜱᴛ\n"
            "/ʙʀᴏᴀᴅᴄᴀꜱᴛ 24ʜ"
        )

    arg = (
        context.args[0].lower()
        if context.args
        else ""
    )

    delete_after = None
    mode = "BROADCAST"
    lifespan = "Permanent"

    if arg.endswith("h"):

        try:

            hours = int(
                arg[:-1]
            )

            if hours <= 0:
                raise ValueError

            delete_after = (
                datetime.now(timezone.utc)
                + timedelta(hours=hours)
            )

            mode = "PBROADCAST"
            lifespan = arg

        except Exception:

            pass

    rows = db.list_users()

    total = len(rows)

    success = 0
    blocked = 0
    failed = 0

    sent = []

    # =====================================================
    # IMPORTANT:
    # Explicitly preserve the original message buttons.
    #
    # replied.reply_markup contains the inline keyboard
    # attached to the message being broadcast.
    # =====================================================

    original_markup = replied.reply_markup

    for r in rows:

        uid = int(
            r["user_id"]
        )

        try:

            copied = await context.bot.copy_message(
                chat_id=uid,
                from_chat_id=replied.chat_id,
                message_id=replied.message_id,

                # THIS IS THE BROADCAST BUTTON FIX
                reply_markup=original_markup
            )

            success += 1

            sent.append(
                (
                    uid,
                    copied.message_id
                )
            )

        except Exception as e:

            error_text = str(e).lower()

            if (
                "blocked" in error_text
                or "chat not found" in error_text
                or "user is deactivated" in error_text
            ):

                blocked += 1

                try:
                    db.delete_user(uid)
                except Exception:
                    pass

            else:

                failed += 1

                log.warning(
                    "Broadcast failed for %s: %s",
                    uid,
                    e
                )

    # =====================================================
    # SCHEDULE BROADCAST DELETE
    # =====================================================

    if delete_after:

        async def delete_broadcast_job(ctx):

            for uid, mid in sent:
async def broadcast(update, context):
    if not admin_ok(update.effective_user.id):
        return await update.message.reply_text("❌ ᴜɴᴀᴜᴛʜᴏʀɪᴢᴇᴅ.")

    replied = update.message.reply_to_message

    if not replied:
        return await update.message.reply_text(
            "ʀᴇᴘʟʏ ᴛᴏ ᴛʜᴇ ᴍᴇꜱꜱᴀɢᴇ ʏᴏᴜ ᴡᴀɴᴛ ᴛᴏ ʙʀᴏᴀᴅᴄᴀꜱᴛ.\n\n"
            "/broadcast\n"
            "/broadcast 24h"
        )

    arg = context.args[0].lower() if context.args else ""

    delete_after = None
    mode = "BROADCAST"
    lifespan = "Permanent"

    if arg.endswith("h"):
        try:
            hours = int(arg[:-1])

            if hours > 0:
                delete_after = datetime.now(timezone.utc) + timedelta(hours=hours)
                mode = "PBROADCAST"
                lifespan = arg

        except (ValueError, TypeError):
            pass

    rows = db.list_users()

    total = len(rows)
    success = 0
    blocked = 0
    failed = 0

    sent_messages = []

    # Preserve buttons from the original message
    original_markup = replied.reply_markup

    for r in rows:
        try:
            uid = int(r["user_id"])

            copied = await context.bot.copy_message(
                chat_id=uid,
                from_chat_id=replied.chat_id,
                message_id=replied.message_id,
                reply_markup=original_markup
            )

            success += 1
            sent_messages.append((uid, copied.message_id))

        except Exception as e:
            error_text = str(e).lower()

            if "blocked" in error_text or "chat not found" in error_text:
                blocked += 1

                try:
                    db.delete_user(uid)
                except Exception:
                    pass

            else:
                failed += 1

    # Schedule deletion ONLY for the broadcast copies
    if delete_after and sent_messages:

        delay = (
            delete_after - datetime.now(timezone.utc)
        ).total_seconds()

        async def delete_broadcast_job(context):
            for uid, message_id in sent_messages:
                try:
                    await context.bot.delete_message(
                        chat_id=uid,
                        message_id=message_id
                    )
                except Exception:
                    pass

        context.job_queue.run_once(
            delete_broadcast_job,
            max(1, delay)
        )

    # Completion statistics
    stats = (
        "📢 <b>BROADCAST COMPLETED!</b>\n\n"
        "📊 <b>Stats Report:</b>\n"
        f"• Total Users DB: {total}\n"
        f"• Successful: {success}\n"
        f"• Blocked Users Wiped: {blocked}\n"
        "• Deleted Accounts Wiped: 0\n"
        f"• Unsuccessful/Failed: {failed}\n\n"
        f"⚙️ Config Mode: {mode}\n"
        f"⏱ Task Lifespan: {lifespan}"
    )

    # IMPORTANT:
    # This message is sent to the admin and is NOT included
    # in the scheduled deletion list.
    try:
        await update.message.reply_text(
            stats,
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"Failed to send broadcast stats: {e}")
        
# =========================================================
# DB CHANNEL INDEXER
# =========================================================

async def channel_post_indexer(update, context):

    post = update.channel_post

    if not post:
        return

    if post.chat_id != DB_CHANNEL_ID:
        return

    try:

        db.add_file(
            DB_CHANNEL_ID,
            post.message_id,
            post.caption
            or post.text
            or ""
        )

        token = db.create_main_link(
            f"message:{DB_CHANNEL_ID}:{post.message_id}"
        )

        url = main_link_url(token)

        markup = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "↗ ꜱʜᴀʀᴇ ᴜʀʟ",
                        url=share_url(url)
                    )
                ]
            ]
        )

        try:

            await context.bot.edit_message_reply_markup(
                chat_id=DB_CHANNEL_ID,
                message_id=post.message_id,
                reply_markup=markup
            )

        except Exception:

            log.exception(
                "Could not add share button to DB message"
            )

    except Exception:

        log.exception(
            "DB channel indexing failed"
        )


# =========================================================
# MAIN
# =========================================================

def main():

    # =====================================================
    # HEALTH SERVER
    # =====================================================

    threading.Thread(
        target=start_health_server,
        daemon=True
    ).start()

    # =====================================================
    # TELEGRAM APPLICATION
    # =====================================================

    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .build()
    )

    # =====================================================
    # GLOBAL BAN GUARD
    # =====================================================

    app.add_handler(
        MessageHandler(
            filters.ALL,
            ban_guard
        ),
        group=-1
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
            "genlink",
            genlink
        )
    )

    app.add_handler(
        CommandHandler(
            "batch",
            batch
        )
    )

    app.add_handler(
        CommandHandler(
            "settings",
            settings
        )
    )

    app.add_handler(
        CommandHandler(
            "banuser",
            banuser
        )
    )

    app.add_handler(
        CommandHandler(
            "unbanuser",
            unbanuser
        )
    )

    app.add_handler(
        CommandHandler(
            "banuser_list",
            banuser_list
        )
    )

    app.add_handler(
        CommandHandler(
            "addsubs",
            addsubs
        )
    )

    app.add_handler(
        CommandHandler(
            "removesubs",
            removesubs
        )
    )

    app.add_handler(
        CommandHandler(
            "list_premium",
            list_premium
        )
    )

    app.add_handler(
        CommandHandler(
            "users",
            users
        )
    )

    app.add_handler(
        CommandHandler(
            "broadcast",
            broadcast
        )
    )

    app.add_handler(
        CommandHandler(
            "myplan",
            myplan
        )
    )

    # =====================================================
    # CALLBACK HANDLER
    # =====================================================

    app.add_handler(
        CallbackQueryHandler(
            callback
        )
    )

    # =====================================================
    # SETTINGS INPUT
    # =====================================================

    app.add_handler(
        MessageHandler(
            filters.PHOTO
            | (
                filters.TEXT
                & ~filters.COMMAND
            ),
            settings_input
        ),
        group=1
    )

    # =====================================================
    # DB CHANNEL INDEXER
    # =====================================================

    app.add_handler(
        MessageHandler(
            filters.ALL,
            channel_post_indexer
        ),
        group=10
    )

    # =====================================================
    # START
    # =====================================================

    log.info(
        "Bot starting"
    )

    app.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True,
        close_loop=False
    )


# =========================================================
# ENTRY POINT
# =========================================================

if __name__ == "__main__":
    main()
