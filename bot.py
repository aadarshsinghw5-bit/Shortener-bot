import os
import logging
import re
import threading
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import quote
from html import escape
from functools import wraps

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
# BASIC CONFIG
# =========================================================

load_dotenv()

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)

log = logging.getLogger(
    "file-store-bot"
)

for name in (
    "httpx",
    "httpcore",
    "telegram",
    "telegram.ext",
):
    logging.getLogger(
        name
    ).setLevel(
        logging.WARNING
    )


BOT_TOKEN = os.environ["BOT_TOKEN"]

BOT_USERNAME = os.environ[
    "BOT_USERNAME"
].lstrip("@")

OWNER_ID = int(
    os.environ["OWNER_ID"]
)

DB_CHANNEL_ID = int(
    os.environ["DB_CHANNEL_ID"]
)

IST = ZoneInfo(
    "Asia/Kolkata"
)


# =========================================================
# ANTI-BYPASS
# =========================================================

BYPASS_MIN_SECONDS = 90


db = Database()

shortener = Shortener(
    db
)


# =========================================================
# PENDING STATES
# =========================================================

_pending_image = set()

_pending_autodelete = set()

_pending_admin = set()

_pending_fsub = set()

# =========================================================
# COMMAND WAIT MESSAGE
# =========================================================

def with_wait(wait_text="ᴘʟᴇᴀꜱᴇ ᴡᴀɪᴛ....."):
    def decorator(handler):
        @wraps(handler)
        async def wrapped(update, context):
            wait_message = None

            try:
                if update.message:
                    wait_message = await update.message.reply_text(
                        wait_text
                    )

                return await handler(update, context)

            finally:
                if wait_message:
                    try:
                        await wait_message.delete()
                    except Exception:
                        pass

        return wrapped

    return decorator

# =========================================================
# HEALTH SERVER
# =========================================================

class HealthHandler(
    BaseHTTPRequestHandler
):

    def do_GET(self):
        self.send_response(200)

        self.send_header(
            "Content-Type",
            "application/json",
        )

        self.end_headers()

        self.wfile.write(
            b'{"ok":true,"service":"telegram-file-store-bot"}'
        )

    def do_HEAD(self):
        self.send_response(200)

        self.send_header(
            "Content-Type",
            "application/json",
        )

        self.send_header(
            "Content-Length",
            "49",
        )

        self.end_headers()

    def log_message(
        self,
        format,
        *args,
    ):
        pass


def start_health_server():
    port = int(
        os.environ.get(
            "PORT",
            "10000",
        )
    )

    server = HTTPServer(
        (
            "0.0.0.0",
            port,
        ),
        HealthHandler,
    )

    log.info(
        "Health server running on port %s",
        port,
    )

    server.serve_forever()


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

    if user.id == OWNER_ID:
        return

    try:
        banned = db.is_banned(
            user.id
        )

    except Exception:
        log.exception(
            "Ban check failed"
        )
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
    return db.get_setting(
        "start_image",
        "",
    )


def start_caption():
    return (
        "<i>ʜɪ ᴛʜᴇʀᴇ....! 💥</i>\n\n"
        "ɪ ᴀᴍ ᴀ ꜰɪʟᴇ-ꜱᴛᴏʀᴇ ʙᴏᴛ.\n"
        "ɪ ᴄᴀɴ ɢᴇɴᴇʀᴀᴛᴇ ʟɪɴᴋꜱ "
        "ᴅɪʀᴇᴄᴛʟʏ ᴡɪᴛʜ ɴᴏ ᴘʀᴏʙʟᴇᴍꜱ.\n\n"
        '<b>ᴍʏ ᴏᴡɴᴇʀ:</b> '
        '<a href="https://t.me/Its_Lozo">'
        "@ɪᴛꜱ_ʟᴏᴢᴏ"
        "</a>"
    )


def about_caption():
    return (
        "<b>ᴀʙᴏᴜᴛ ᴜꜱ..</b>\n\n"
        '➤ ᴍᴀᴅᴇ ꜰᴏʀ : '
        '<a href="https://t.me/Anime_Hub_94">'
        "ᴀɴɪᴍᴇ ʜᴜʙ"
        "</a>\n"
        '➤ ᴏᴡɴᴇʀ : '
        '<a href="https://t.me/Its_Lozo">'
        "@ɪᴛꜱ_ʟᴏᴢᴏ"
        "</a>\n"
        '➤ ᴅᴇᴠᴇʟᴏᴘᴇʀ : '
        '<a href="https://t.me/Its_Lozo">'
        "@ɪᴛꜱ_ʟᴏᴢᴏ"
        "</a>\n\n"
        "ᴀᴅɪᴏs !!"
    )


def start_keyboard():
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "ᴀʙᴏᴜᴛ",
                    callback_data="about",
                ),
                InlineKeyboardButton(
                    "ᴄʟᴏꜱᴇ",
                    callback_data="close",
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
                    callback_data="back",
                ),
                InlineKeyboardButton(
                    "ᴄʟᴏꜱᴇ",
                    callback_data="close",
                ),
            ]
        ]
    )


async def render_start(
    message,
):
    image = start_image()

    if image:
        try:
            return await message.reply_photo(
                photo=image,
                caption=start_caption(),
                parse_mode="HTML",
                reply_markup=start_keyboard(),
            )
        except Exception:
            log.exception(
                "Could not send start image"
            )

    await message.reply_text(
        start_caption(),
        parse_mode="HTML",
        reply_markup=start_keyboard(),
    )


async def edit_start(q):
    image = start_image()

    if image:
        try:
            return await q.edit_message_media(
                media=InputMediaPhoto(
                    media=image,
                    caption=start_caption(),
                    parse_mode="HTML",
                ),
                reply_markup=start_keyboard(),
            )
        except Exception:
            pass

    try:
        await q.edit_message_text(
            start_caption(),
            parse_mode="HTML",
            reply_markup=start_keyboard(),
        )

    except Exception:
        pass


async def edit_about(q):
    try:
        await q.edit_message_caption(
            caption=about_caption(),
            parse_mode="HTML",
            reply_markup=about_keyboard(),
        )

    except Exception:
        try:
            await q.edit_message_text(
                about_caption(),
                parse_mode="HTML",
                reply_markup=about_keyboard(),
            )

        except Exception:
            pass


# =========================================================
# FORCE SUB
# =========================================================

async def is_fsub_member(
    bot,
    user_id,
):
    missing = []

    for row in db.list_fsub():
        try:
            member = await bot.get_chat_member(
                int(row["channel_id"]),
                user_id,
            )

            if member.status in (
                ChatMemberStatus.LEFT,
                ChatMemberStatus.BANNED,
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
                        (
                            f"ᴊᴏɪɴ "
                            f"{row.get('title') or 'ᴄʜᴀɴɴᴇʟ'}"
                        ),
                        url=row["invite_link"],
                    )
                ]
            )

    buttons.append(
        [
            InlineKeyboardButton(
                "✅ ᴄʜᴇᴄᴋ ᴊᴏɪɴ",
                callback_data="check_fsub",
            )
        ]
    )

    return InlineKeyboardMarkup(
        buttons
    )


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
        "https://t.me/share/url?"
        f"url={quote(url, safe='')}"
    )


def admin_ok(uid):
    return (
        uid == OWNER_ID
        or db.is_admin(uid)
    )


# =========================================================
# URL PREVIEW CONTROL
# =========================================================

async def disable_copied_message_preview(
    bot,
    message,
):
    """
    Disable Telegram URL preview for an already sent text message.

    NOTE:
    Telegram does not reliably allow link_preview_options on
    media captions through edit_message_caption().
    """

    if not message:
        return message

    try:
        if message.text:
            return await bot.edit_message_text(
                chat_id=message.chat_id,
                message_id=message.message_id,
                text=message.text,
                entities=message.entities,
                link_preview_options=LinkPreviewOptions(
                    is_disabled=True
                ),
                reply_markup=message.reply_markup,
            )

    except Exception:
        log.exception(
            "Could not disable URL preview"
        )

    return message

    try:
        # -------------------------------------------------
        # TEXT MESSAGE
        # -------------------------------------------------
        if message.text:
            edited = await bot.edit_message_text(
                chat_id=message.chat_id,
                message_id=message.message_id,
                text=message.text,
                entities=message.entities,
                link_preview_options=LinkPreviewOptions(
                    is_disabled=True
                ),
                reply_markup=message.reply_markup,
            )

            return edited

        # -------------------------------------------------
        # MEDIA MESSAGE WITH CAPTION
        # -------------------------------------------------
        if message.caption:
            edited = await bot.edit_message_caption(
                chat_id=message.chat_id,
                message_id=message.message_id,
                caption=message.caption,
                caption_entities=message.caption_entities,
                link_preview_options=LinkPreviewOptions(
                    is_disabled=True
                ),
                reply_markup=message.reply_markup,
            )

            return edited

    except Exception:
        log.exception(
            "Could not disable URL preview "
            "for copied message"
        )

    return message


# =========================================================
# SETTINGS
# =========================================================

def settings_keyboard():
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "🖼️ ꜱᴇᴛ ɪᴍᴀɢᴇ",
                    callback_data="set_image",
                ),
                InlineKeyboardButton(
                    "🗑️ ᴀᴜᴛᴏ ᴅᴇʟᴇᴛᴇ",
                    callback_data="auto_delete",
                ),
            ],
            [
                InlineKeyboardButton(
                    "👮 ᴀᴅᴍɪɴꜱ",
                    callback_data="admins",
                ),
                InlineKeyboardButton(
                    "📢 ꜰꜱᴜʙ",
                    callback_data="fsub",
                ),
            ],
            [
                InlineKeyboardButton(
                    "✖️ ᴄʟᴏꜱᴇ",
                    callback_data="settings_close",
                )
            ],
        ]
    )


def settings_text():
    return (
        "<b>⚙️ ꜱᴇᴛᴛɪɴɢꜱ</b>\n\n"
        "ᴄʜᴏᴏꜱᴇ ᴀɴ ᴏᴘᴛɪᴏɴ."
    )


async def settings(
    update,
    context,
):
    uid = update.effective_user.id

    if not admin_ok(uid):
        return await update.message.reply_text(
            "❌ ᴜɴᴀᴜᴛʜᴏʀɪᴢᴇᴅ"
        )

    return await update.message.reply_text(
        settings_text(),
        parse_mode="HTML",
        reply_markup=settings_keyboard(),
    )


# =========================================================
# GENLINK
# =========================================================

async def genlink(
    update,
    context,
):
    if not admin_ok(
        update.effective_user.id
    ):
        return await update.message.reply_text(
            "❌ ʏᴏᴜ ᴀʀᴇ ɴᴏᴛ ᴀᴜᴛʜᴏʀɪᴢᴇᴅ."
        )

    replied = (
        update.message.reply_to_message
    )

    if not replied:
        return await update.message.reply_text(
            "ʀᴇᴘʟʏ ᴛᴏ ᴀɴʏ ᴍᴇꜱꜱᴀɢᴇ "
            "ᴀɴᴅ ᴜꜱᴇ /ɢᴇɴʟɪɴᴋ."
        )

    try:
        # =================================================
        # PLAIN TEXT / URL MESSAGE
        # =================================================
        #
        # IMPORTANT:
        # Do NOT use copy_message() here.
        #
        # copy_message() can create the URL preview before
        # we get a chance to disable it.
        #
        # send_message() with LinkPreviewOptions prevents
        # the preview from being created in the first place.
        # =================================================

        if replied.text:
            copied = await context.bot.send_message(
                chat_id=DB_CHANNEL_ID,
                text=replied.text,
                entities=replied.entities,
                link_preview_options=LinkPreviewOptions(
                    is_disabled=True
                ),
            )

        # =================================================
        # ALL OTHER MESSAGE TYPES
        # =================================================
        else:
            copied = await context.bot.copy_message(
                chat_id=DB_CHANNEL_ID,
                from_chat_id=replied.chat_id,
                message_id=replied.message_id,
            )

            copied = await disable_copied_message_preview(
                context.bot,
                copied,
            )

        # =================================================
        # SAVE DB RECORD
        # =================================================

        db.add_file(
            DB_CHANNEL_ID,
            copied.message_id,
            getattr(
                replied,
                "caption",
                None,
            )
            or getattr(
                replied,
                "text",
                None,
            )
            or "",
        )

        # =================================================
        # CREATE MAIN LINK
        # =================================================

        main = db.create_main_link(
            (
                f"message:"
                f"{DB_CHANNEL_ID}:"
                f"{copied.message_id}"
            )
        )

        url = main_link_url(
            main
        )

        markup = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "↗ ꜱʜᴀʀᴇ ᴜʀʟ",
                        url=share_url(url),
                    )
                ]
            ]
        )

        # =================================================
        # ADD SHARE BUTTON TO DB MESSAGE
        # =================================================

        try:
            await context.bot.edit_message_reply_markup(
                chat_id=DB_CHANNEL_ID,
                message_id=copied.message_id,
                reply_markup=markup,
            )

        except Exception:
            log.exception(
                "Could not add share button to genlink message"
            )

        # =================================================
        # SEND GENERATED LINK TO ADMIN
        # =================================================

        await update.message.reply_text(
            (
                "✅ <b>ɢᴇɴʟɪɴᴋ ɢᴇɴᴇʀᴀᴛᴇᴅ</b>\n\n"
                f"{url}"
            ),
            parse_mode="HTML",
            disable_web_page_preview=True,
            reply_markup=markup,
        )

    except Exception:
        log.exception(
            "Genlink failed"
        )

        await update.message.reply_text(
            "❌ ɢᴇɴʟɪɴᴋ ɢᴇɴᴇʀᴀᴛᴇ ɴᴀʜɪ ʜᴜᴀ."
        )


# =========================================================
# BATCH
# =========================================================

def parse_message_link(link):
    match = re.fullmatch(
        r"https?://t\.me/c/(\d+)/(\d+)",
        link.strip(),
    )

    if match:
        return (
            int(
                "-100"
                + match.group(1)
            ),
            int(
                match.group(2)
            ),
        )

    match = re.fullmatch(
        r"https?://t\.me/([^/]+)/(\d+)",
        link.strip(),
    )

    if match:
        return (
            match.group(1),
            int(
                match.group(2)
            ),
        )

    return None


async def batch(
    update,
    context,
):
    if not admin_ok(
        update.effective_user.id
    ):
        return await update.message.reply_text(
            "❌ ʏᴏᴜ ᴀʀᴇ ɴᴏᴛ ᴀᴜᴛʜᴏʀɪᴢᴇᴅ."
        )

    if len(context.args) != 2:
        return await update.message.reply_text(
            "ᴜꜱᴀɢᴇ:\n"
            "/ʙᴀᴛᴄʜ <ꜰɪʀꜱᴛ ᴅʙ ʟɪɴᴋ> "
            "<ʟᴀꜱᴛ ᴅʙ ʟɪɴᴋ>"
        )

    first = parse_message_link(
        context.args[0]
    )

    last = parse_message_link(
        context.args[1]
    )

    if not first or not last:
        return await update.message.reply_text(
            "❌ ɪɴᴠᴀʟɪᴅ ᴛᴇʟᴇɢʀᴀᴍ ᴘᴏꜱᴛ ʟɪɴᴋ."
        )

    try:
        fc = (
            (
                await context.bot.get_chat(
                    first[0]
                )
            ).id
            if isinstance(
                first[0],
                str,
            )
            else first[0]
        )

        lc = (
            (
                await context.bot.get_chat(
                    last[0]
                )
            ).id
            if isinstance(
                last[0],
                str,
            )
            else last[0]
        )

    except Exception:
        return await update.message.reply_text(
            "❌ ᴄᴏᴜʟᴅ ɴᴏᴛ ʀᴇꜱᴏʟᴠᴇ "
            "ᴛʜᴇ ᴅʙ ᴄʜᴀɴɴᴇʟ."
        )

    if (
        fc != DB_CHANNEL_ID
        or lc != DB_CHANNEL_ID
    ):
        return await update.message.reply_text(
            "❌ ʙᴏᴛʜ ʟɪɴᴋꜱ ᴍᴜꜱᴛ ʙᴇ "
            "ꜰʀᴏᴍ ᴛʜᴇ ᴅʙ ᴄʜᴀɴɴᴇʟ."
        )

    lo, hi = sorted(
        (
            first[1],
            last[1],
        )
    )

    rows = db.list_files_between(
        DB_CHANNEL_ID,
        lo,
        hi,
    )

    if not rows:
        return await update.message.reply_text(
            "❌ ɴᴏ ᴅʙ ᴘᴏꜱᴛꜱ ꜰᴏᴜɴᴅ "
            "ɪɴ ᴛʜɪꜱ ʀᴀɴɢᴇ."
        )

    bid = db.create_batch(
        [
            r["file_id"]
            for r in rows
        ]
    )

    main = db.create_main_link(
        f"batch:{bid}"
    )

    url = main_link_url(
        main
    )

    markup = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "↗ ꜱʜᴀʀᴇ ᴜʀʟ",
                    url=share_url(url),
                )
            ]
        ]
    )

    await context.bot.send_message(
        DB_CHANNEL_ID,
        "📦 <b>ʙᴀᴛᴄʜ ꜱʜᴀʀᴇ ᴜʀʟ</b>",
        parse_mode="HTML",
        reply_markup=markup,
    )

    await update.message.reply_text(
        (
            "✅ <b>ʙᴀᴛᴄʜ ʟɪɴᴋ ɢᴇɴᴇʀᴀᴛᴇᴅ</b>\n\n"
            f"ɪᴛᴇᴍꜱ: <b>{len(rows)}</b>\n\n"
            f"{url}"
        ),
        parse_mode="HTML",
        disable_web_page_preview=True,
        reply_markup=markup,
    )


# =========================================================
# DOWNLOAD PAGE
# =========================================================

async def send_download_page(
    message,
    short_url,
):
    caption = (
        "<i>📊 ʜᴇʏ ʙʀᴏ/ꜱɪꜱ,</i>\n\n"
        "➜ ʏᴏᴜʀ ʟɪɴᴋ ɪꜱ ʀᴇᴀᴅʏ, "
        "ᴋɪɴᴅʟʏ ᴄʟɪᴄᴋ ᴏɴ\n"
        "ᴅᴏᴡɴʟᴏᴀᴅ ʙᴜᴛᴛᴏɴ! 👇\n\n"
        "ᴛᴏ ʙᴜʏ ᴘʀᴇᴍɪᴜᴍ, "
        "ᴄᴏɴᴛᴀᴄᴛ: "
        '<a href="https://t.me/Its_Lozo">'
        "@ɪᴛꜱ_ʟᴏᴢᴏ"
        "</a>"
    )

    kb = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "• ᴄʟɪᴄᴋ ʜᴇʀᴇ ᴛᴏ ᴅᴏᴡɴʟᴏᴀᴅ •",
                    url=short_url,
                )
            ],
            [
                InlineKeyboardButton(
                    "ᴘʀᴇᴍɪᴜᴍ",
                    url="https://t.me/PremiumHub094",
                ),
                InlineKeyboardButton(
                    "ᴛᴜᴛᴏʀɪᴀʟ",
                    url="https://t.me/Tutorial_Hub_94/4",
                ),
            ],
        ]
    )

    image = start_image()

    if image:
        try:
            return await message.reply_photo(
                photo=image,
                caption=caption,
                parse_mode="HTML",
                reply_markup=kb,
            )
        except Exception:
            pass

    await message.reply_text(
        caption,
        parse_mode="HTML",
        reply_markup=kb,
    )


# =========================================================
# DELIVERY
# =========================================================

async def deliver_target(
    update,
    target,
):
    ids = []

    bot = update.get_bot()

    chat_id = (
        update.effective_chat.id
    )

    # =====================================================
    # SINGLE MESSAGE
    # =====================================================

    if target.startswith(
        "message:"
    ):
        _, cid, mid = target.split(
            ":",
            2,
        )

        # -------------------------------------------------
        # First copy the DB message.
        #
        # For a normal text/URL message, Telegram's copied
        # message can be edited to disable preview.
        # -------------------------------------------------

        message = await bot.copy_message(
            chat_id=chat_id,
            from_chat_id=int(cid),
            message_id=int(mid),
        )

        message = await disable_copied_message_preview(
            bot,
            message,
        )

        ids.append(
            message.message_id
        )

    # =====================================================
    # BATCH
    # =====================================================

    elif target.startswith(
        "batch:"
    ):
        bid = target.split(
            ":",
            1,
        )[1]

        rows = db.get_batch_items(
            bid
        )

        if not rows:
            return []

        for row in rows:
            try:
                message = await bot.copy_message(
                    chat_id=chat_id,
                    from_chat_id=int(
                        row["channel_id"]
                    ),
                    message_id=int(
                        row["message_id"]
                    ),
                )

                message = await disable_copied_message_preview(
                    bot,
                    message,
                )

                ids.append(
                    message.message_id
                )

            except Exception:
                log.exception(
                    "Batch delivery failed"
                )

    return ids


async def delete_delivered(context):
    data = context.job.data

    chat_id = data["chat_id"]
    message_ids = data["message_ids"]

    for mid in message_ids:
        try:
            await context.bot.delete_message(
                chat_id=chat_id,
                message_id=mid,
            )
        except Exception as e:
            log.exception(
                "Auto-delete failed: chat_id=%s message_id=%s error=%s",
                chat_id,
                mid,
                e,
            )


def auto_delete_minutes():
    try:
        return max(
            0,
            int(
                db.get_setting(
                    "auto_delete_minutes",
                    "10",
                )
            ),
        )
    except Exception:
        return 10


async def deliver_and_notify(
    update,
    context,
    target,
):
    ids = await deliver_target(
        update,
        target,
    )

    if not ids:
        return

    mins = auto_delete_minutes()

    if mins <= 0:
        return

    chat_id = update.effective_chat.id

    msg = await context.bot.send_message(
        chat_id=chat_id,
        text=(
            f"ᴛʜɪs ꜰɪʟᴇ ɪs ᴅᴇʟᴇᴛɪɴɢ "
            f"ᴀᴜᴛᴏᴍᴀᴛɪᴄᴀʟʟʏ ɪɴ "
            f"{mins} ᴍɪɴᴜᴛᴇꜱ.\n\n"
            "ꜰᴏʀᴡᴀʀᴅ ɪᴛ ᴛᴏ ʏᴏᴜʀ "
            "ꜱᴀᴠᴇᴅ ᴍᴇꜱꜱᴀɢᴇꜱ..!"
        ),
    )

    ids.append(msg.message_id)

    try:
        context.job_queue.run_once(
            delete_delivered,
            when=mins * 60,
            data={
                "chat_id": chat_id,
                "message_ids": ids,
            },
            name=f"auto_delete_{chat_id}_{msg.message_id}",
        )

        log.info(
            "Auto-delete scheduled: chat_id=%s messages=%s after=%s minutes",
            chat_id,
            ids,
            mins,
        )

    except Exception:
        log.exception(
            "Could not schedule auto-delete"
        )


# =========================================================
# BYPASS MESSAGE
# =========================================================

def bypass_message():
    return (
        "<b>🚫 ʙʏᴘᴀss ᴅᴇᴛᴇᴄᴛᴇᴅ</b>\n\n"
        "ʏᴏᴜ ʜᴀᴠᴇ ʙᴇᴇɴ ᴅᴇᴛᴇᴄᴛᴇᴅ ᴜsɪɴɢ ᴀ ʙʏᴘᴀss "
        "ᴛᴏ sᴋɪᴘ ᴛʜᴇ ʀᴇǫᴜɪʀᴇᴅ ᴠᴇʀɪғɪᴄᴀᴛɪᴏɴ ᴛɪᴍᴇ.\n\n"
        "ᴀs ᴀ ʀᴇsᴜʟᴛ, ʏᴏᴜʀ ᴀᴄᴄᴇss ᴛᴏ ᴛʜɪs ʙᴏᴛ "
        "ʜᴀs ʙᴇᴇɴ <b>ᴘᴇʀᴍᴀɴᴇɴᴛʟʏ ʀᴇsᴛʀɪᴄᴛᴇᴅ.</b>\n\n"
        f'ɪғ ʏᴏᴜ ʙᴇʟɪᴇᴠᴇ ᴛʜɪs ᴡᴀs ᴀ ᴍɪsᴛᴀᴋᴇ, '
        f'ᴄᴏɴᴛᴀᴄᴛ '
        f'<a href="tg://user?id={OWNER_ID}">'
        f"ʟᴏᴢᴏ_⁹⁴"
        f"</a>."
    )


# =========================================================
# VERIFY
# =========================================================

async def verify(
    update,
    context,
    token,
):
    uid = update.effective_user.id

    # =====================================================
    # OWNER EXEMPTION
    # =====================================================

    if uid == OWNER_ID:
        row = db.get_token(
            token
        )

        if row:
            if int(
                row["user_id"]
            ) == uid:
                target = db.consume_token(
                    token,
                    uid,
                )
            else:
                target = row["target"]

            if target:
                return await deliver_and_notify(
                    update,
                    context,
                    target,
                )

        return await update.message.reply_text(
            "❌ ᴛʜɪꜱ ʟɪɴᴋ ɪs ɴᴏᴛ ᴠᴀʟɪᴅ."
        )

    # =====================================================
    # FORCE SUB
    # =====================================================

    missing = await is_fsub_member(
        context.bot,
        uid,
    )

    if missing:
        return await update.message.reply_text(
            "⚡ <b>ᴊᴏɪɴ ʀᴇǫᴜɪʀᴇᴅ</b>\n\n"
            "ᴊᴏɪɴ ᴀʟʟ ʀᴇǫᴜɪʀᴇᴅ ᴄʜᴀɴɴᴇʟꜱ "
            "ᴛʜᴇɴ ᴛᴀᴘ ᴄʜᴇᴄᴋ ᴊᴏɪɴ.",
            parse_mode="HTML",
            reply_markup=fsub_keyboard(
                missing
            ),
        )

    # =====================================================
    # TOKEN VALIDATION
    # =====================================================

    row = db.get_token(
        token
    )

    if (
        not row
        or int(
            row["user_id"]
        ) != uid
    ):
        return await update.message.reply_text(
            "❌ ᴛʜɪꜱ ꜱʜᴏʀᴛᴇɴᴇʀ ʟɪɴᴋ "
            "ɪs ɴᴏᴛ ᴍᴀᴅᴇ ꜰᴏʀ ʏᴏᴜ."
        )

    # =====================================================
    # ALREADY USED
    # =====================================================

    if row.get("used"):
        return await update.message.reply_text(
            "❌ ᴛʜɪꜱ ꜱʜᴏʀᴛᴇɴᴇʀ ʟɪɴᴋ "
            "ɪs ᴀʟʀᴇᴀᴅʏ ᴜsᴇᴅ."
        )

    # =====================================================
    # EXPIRED CHECK
    # =====================================================

    try:
        if db._dt(
            row["expires_at"]
        ) <= datetime.now(
            timezone.utc
        ):
            return await update.message.reply_text(
                "❌ ᴛʜɪꜱ ꜱʜᴏʀᴛᴇɴᴇʀ ʟɪɴᴋ "
                "ɪs ᴇxᴘɪʀᴇᴅ."
            )

    except Exception:
        return await update.message.reply_text(
            "❌ ᴛʜɪꜱ ꜱʜᴏʀᴛᴇɴᴇʀ ʟɪɴᴋ "
            "ɪs ɴᴏᴛ ᴠᴀʟɪᴅ."
        )

    # =====================================================
    # ANTI-BYPASS CHECK
    # =====================================================

    age = db.get_token_age_seconds(
        token
    )

    if (
        age is not None
        and age < BYPASS_MIN_SECONDS
    ):
        log.warning(
            "BYPASS DETECTED | user=%s | token=%s | age=%.2fs",
            uid,
            token,
            age,
        )

        db.ban_user(
            uid,
            reason="Bypass Detected",
        )

        try:
            db.consume_token(
                token,
                uid,
            )
        except Exception:
            pass

        return await update.message.reply_text(
            bypass_message(),
            parse_mode="HTML",
            link_preview_options=LinkPreviewOptions(
                is_disabled=True
            ),
        )

    # =====================================================
    # NORMAL DELIVERY
    # =====================================================

    target = db.consume_token(
        token,
        uid,
    )

    if not target:
        return await update.message.reply_text(
            "❌ ᴛʜɪꜱ ꜱʜᴏʀᴛᴇɴᴇʀ ʟɪɴᴋ "
            "ɪs ᴇxᴘɪʀᴇᴅ ᴏʀ ᴀʟʀᴇᴀᴅʏ ᴜꜱᴇᴅ."
        )

    await deliver_and_notify(
        update,
        context,
        target,
    )


# =========================================================
# OPEN MAIN LINK
# =========================================================

async def open_main(
    update,
    context,
    token,
):
    uid = update.effective_user.id

    row = db.get_main_link(
        token
    )

    if not row:
        return await update.message.reply_text(
            "❌ ᴛʜɪꜱ ʟɪɴᴋ ɪs ɴᴏᴛ ᴠᴀʟɪᴅ."
        )

    missing = await is_fsub_member(
        context.bot,
        uid,
    )

    if missing:
        return await update.message.reply_text(
            "⚡ <b>ᴊᴏɪɴ ʀᴇǫᴜɪʀᴇᴅ</b>\n\n"
            "ᴊᴏɪɴ ᴀʟʟ ʀᴇǫᴜɪʀᴇᴅ ᴄʜᴀɴɴᴇʟꜱ "
            "ᴛʜᴇɴ ᴛᴀᴘ ᴄʜᴇᴄᴋ ᴊᴏɪɴ.",
            parse_mode="HTML",
            reply_markup=fsub_keyboard(
                missing
            ),
        )

    # =====================================================
    # OWNER / PREMIUM EXEMPTION
    # =====================================================

    if (
        uid == OWNER_ID
        or db.is_premium(uid)
    ):
        return await deliver_and_notify(
            update,
            context,
            row["target"],
        )

    # =====================================================
    # CREATE TOKEN
    # =====================================================

    tok = db.create_token(
        uid,
        row["target"],
        2,
    )

    short_url = shortener.create_from_token(
        tok,
        BOT_USERNAME,
    )

    if not short_url:
        return await update.message.reply_text(
            "⚠️ ꜱʜᴏʀᴛᴇɴᴇʀ ɪꜱ ɴᴏᴛ "
            "ᴄᴏɴꜰɪɢᴜʀᴇᴅ ᴄᴏʀʀᴇᴄᴛʟʏ."
        )

    await send_download_page(
        update.message,
        short_url,
    )


# =========================================================
# START COMMAND
# =========================================================

async def start(
    update,
    context,
):
    u = update.effective_user

    db.add_user(
        u.id,
        u.username or "",
        u.first_name or "",
    )

    if db.is_banned(
        u.id
    ):
        return await update.message.reply_text(
            "🚫 ʏᴏᴜ ᴀʀᴇ ʙᴀɴɴᴇᴅ."
        )

    if context.args:
        arg = context.args[0]

        if arg.startswith(
            "verify_"
        ):
            return await verify(
                update,
                context,
                arg[7:],
            )

        if arg.startswith(
            "link_"
        ):
            return await open_main(
                update,
                context,
                arg[5:],
            )

    await render_start(
        update.message
    )


# =========================================================
# TIMEZONE SYSTEM
# =========================================================

TIMEZONE_OPTIONS = {
    "Asia/Kolkata": "🇮🇳 India",
    "Asia/Dubai": "🇦🇪 UAE",
    "Asia/Riyadh": "🇸🇦 Saudi Arabia",
    "Asia/Karachi": "🇵🇰 Pakistan",
    "Asia/Dhaka": "🇧🇩 Bangladesh",
    "Asia/Kathmandu": "🇳🇵 Nepal",
    "Asia/Tokyo": "🇯🇵 Japan",
    "Asia/Singapore": "🇸🇬 Singapore",
    "Europe/London": "🇬🇧 UK",
    "Europe/Berlin": "🇩🇪 Germany",
    "America/New_York": "🇺🇸 USA - Eastern",
    "America/Chicago": "🇺🇸 USA - Central",
    "America/Denver": "🇺🇸 USA - Mountain",
    "America/Los_Angeles": "🇺🇸 USA - Pacific",
}


def timezone_keyboard():
    buttons = []

    for (
        tz_name,
        label,
    ) in TIMEZONE_OPTIONS.items():
        buttons.append(
            [
                InlineKeyboardButton(
                    label,
                    callback_data=(
                        f"tz:{tz_name}"
                    ),
                )
            ]
        )

    buttons.append(
        [
            InlineKeyboardButton(
                "❌ ᴄʟᴏsᴇ",
                callback_data="tz_close",
            )
        ]
    )

    return InlineKeyboardMarkup(
        buttons
    )


def timezone_display_name(
    tz_name
):
    return TIMEZONE_OPTIONS.get(
        tz_name,
        tz_name,
    )


async def timezone_command(
    update,
    context,
):
    uid = update.effective_user.id

    db.add_user(
        uid,
        update.effective_user.username
        or "",
        update.effective_user.first_name
        or "",
    )

    current = db.get_user_timezone(
        uid
    )

    try:
        now_local = datetime.now(
            timezone.utc
        ).astimezone(
            ZoneInfo(current)
        )

        current_time = now_local.strftime(
            "%d-%m-%Y %I:%M:%S %p"
        )

    except Exception:
        current = "Asia/Kolkata"

        current_time = datetime.now(
            timezone.utc
        ).astimezone(
            IST
        ).strftime(
            "%d-%m-%Y %I:%M:%S %p"
        )

    text = (
        "🌍 <b>ᴄʜᴏᴏꜱᴇ ʏᴏᴜʀ ᴛɪᴍᴇᴢᴏɴᴇ</b>\n\n"
        f"🕐 ᴄᴜʀʀᴇɴᴛ: "
        f"<b>{timezone_display_name(current)}</b>\n"
        f"📅 ʟᴏᴄᴀʟ ᴛɪᴍᴇ: "
        f"<code>{current_time}</code>\n\n"
        "ᴛʜɪꜱ ᴡɪʟʟ ʙᴇ ᴜꜱᴇᴅ ꜰᴏʀ ʏᴏᴜʀ "
        "ᴘʀᴇᴍɪᴜᴍ ꜱᴜʙꜱᴄʀɪᴘᴛɪᴏɴ ᴛɪᴍᴇ."
    )

    await update.message.reply_text(
        text,
        parse_mode="HTML",
        reply_markup=timezone_keyboard(),
    )


# =========================================================
# CALLBACK HANDLER
# =========================================================

async def callback(
    update,
    context,
):
    q = update.callback_query

    uid = q.from_user.id

    if uid:
           if db.is_banned(uid):
                await q.answer(
                    "🚫 ʏᴏᴜ ᴀʀᴇ ʙᴀɴɴᴇᴅ.",
                    show_alert=True,
                )
                return

        except Exception:
            log.exception(
                "Callback ban check failed"
            )

    # =====================================================
    # TIMEZONE CALLBACKS
    # =====================================================

    if q.data.startswith(
        "tz:"
    ):
        tz_name = q.data[3:]

        try:
            ZoneInfo(tz_name)

        except Exception:
            return await q.answer(
                "❌ ɪɴᴠᴀʟɪᴅ ᴛɪᴍᴇᴢᴏɴᴇ.",
                show_alert=True,
            )

        db.set_user_timezone(
            uid,
            tz_name,
        )

        try:
            local_now = datetime.now(
                timezone.utc
            ).astimezone(
                ZoneInfo(tz_name)
            )

            time_text = local_now.strftime(
                "%d-%m-%Y %I:%M:%S %p"
            )

        except Exception:
            time_text = "—"

        await q.answer(
            "✅ ᴛɪᴍᴇᴢᴏɴᴇ ᴜᴘᴅᴀᴛᴇᴅ",
            show_alert=False,
        )

        try:
            await q.message.edit_text(
                "✅ <b>ᴛɪᴍᴇᴢᴏɴᴇ ᴜᴘᴅᴀᴛᴇᴅ</b>\n\n"
                f"🌍 ᴛɪᴍᴇᴢᴏɴᴇ: "
                f"<b>{timezone_display_name(tz_name)}</b>\n"
                f"🕐 ʟᴏᴄᴀʟ ᴛɪᴍᴇ: "
                f"<code>{time_text}</code>\n\n"
                "ʏᴏᴜʀ /myplan ᴛɪᴍᴇꜱ ᴡɪʟʟ ɴᴏᴡ "
                "ʙᴇ ꜱʜᴏᴡɴ ɪɴ ᴛʜɪꜱ ᴛɪᴍᴇᴢᴏɴᴇ.",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(
                    [
                        [
                            InlineKeyboardButton(
                                "🌍 ᴄʜᴀɴɢᴇ ᴛɪᴍᴇᴢᴏɴᴇ",
                                callback_data="timezone_menu",
                            )
                        ],
                        [
                            InlineKeyboardButton(
                                "✖️ ᴄʟᴏꜱᴇ",
                                callback_data="tz_close",
                            )
                        ],
                    ]
                ),
            )

        except Exception:
            pass

        return

    if q.data == "timezone_menu":
        await q.answer()

        current = db.get_user_timezone(
            uid
        )

        return await q.message.edit_text(
            "🌍 <b>ᴄʜᴏᴏꜱᴇ ʏᴏᴜʀ ᴛɪᴍᴇᴢᴏɴᴇ</b>\n\n"
            f"ᴄᴜʀʀᴇɴᴛ: "
            f"<b>{timezone_display_name(current)}</b>",
            parse_mode="HTML",
            reply_markup=timezone_keyboard(),
        )

    if q.data == "tz_close":
        await q.answer()

        try:
            await q.message.delete()

        except Exception:
            pass

        return

    # =====================================================
    # NORMAL ADMIN CALLBACKS
    # =====================================================

    if not admin_ok(uid):
        return await q.answer(
            "❌ ᴜɴᴀᴜᴛʜᴏʀɪᴢᴇᴅ",
            show_alert=True,
        )

    if (
        q.data in (
            "add_admin",
            "remove_admin",
        )
        and uid != OWNER_ID
    ):
        return await q.answer(
            "🚫 ᴏɴʟʏ ᴏᴡɴᴇʀ ᴄᴀɴ "
            "ᴀᴅᴅ/ʀᴇᴍᴏᴠᴇ ᴀᴅᴍɪɴꜱ",
            show_alert=True,
        )

    if (
        q.data in (
            "add_fsub",
            "remove_fsub",
        )
        and uid != OWNER_ID
    ):
        return await q.answer(
            "🚫 ᴏɴʟʏ ᴏᴡɴᴇʀ ᴄᴀɴ "
            "ᴀᴅᴅ/ʀᴇᴍᴏᴠᴇ ꜰꜱᴜʙ",
            show_alert=True,
        )

    await q.answer()

    # =====================================================
    # CLOSE
    # =====================================================

    if q.data in (
        "close",
        "settings_close",
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
            uid,
        )

        if missing:
            return await q.answer(
                "❌ ᴊᴏɪɴ ᴀʟʟ ᴄʜᴀɴɴᴇʟꜱ ꜰɪʀꜱᴛ.",
                show_alert=True,
            )

        try:
            await q.message.delete()

        except Exception:
            pass

        return await render_start(
            q.message
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
            "ꜱᴇɴᴅ ᴛʜᴇ ɴᴇᴡ ᴛɪᴍᴇ "
            "ɪɴ ᴍɪɴᴜᴛᴇꜱ.\n"
            "ꜱᴇɴᴅ <code>0</code> ᴛᴏ ᴅɪꜱᴀʙʟᴇ.",
            parse_mode="HTML",
        )

    # =====================================================
    # ADMINS LIST
    # =====================================================

    if q.data == "admins":
        lines = [
            "<b>👮 ᴀᴅᴍɪɴꜱ</b>",
            "",
            (
                f'• <a href="tg://user?id={OWNER_ID}">'
                f"ᴏᴡɴᴇʀ</a> — "
                f"<code>{OWNER_ID}</code>"
            ),
        ]

        for a in db.list_admins():
            name = (
                a["first_name"]
                or (
                    "@"
                    + a["username"]
                    if a["username"]
                    else "ᴀᴅᴍɪɴ"
                )
            )

            name = escape(
                name
            )

            lines.append(
                (
                    f'• <a href="tg://user?id='
                    f'{a["user_id"]}">'
                    f"{name}</a> — "
                    f"<code>{a['user_id']}</code>"
                )
            )

        return await q.message.edit_text(
            "\n".join(lines),
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "➕ ᴀᴅᴅ ᴀᴅᴍɪɴ",
                            callback_data="add_admin",
                        ),
                        InlineKeyboardButton(
                            "➖ ʀᴇᴍᴏᴠᴇ",
                            callback_data="remove_admin",
                        ),
                    ],
                    [
                        InlineKeyboardButton(
                            "↩️ ʙᴀᴄᴋ",
                            callback_data="settings_back",
                        )
                    ],
                ]
            ),
        )

    # =====================================================
    # ADD ADMIN
    # =====================================================

    if q.data == "add_admin":
        if uid != OWNER_ID:
            return await q.answer(
                "❌ ᴏɴʟʏ ᴏᴡɴᴇʀ ᴄᴀɴ "
                "ᴀᴅᴅ ᴀᴅᴍɪɴꜱ.",
                show_alert=True,
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
                "❌ ᴏɴʟʏ ᴏᴡɴᴇʀ ᴄᴀɴ "
                "ʀᴇᴍᴏᴠᴇ ᴀᴅᴍɪɴꜱ.",
                show_alert=True,
            )

        _pending_admin.add(
            -uid
        )

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
            "",
        ]

        for r in rows:
            invite = r.get(
                "invite_link",
                "",
            )

            title = r.get(
                "title",
                "ᴄʜᴀɴɴᴇʟ",
            )

            title = escape(
                title
            )

            if invite:
                lines.append(
                    (
                        f'• <a href="{escape(invite)}">'
                        f"{title}</a> — "
                        f'<code>{r["channel_id"]}</code>'
                    )
                )

            else:
                lines.append(
                    (
                        f"• {title} — "
                        f'<code>{r["channel_id"]}</code>'
                    )
                )

        kb = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "➕ ᴀᴅᴅ ꜰꜱᴜʙ",
                        callback_data="add_fsub",
                    )
                ],
                [
                    InlineKeyboardButton(
                        "➖ ʀᴇᴍᴏᴠᴇ ꜰꜱᴜʙ",
                        callback_data="remove_fsub",
                    )
                ],
                [
                    InlineKeyboardButton(
                        "↩️ ʙᴀᴄᴋ",
                        callback_data="settings_back",
                    )
                ],
            ]
        )

        return await q.message.edit_text(
            "\n".join(lines),
            parse_mode="HTML",
            reply_markup=kb,
        )

    # =====================================================
    # ADD F-SUB
    # =====================================================

    if q.data == "add_fsub":
        if uid != OWNER_ID:
            return await q.answer(
                "❌ ᴏɴʟʏ ᴏᴡɴᴇʀ ᴄᴀɴ "
                "ᴀᴅᴅ ꜰꜱᴜʙ.",
                show_alert=True,
            )

        _pending_fsub.add(uid)

        return await q.message.reply_text(
            "📢 ꜱᴇɴᴅ ᴛʜᴇ ᴄʜᴀɴɴᴇʟ ɪᴅ ᴏɴʟʏ.\n"
            "ᴛʜᴇ ʙᴏᴛ ᴡɪʟʟ ɢᴇᴛ "
            "ᴛʜᴇ ᴄʜᴀɴɴᴇʟ ɴᴀᴍᴇ ᴀɴᴅ "
            "ɪɴᴠɪᴛᴇ ʟɪɴᴋ ᴀᴜᴛᴏᴍᴀᴛɪᴄᴀʟʟʏ."
        )

    # =====================================================
    # REMOVE F-SUB
    # =====================================================

    if q.data == "remove_fsub":
        if uid != OWNER_ID:
            return await q.answer(
                "❌ ᴏɴʟʏ ᴏᴡɴᴇʀ ᴄᴀɴ "
                "ʀᴇᴍᴏᴠᴇ ꜰꜱᴜʙ.",
                show_alert=True,
            )

        _pending_fsub.add(
            uid * 1000000000 + 1
        )

        return await q.message.reply_text(
            "➖ ꜱᴇɴᴅ ᴛʜᴇ ꜰꜱᴜʙ "
            "ᴄʜᴀɴɴᴇʟ ɪᴅ ᴛᴏ ʀᴇᴍᴏᴠᴇ."
        )

    # =====================================================
    # SETTINGS BACK
    # =====================================================

    if q.data == "settings_back":
        return await q.message.edit_text(
            settings_text(),
            parse_mode="HTML",
            reply_markup=settings_keyboard(),
        )


# =========================================================
# SETTINGS INPUT
# =========================================================

async def settings_input(
    update,
    context,
):
    if not update.effective_user:
        return

    if not update.message:
        return

    uid = update.effective_user.id

    if not admin_ok(uid):
        return

    # =====================================================
    # START IMAGE
    # =====================================================

    if (
        uid in _pending_image
        and update.message.photo
    ):
        db.set_setting(
            "start_image",
            update.message.photo[-1].file_id,
        )

        _pending_image.discard(
            uid
        )

        return await update.message.reply_text(
            "✅ ꜱᴛᴀʀᴛ ɪᴍᴀɢᴇ "
            "ᴜᴘᴅᴀᴛᴇᴅ ꜱᴜᴄᴄᴇꜱꜱꜰᴜʟʟʏ."
        )

    # =====================================================
    # AUTO DELETE
    # =====================================================

    if uid in _pending_autodelete:
        try:
            minutes = max(
                0,
                int(
                    update.message.text.strip()
                ),
            )

            db.set_setting(
                "auto_delete_minutes",
                minutes,
            )

            _pending_autodelete.discard(
                uid
            )

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
            _pending_admin.discard(
                uid
            )

            _pending_admin.discard(
                -uid
            )

            return await update.message.reply_text(
                "❌ ᴏɴʟʏ ᴏᴡɴᴇʀ ᴄᴀɴ "
                "ᴀᴅᴅ ᴏʀ ʀᴇᴍᴏᴠᴇ ᴀᴅᴍɪɴꜱ."
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

        _pending_admin.discard(
            uid
        )

        _pending_admin.discard(
            -uid
        )

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
            _pending_fsub.discard(
                uid
            )

            return await update.message.reply_text(
                "❌ ᴏɴʟʏ ᴏᴡɴᴇʀ ᴄᴀɴ "
                "ᴀᴅᴅ ꜰꜱᴜʙ."
            )

        try:
            cid = int(
                update.message.text.strip()
            )

            chat = await context.bot.get_chat(
                cid
            )

            invite = (
                await context.bot.create_chat_invite_link(
                    cid,
                    name="File Store FSub",
                )
            )

            db.add_fsub(
                cid,
                invite.invite_link,
                (
                    chat.title
                    or chat.username
                    or str(cid)
                ),
            )

            _pending_fsub.discard(
                uid
            )

            return await update.message.reply_text(
                (
                    f"✅ <b>ꜰꜱᴜʙ ᴀᴅᴅᴇᴅ</b>\n\n"
                    f'📢 <a href="{escape(invite.invite_link)}">'
                    f"{escape(chat.title or 'Channel')}"
                    f"</a>\n"
                    f"🆔 <code>{cid}</code>\n"
                    f'🔗 <a href="{escape(invite.invite_link)}">'
                    f"ɪɴᴠɪᴛᴇ ʟɪɴᴋ</a>"
                ),
                parse_mode="HTML",
            )

        except Exception as e:
            return await update.message.reply_text(
                (
                    "❌ ᴄᴏᴜʟᴅ ɴᴏᴛ ᴀᴅᴅ ᴛʜɪꜱ "
                    "ᴄʜᴀɴɴᴇʟ.\n"
                    "ᴍᴀᴋᴇ ꜱᴜʀᴇ ᴛʜᴇ ʙᴏᴛ ɪꜱ ᴀᴅᴍɪɴ.\n\n"
                    f"<code>{escape(str(e))}</code>"
                ),
                parse_mode="HTML",
            )

    # =====================================================
    # REMOVE F-SUB
    # =====================================================

    remkey = (
        uid * 1000000000
        + 1
    )

    if remkey in _pending_fsub:
        if uid != OWNER_ID:
            _pending_fsub.discard(
                remkey
            )

            return await update.message.reply_text(
                "❌ ᴏɴʟʏ ᴏᴡɴᴇʀ ᴄᴀɴ "
                "ʀᴇᴍᴏᴠᴇ ꜰꜱᴜʙ."
            )

        try:
            cid = (
                update.message.text.strip()
            )

            db.del_fsub(
                cid
            )

            _pending_fsub.discard(
                remkey
            )

            return await update.message.reply_text(
                "✅ ꜰꜱᴜʙ ᴄʜᴀɴɴᴇʟ "
                "ʀᴇᴍᴏᴠᴇᴅ."
            )

        except Exception:
            return await update.message.reply_text(
                "❌ ᴄᴏᴜʟᴅ ɴᴏᴛ ʀᴇᴍᴏᴠᴇ."
            )

async def expire_premium(context):
    data = context.job.data

    uid = int(data["user_id"])

    try:
        premium = db.get_premium(uid)

        if not premium:
            return

        expiry = db._dt(premium["expires_at"])
        now = datetime.now(timezone.utc)

        # Agar expiry abhi nahi hui hai, safety ke liye dobara schedule.
        if expiry > now:
            delay = max(
                1,
                (expiry - now).total_seconds(),
            )

            context.job_queue.run_once(
                expire_premium,
                when=delay,
                data={
                    "user_id": uid,
                },
                name=f"premium_expiry_{uid}",
            )
            return

        db.remove_premium(uid)

        log.info(
            "Premium expired and removed for user %s",
            uid,
        )

    except Exception:
        log.exception(
            "Premium expiry failed for user %s",
            uid,
        )

# =========================================================
# PREMIUM ADD
# =========================================================

async def addsubs(
    update,
    context,
):
    if not admin_ok(
        update.effective_user.id
    ):
        return await update.message.reply_text(
            "❌ ᴜɴᴀᴜᴛʜᴏʀɪᴢᴇᴅ."
        )

    if len(context.args) != 2:
        return await update.message.reply_text(
            "ᴜꜱᴀɢᴇ: /ᴀᴅᴅꜱᴜʙꜱ USER_ID DAYS"
        )

    try:
        uid = int(
            context.args[0]
        )

        days = int(
            context.args[1]
        )

        if days <= 0:
            raise ValueError

    except Exception:
        return await update.message.reply_text(
            "❌ ᴜꜱᴇ: /ᴀᴅᴅꜱᴜʙꜱ USER_ID DAYS"
        )

    try:
        try:
            u = await context.bot.get_chat(
                uid
            )

            db.add_user(
                uid,
                u.username or "",
                u.first_name or "",
            )

        except Exception:
            u = None

        start_time, expiry = (
            db.add_premium(
                uid,
                days,
            )
        )

        delay = max(
            1,
            (
                db._dt(expiry)
                - datetime.now(timezone.utc)
            ).total_seconds(),
        )

        context.job_queue.run_once(
            expire_premium,
            when=delay,
            data={
                "user_id": uid,
            },
            name=f"premium_expiry_{uid}",
        )

        name = (
            u.first_name
            if u
            else None
        ) or "ᴜsᴇʀ"

        await context.bot.send_message(
            uid,
            (
                "🎉 <b>Congratulations!</b>\n\n"
                "Your account has been upgraded to the "
                f"Premium Ad-Free Tier for the next "
                f"{days} Days.\n"
                "Enjoy high-speed bypass-free file downloads!"
            ),
            parse_mode="HTML",
        )

        await update.message.reply_text(
            (
                "<b>✅ Premium Tier Activated "
                "Successfully!</b>\n\n"
                f"👤 Name: {escape(name)}\n"
                f"🆔 User ID: {uid}\n"
                f"⏳ Duration Allocated: {days} Days"
            ),
            parse_mode="HTML",
        )

    except Exception as e:
        await update.message.reply_text(
            f"❌ {escape(str(e))}"
        )


# =========================================================
# PREMIUM REMOVE
# =========================================================

async def removesubs(
    update,
    context,
):
    if not admin_ok(
        update.effective_user.id
    ):
        return await update.message.reply_text(
            "❌ ᴜɴᴀᴜᴛʜᴏʀɪᴢᴇᴅ."
        )

    if len(context.args) != 1:
        return await update.message.reply_text(
            "ᴜꜱᴀɢᴇ: /ʀᴇᴍᴏᴠᴇꜱᴜʙꜱ USER_ID"
        )

    try:
        uid = int(
            context.args[0]
        )

    except Exception:
        return await update.message.reply_text(
            "❌ ᴇɴᴛᴇʀ ᴀ ᴠᴀʟɪᴅ ᴜꜱᴇʀ ɪᴅ."
        )

    old = db.remove_premium(
        uid
    )

    if old:
        try:
            await context.bot.send_message(
                uid,
                (
                    "🚨 <b>Notification:</b> "
                    "Your premium subscription package has "
                    "been manually revoked by the management team."
                ),
                parse_mode="HTML",
            )

        except Exception:
            pass

    await update.message.reply_text(
        (
            "✅ ᴘʀᴇᴍɪᴜᴍ ʀᴇᴍᴏᴠᴇᴅ.\n\n"
            f"ᴜꜱᴇʀ: <code>{uid}</code>"
        ),
        parse_mode="HTML",
    )


# =========================================================
# MY PLAN
# =========================================================

async def myplan(
    update,
    context,
):
    uid = update.effective_user.id

    db.add_user(
        uid,
        update.effective_user.username
        or "",
        update.effective_user.first_name
        or "",
    )

    plan = db.get_premium(
        uid
    )

    if not plan:
        return await update.message.reply_text(
            (
                "💎 <b>ᴍʏ ᴘʟᴀɴ</b>\n\n"
                "🔴 ꜱᴛᴀᴛᴜꜱ: ꜰʀᴇᴇ ᴘʟᴀɴ\n\n"
                "⚡ ꜱʜᴏʀᴛᴇɴᴇʀ ʙʏᴘᴀꜱꜱ: ᴅɪꜱᴀʙʟᴇᴅ\n\n"
                "💎 ɢᴇᴛ ᴘʀᴇᴍɪᴜᴍ ᴛᴏ ᴇɴᴊᴏʏ "
                "ꜱʜᴏʀᴛᴇɴᴇʀ-ꜰʀᴇᴇ ᴅᴏᴡɴʟᴏᴀᴅꜱ.\n\n"
                'ᴄᴏɴᴛᴀᴄᴛ: '
                '<a href="https://t.me/Its_Lozo">'
                "@ɪᴛꜱ_ʟᴏᴢᴏ"
                "</a>"
            ),
            parse_mode="HTML",
            link_preview_options=LinkPreviewOptions(
                is_disabled=True
            ),
        )

    try:
        start = db._dt(
            plan["starts_at"]
        )

        expiry = db._dt(
            plan["expires_at"]
        )

        now = datetime.now(
            timezone.utc
        )

        if expiry <= now:
            return await update.message.reply_text(
                (
                    "💎 <b>ᴍʏ ᴘʟᴀɴ</b>\n\n"
                    "🔴 ꜱᴛᴀᴛᴜꜱ: ꜰʀᴇᴍɪᴜᴍ ᴇxᴘɪʀᴇᴅ\n\n"
                    "⚡ ꜱʜᴏʀᴛᴇɴᴇʀ ʙʏᴘᴀꜱꜱ: ᴅɪꜱᴀʙʟᴇᴅ"
                ),
                parse_mode="HTML",
                link_preview_options=LinkPreviewOptions(
                    is_disabled=True
                ),
            )

        remaining = (
            expiry - now
        )

        total_seconds = int(
            remaining.total_seconds()
        )

        days = (
            total_seconds
            // 86400
        )

        hours = (
            total_seconds
            % 86400
        ) // 3600

        minutes = (
            total_seconds
            % 3600
        ) // 60

        remaining_text = (
            (
                f"{days}d "
                f"{hours}h "
                f"{minutes}m"
            )
            if days > 0
            else (
                f"{hours}h "
                f"{minutes}m"
            )
        )

        user_tz_name = (
            db.get_user_timezone(
                uid
            )
        )

        try:
            user_tz = ZoneInfo(
                user_tz_name
            )

        except Exception:
            user_tz_name = (
                "Asia/Kolkata"
            )

            user_tz = IST

        local_start = (
            start.astimezone(
                user_tz
            )
        )

        local_expiry = (
            expiry.astimezone(
                user_tz
            )
        )

        tz_label = (
            timezone_display_name(
                user_tz_name
            )
        )

        return await update.message.reply_text(
            (
                "💎 <b>ᴍʏ ᴘʟᴀɴ</b>\n\n"
                "🟢 ꜱᴛᴀᴛᴜꜱ: ᴘʀᴇᴍɪᴜᴍ ᴀᴄᴛɪᴠᴇ\n\n"
                f"🌍 ᴛɪᴍᴇᴢᴏɴᴇ: <b>{tz_label}</b>\n\n"
                f"📅 ᴀᴄᴛɪᴠᴀᴛᴇᴅ ᴏɴ: "
                f"<code>{local_start.strftime('%d-%m-%Y %I:%M:%S %p')}</code>\n"
                f"⏳ ᴇxᴘɪʀᴇꜱ ᴏɴ: "
                f"<code>{local_expiry.strftime('%d-%m-%Y %I:%M:%S %p')}</code>\n"
                f"⏱ ᴛɪᴍᴇ ʀᴇᴍᴀɪɴɪɴɢ: "
                f"<b>{remaining_text}</b>\n\n"
                "⚡ ꜱʜᴏʀᴛᴇɴᴇʀ ʙʏᴘᴀꜱꜱ: "
                "🟢 ᴇɴᴀʙʟᴇᴅ\n\n"
                "💎 ᴛʜᴀɴᴋ ʏᴏᴜ ꜰᴏʀ ᴜsɪɴɢ ᴘʀᴇᴍɪᴜᴍ!"
            ),
            parse_mode="HTML",
            link_preview_options=LinkPreviewOptions(
                is_disabled=True
            ),
        )

    except Exception:
        log.exception(
            "Myplan failed"
        )

        return await update.message.reply_text(
            "❌ ᴄᴏᴜʟᴅ ɴᴏᴛ ʟᴏᴀᴅ "
            "ʏᴏᴜʀ ᴘʟᴀɴ ᴅᴇᴛᴀɪʟꜱ."
        )


# =========================================================
# PREMIUM LIST
# =========================================================

async def list_premium(
    update,
    context,
):
    if not admin_ok(
        update.effective_user.id
    ):
        return await update.message.reply_text(
            "❌ ᴜɴᴀᴜᴛʜᴏʀɪᴢᴇᴅ."
        )

    rows = db.list_premium()

    if not rows:
        return await update.message.reply_text(
            "📋 ɴᴏ ᴀᴄᴛɪᴠᴇ ᴘʀᴇᴍɪᴜᴍ "
            "ꜱᴜʙꜱᴄʀɪᴘᴛɪᴏɴꜱ."
        )

    users_cache = {
        int(x["user_id"]): x
        for x in db.list_users()
    }

    lines = [
        "<b>💎 ᴘʀᴇᴍɪᴜᴍ ꜱᴜʙꜱᴄʀɪʙᴇʀꜱ</b>",
        "",
    ]

    for i, r in enumerate(
        rows,
        1,
    ):
        uid = int(
            r["user_id"]
        )

        u = users_cache.get(
            uid,
            {},
        )

        def fmt(value):
            return (
                db._dt(value)
                .astimezone(IST)
                .strftime(
                    "%d-%m-%Y %I:%M:%S %p"
                )
            )

        name = (
            u.get(
                "first_name"
            )
            or "User"
        )

        username = (
            "@"
            + u.get("username")
            if u.get("username")
            else "—"
        )

        lines.append(
            (
                f"<b>#{i}</b>\n"
                f'👤 <a href="tg://user?id={uid}">'
                f"{escape(name)}</a>\n"
                f"🔹 Username: {escape(username)}\n"
                f"🆔 User ID: <code>{uid}</code>\n"
                f"🟢 Start: <code>"
                f"{fmt(r.get('starts_at', r['expires_at']))}"
                f" IST</code>\n"
                f"🔴 End: <code>"
                f"{fmt(r['expires_at'])}"
                f" IST</code>\n"
            )
        )

    text = "\n".join(
        lines
    )

    for pos in range(
        0,
        len(text),
        3900,
    ):
        await update.message.reply_text(
            text[
                pos:pos + 3900
            ],
            parse_mode="HTML",
            disable_web_page_preview=True,
        )


# =========================================================
# BAN USER
# =========================================================

async def banuser(
    update,
    context,
):
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
            "❌ ᴘʟᴇᴀꜱᴇ ᴇɴᴛᴇʀ "
            "ᴀ ᴠᴀʟɪᴅ ᴜꜱᴇʀ ɪᴅ."
        )

    if target_id == OWNER_ID:
        return await update.message.reply_text(
            "❌ ᴏᴡɴᴇʀ ᴄᴀɴɴᴏᴛ "
            "ʙᴇ ʙᴀɴɴᴇᴅ."
        )

    if db.is_banned(
        target_id
    ):
        return await update.message.reply_text(
            "⚠️ ᴛʜɪꜱ ᴜꜱᴇʀ "
            "ɪs ᴀʟʀᴇᴀᴅʏ ʙᴀɴɴᴇᴅ."
        )

    db.ban_user(
        target_id,
        reason="Manual Ban",
    )

    try:
        await context.bot.send_message(
            target_id,
            "🚫 <b>You Are Banned From Using The Bot</b> 🚫",
            parse_mode="HTML",
        )

    except Exception:
        pass

    await update.message.reply_text(
        (
            "✅ <b>ᴜꜱᴇʀ ʙᴀɴɴᴇᴅ "
            "ꜱᴜᴄᴄᴇꜱꜱꜰᴜʟʟʏ.</b>\n\n"
            f"🆔 <code>{target_id}</code>"
        ),
        parse_mode="HTML",
    )


# =========================================================
# UNBAN USER
# =========================================================

async def unbanuser(
    update,
    context,
):
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
            "❌ ᴘʟᴇᴀꜱᴇ ᴇɴᴛᴇʀ "
            "ᴀ ᴠᴀʟɪᴅ ᴜꜱᴇʀ ɪᴅ."
        )

    if not db.is_banned(
        target_id
    ):
        return await update.message.reply_text(
            "⚠️ ᴛʜɪꜱ ᴜꜱᴇʀ "
            "ɪs ɴᴏᴛ ʙᴀɴɴᴇᴅ."
        )

    db.unban_user(
        target_id
    )

    try:
        await context.bot.send_message(
            target_id,
            (
                "✅ <b>Your ban has been removed.</b>\n\n"
                "You can use the bot again."
            ),
            parse_mode="HTML",
        )

    except Exception:
        pass

    await update.message.reply_text(
        (
            "✅ <b>ᴜꜱᴇʀ ᴜɴʙᴀɴɴᴇᴅ "
            "ꜱᴜᴄᴄᴇꜱꜱꜰᴜʟʟʏ.</b>\n\n"
            f"🆔 <code>{target_id}</code>"
        ),
        parse_mode="HTML",
    )


# =========================================================
# BANNED USER LIST
# =========================================================

async def banuser_list(
    update,
    context,
):
    uid = update.effective_user.id

    if not admin_ok(uid):
        return await update.message.reply_text(
            "❌ ᴜɴᴀᴜᴛʜᴏʀɪᴢᴇᴅ."
        )

    banned_users = (
        db.list_banned_users()
    )

    if not banned_users:
        return await update.message.reply_text(
            (
                "📋 <b>ʙᴀɴɴᴇᴅ ᴜꜱᴇʀ ʟɪꜱᴛ</b>\n\n"
                "✅ ɴᴏ ᴜꜱᴇʀ ɪs "
                "ᴄᴜʀʀᴇɴᴛʟʏ ʙᴀɴɴᴇᴅ."
            ),
            parse_mode="HTML",
        )

    lines = [
        "🚫 <b>ʙᴀɴɴᴇᴅ ᴜꜱᴇʀꜱ</b>",
        "",
    ]

    for i, row in enumerate(
        banned_users,
        1,
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
                "@"
                + user.username
                if user.username
                else "No Username"
            )

        except Exception:
            name = "Unknown"
            username = "No Username"

        profile_link = (
            f'<a href="tg://user?id={user_id}">'
            f"{escape(name)}</a>"
        )

        reason = (
            row.get("reason")
            or "Manual Ban"
        )

        banned_at = row.get(
            "banned_at"
        )

        if banned_at:
            try:
                banned_time = (
                    db._dt(
                        banned_at
                    )
                    .astimezone(IST)
                    .strftime(
                        "%d-%m-%Y %I:%M:%S %p"
                    )
                )

            except Exception:
                banned_time = "—"

        else:
            banned_time = "—"

        lines.append(
            (
                f"<b>{i}.</b> {profile_link}\n"
                f"👤 Name: <code>{escape(name)}</code>\n"
                f"🔗 Username: {escape(username)}\n"
                f"🆔 UID: <code>{user_id}</code>\n"
                f"⚠️ Reason: <code>{escape(str(reason))}</code>\n"
                f"🕐 Banned: <code>{banned_time} IST</code>\n"
                f"━━━━━━━━━━━━━━"
            )
        )

    text = "\n".join(
        lines
    )

    if len(text) <= 4000:
        return await update.message.reply_text(
            text,
            parse_mode="HTML",
            disable_web_page_preview=True,
        )

    chunk = ""

    for line in lines:
        if (
            len(chunk)
            + len(line)
            + 1
            > 4000
        ):
            await update.message.reply_text(
                chunk,
                parse_mode="HTML",
                disable_web_page_preview=True,
            )

            chunk = ""

        chunk += (
            line
            + "\n"
        )

    if chunk:
        await update.message.reply_text(
            chunk,
            parse_mode="HTML",
            disable_web_page_preview=True,
        )


# =========================================================
# USERS
# =========================================================

async def users(
    update,
    context,
):
    if not admin_ok(
        update.effective_user.id
    ):
        return await update.message.reply_text(
            "❌ ᴜɴᴀᴜᴛʜᴏʀɪᴢᴇᴅ."
        )

    rows = db.list_users()

    lines = [
        f"<b>👥 ᴜꜱᴇʀꜱ: {len(rows)}</b>",
        "",
    ]

    for r in rows:
        uid = int(
            r["user_id"]
        )

        name = (
            r.get(
                "first_name"
            )
            or "User"
        )

        username = (
            "@"
            + r["username"]
            if r.get("username")
            else "—"
        )

        lines.append(
            (
                f'• <a href="tg://user?id={uid}">'
                f"{escape(name)}</a> | "
                f"{escape(username)} | "
                f"<code>{uid}</code>"
            )
        )

    text = "\n".join(
        lines
    )

    for pos in range(
        0,
        len(text),
        3900,
    ):
        await update.message.reply_text(
            text[
                pos:pos + 3900
            ],
            parse_mode="HTML",
            disable_web_page_preview=True,
        )


# =========================================================
# BROADCAST
# =========================================================

async def broadcast(
    update,
    context,
):
    if not admin_ok(
        update.effective_user.id
    ):
        return await update.message.reply_text(
            "❌ ᴜɴᴀᴜᴛʜᴏʀɪᴢᴇᴅ."
        )

    replied = (
        update.message.reply_to_message
    )

    if not replied:
        return await update.message.reply_text(
            (
                "ʀᴇᴘʟʏ ᴛᴏ ᴛʜᴇ ᴍᴇꜱꜱᴀɢᴇ "
                "ʏᴏᴜ ᴡᴀɴᴛ ᴛᴏ ʙʀᴏᴀᴅᴄᴀꜱᴛ.\n\n"
                "/ʙʀᴏᴀᴅᴄᴀꜱᴛ\n"
                "/ʙʀᴏᴀᴅᴄᴀꜱᴛ 24ʜ"
            )
        )

    arg = (
        context.args[0].lower()
        if context.args
        else ""
    )

    delete_after = None
    mode = "BROADCAST"
    lifespan = "PERMANENT ♾"

    if arg.endswith("h"):
        try:
            hours = int(arg[:-1])

            if hours > 0:
                delete_after = (
                    datetime.now(
                        timezone.utc
                    )
                    + timedelta(
                        hours=hours
                    )
                )

                mode = "PBROADCAST"
                lifespan = (
                    f"{hours} "
                    f"{'HOUR' if hours == 1 else 'HOURS'}"
                )

        except Exception:
            pass

    # =====================================================
    # BROADCAST INITIALIZATION MESSAGE
    # =====================================================

    initialization_message = await update.message.reply_text(
        (
            "🚀 <b>Broadcast Initialization Started...</b>\n\n"
            f"⚙️ <b>Type:</b> {mode}\n"
            f"⏱ <b>Lifespan:</b> {lifespan}\n"
            "⏳ <b>Please wait till system transfers blocks...</b>"
        ),
        parse_mode="HTML",
    )

    rows = db.list_users()

    total = len(rows)

    success = 0
    blocked = 0
    failed = 0

    sent = []

    for r in rows:
        uid = int(
            r["user_id"]
        )

        try:
            message = await context.bot.copy_message(
                chat_id=uid,
                from_chat_id=replied.chat_id,
                message_id=replied.message_id,
                reply_markup=replied.reply_markup,
            )

            success += 1

            sent.append(
                (
                    uid,
                    message.message_id,
                )
            )

        except Exception as e:
            error_text = str(e).lower()

            if (
                "blocked" in error_text
                or "chat not found" in error_text
            ):
                blocked += 1

                try:
                    db.delete_user(uid)
                except Exception:
                    pass

            else:
                failed += 1

    # =====================================================
    # AUTO DELETE SCHEDULE
    # =====================================================

    if delete_after:

        async def delete_broadcast_job(
            ctx
        ):
            for (
                user_id,
                message_id,
            ) in sent:

                try:
                    await ctx.bot.delete_message(
                        chat_id=user_id,
                        message_id=message_id,
                    )

                except Exception:
                    pass

        delay = max(
            1,
            (
                delete_after
                - datetime.now(
                    timezone.utc
                )
            ).total_seconds(),
        )

        context.job_queue.run_once(
            delete_broadcast_job,
            delay,
        )

    # =====================================================
    # DELETE INITIALIZATION MESSAGE
    # =====================================================

    try:
        await initialization_message.delete()
    except Exception:
        pass

    # =====================================================
    # FINAL STATS
    # =====================================================

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

    await update.message.reply_text(
        stats,
        parse_mode="HTML",
    )


# =========================================================
# DB CHANNEL INDEXER
# =========================================================

async def channel_post_indexer(
    update,
    context,
):
    post = update.channel_post

    if (
        not post
        or post.chat_id != DB_CHANNEL_ID
    ):
        return

    try:
        db.add_file(
            DB_CHANNEL_ID,
            post.message_id,
            post.caption
            or post.text
            or "",
        )

        token = db.create_main_link(
            (
                f"message:"
                f"{DB_CHANNEL_ID}:"
                f"{post.message_id}"
            )
        )

        url = main_link_url(
            token
        )

        markup = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "↗ ꜱʜᴀʀᴇ ᴜʀʟ",
                        url=share_url(url),
                    )
                ]
            ]
        )

        try:
            await context.bot.edit_message_reply_markup(
                chat_id=DB_CHANNEL_ID,
                message_id=post.message_id,
                reply_markup=markup,
            )

        except Exception:
            log.exception(
                "Could not add share button "
                "to DB message"
            )

    except Exception:
        log.exception(
            "DB channel indexing failed"
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
            ban_guard,
        ),
        group=-1,
    )

    # =====================================================
    # COMMAND HANDLERS
    # =====================================================

    app.add_handler(
        CommandHandler(
            "start",
            with_wait("ᴘʟᴇᴀꜱᴇ ᴡᴀɪᴛ.....")(start),
        )
    )

    app.add_handler(
        CommandHandler(
            "genlink",
            with_wait("ɢᴇɴᴇʀᴀᴛɪɴɢ ʟɪɴᴋ...")(genlink),
        )
    )

    app.add_handler(
        CommandHandler(
            "batch",
            with_wait("ɢᴇɴᴇʀᴀᴛɪɴɢ ʟɪɴᴋ...")(batch),
        )
    )

    app.add_handler(
        CommandHandler(
            "settings",
            with_wait("ᴘʟᴇᴀꜱᴇ ᴡᴀɪᴛ.....")(settings),
        )
    )

    app.add_handler(
        CommandHandler(
            "banuser",
            with_wait("ᴘʟᴇᴀꜱᴇ ᴡᴀɪᴛ.....")(banuser),
        )
    )

    app.add_handler(
        CommandHandler(
            "unbanuser",
            with_wait("ᴘʟᴇᴀꜱᴇ ᴡᴀɪᴛ.....")(unbanuser),
        )
    )

    app.add_handler(
        CommandHandler(
            "banuser_list",
            with_wait("ᴘʟᴇᴀꜱᴇ ᴡᴀɪᴛ.....")(banuser_list),
        )
    )

    app.add_handler(
        CommandHandler(
            "addsubs",
            with_wait("ᴘʟᴇᴀꜱᴇ ᴡᴀɪᴛ.....")(addsubs),
        )
    )

    app.add_handler(
        CommandHandler(
            "removesubs",
            with_wait("ᴘʟᴇᴀꜱᴇ ᴡᴀɪᴛ.....")(removesubs),
        )
    )

    app.add_handler(
        CommandHandler(
            "list_premium",
            with_wait(
                "ꜰᴇᴛᴄʜɪɴɢ ᴘʀᴇᴍɪᴜᴍ ᴜꜱᴇʀꜱ ʟɪꜱᴛ...."
            )(list_premium),
        )
    )

    app.add_handler(
        CommandHandler(
            "users",
            with_wait("ꜰᴇᴛᴄʜɪɴɢ ᴜꜱᴇʀꜱ....")(users),
        )
    )

    app.add_handler(
        CommandHandler(
            "broadcast",
            with_wait("ᴘʟᴇᴀꜱᴇ ᴡᴀɪᴛ.....")(broadcast),
        )
    )

    app.add_handler(
        CommandHandler(
            "myplan",
            with_wait("ᴘʟᴇᴀꜱᴇ ᴡᴀɪᴛ.....")(myplan),
        )
    )

    app.add_handler(
        CommandHandler(
            "timezone",
            with_wait("ᴘʟᴇᴀꜱᴇ ᴡᴀɪᴛ.....")(timezone_command),
        )
    )

    # =====================================================
    # CALLBACK
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
            settings_input,
        ),
        group=1,
    )

    # =====================================================
    # DB CHANNEL INDEXER
    # =====================================================

    app.add_handler(
        MessageHandler(
            filters.ALL,
            channel_post_indexer,
        ),
        group=10,
    )

    log.info(
        "Bot starting"
    )

    app.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True,
        close_loop=False,
    )


if __name__ == "__main__":
    main()
