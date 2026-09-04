import asyncio
import logging
import os
import re
import uuid
from datetime import datetime, timezone

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    LinkPreviewOptions,
)

from fastapi import FastAPI
import uvicorn

from database import Database


# =========================================================
# LOGGING
# =========================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(name)s - %(message)s"
)

log = logging.getLogger("file-store-bot")


# =========================================================
# ENVIRONMENT
# =========================================================

BOT_TOKEN = os.environ["BOT_TOKEN"]
OWNER_ID = int(os.environ["OWNER_ID"])

PORT = int(os.getenv("PORT", "10000"))
BOT_USERNAME = os.getenv("BOT_USERNAME", "").lstrip("@")

# Supabase
db = Database()


# =========================================================
# BOT
# =========================================================

bot = Bot(
    BOT_TOKEN,
    default=DefaultBotProperties(
        parse_mode=ParseMode.HTML
    )
)

dp = Dispatcher()
router = Router()

dp.include_router(router)


# =========================================================
# FASTAPI
# =========================================================

app = FastAPI()


@app.get("/")
async def health():
    return {
        "ok": True,
        "service": "telegram-file-store-bot"
    }


@app.get("/health")
async def health2():
    return {
        "ok": True
    }


# =========================================================
# HELPERS
# =========================================================

def now():
    return datetime.now(timezone.utc)


def is_owner(user_id: int):
    return user_id == OWNER_ID


def is_admin(user_id: int):
    return is_owner(user_id) or db.is_admin(user_id)


def is_mod(user_id: int):
    return is_admin(user_id)


def profile_link(user_id: int, name: str):
    safe_name = name or "User"

    return (
        f'<a href="tg://user?id={user_id}">'
        f'{safe_name}</a>'
    )


def escape_html(text):
    if not text:
        return ""

    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def format_remaining(expires_at):
    try:
        expiry = datetime.fromisoformat(
            str(expires_at).replace("Z", "+00:00")
        )

        if expiry.tzinfo is None:
            expiry = expiry.replace(
                tzinfo=timezone.utc
            )

        remaining = expiry - now()

        if remaining.total_seconds() <= 0:
            return "0 Days, 0 Hours"

        total_hours = int(
            remaining.total_seconds() // 3600
        )

        days = total_hours // 24
        hours = total_hours % 24

        return f"{days} Days, {hours} Hours"

    except Exception:
        return "Unknown"


def format_days_from_expiry(expires_at):
    try:
        expiry = datetime.fromisoformat(
            str(expires_at).replace("Z", "+00:00")
        )

        if expiry.tzinfo is None:
            expiry = expiry.replace(
                tzinfo=timezone.utc
            )

        remaining = expiry - now()

        if remaining.total_seconds() <= 0:
            return 0

        return int(
            remaining.total_seconds() // 86400
        )

    except Exception:
        return 0


# =========================================================
# USER
# =========================================================

def ensure_user_from_message(message: Message):
    if not message.from_user:
        return

    db.add_user(
        message.from_user.id,
        message.from_user.username or "",
        message.from_user.first_name or ""
    )


def get_user_name(user_id: int):
    return db_user_name(user_id)


def db_user_name(user_id: int):
    # database.py doesn't expose a direct name getter,
    # so retrieve from users through Supabase.
    try:
        result = db.db.table("users").select(
            "username, first_name"
        ).eq(
            "user_id",
            user_id
        ).limit(1).execute()

        if result.data:
            row = result.data[0]

            return (
                row.get("first_name")
                or row.get("username")
                or f"User {user_id}"
            )

    except Exception:
        pass

    try:
        # Telegram fallback
        # This function is normally called after the
        # user's record exists.
        return f"User {user_id}"
    except Exception:
        return f"User {user_id}"


async def telegram_user_name(user_id: int):
    name = get_user_name(user_id)

    if name != f"User {user_id}":
        return name

    try:
        chat = await bot.get_chat(user_id)

        if chat.first_name:
            return chat.first_name

        if chat.username:
            return chat.username

    except Exception:
        pass

    return f"User {user_id}"


# =========================================================
# LINK PREVIEW
# =========================================================

def link_preview_disabled():
    return LinkPreviewOptions(
        is_disabled=True
    )


# =========================================================
# SETTINGS
# =========================================================

async def get_setting(key, default=None):
    return db.get_setting(
        key,
        default
    )


async def set_setting(key, value):
    db.set_setting(
        key,
        value
    )


# =========================================================
# START IMAGE
# =========================================================

async def get_start_image():
    return await get_setting(
        "start_image",
        None
    )


# =========================================================
# AUTO DELETE
# =========================================================

def schedule_delete(
    chat_id: int,
    message_id: int,
    seconds: int
):
    asyncio.create_task(
        delete_message_later(
            chat_id,
            message_id,
            seconds
        )
    )


async def delete_message_later(
    chat_id: int,
    message_id: int,
    seconds: int
):
    try:
        await asyncio.sleep(seconds)

        await bot.delete_message(
            chat_id,
            message_id
        )

    except Exception as e:
        log.debug(
            "Auto delete failed: %s",
            e
        )


async def send_auto_delete_notice(
    chat_id: int,
    minutes: int
):
    if minutes <= 0:
        return None

    text = (
        "<i>This File is deleting automatically "
        f"in {minutes} minutes. "
        "Forward in your Saved Messages..!</i>"
    )

    msg = await bot.send_message(
        chat_id,
        text
    )

    schedule_delete(
        chat_id,
        msg.message_id,
        minutes * 60
    )

    return msg


# =========================================================
# START INTERFACE
# =========================================================

START_TEXT = (
    "<b><i>Hi There...! 💥</i></b>\n\n"
    "<i>I am a file-store bot.\n"
    "I can generate links directly with no problems.</i>\n\n"
    '<b>My Owner:</b> '
    '<a href="https://t.me/Its_Lozo">@Its_Lozo</a>'
)


def start_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="ABOUT",
                    callback_data="about"
                ),
                InlineKeyboardButton(
                    text="CLOSE",
                    callback_data="close"
                )
            ]
        ]
    )


async def send_start_interface(target):

    image = await get_start_image()

    keyboard = start_keyboard()

    if isinstance(target, Message):

        if image:
            try:
                return await target.answer_photo(
                    photo=image,
                    caption=START_TEXT,
                    reply_markup=keyboard
                )
            except Exception as e:
                log.warning(
                    "Start image failed: %s",
                    e
                )

        return await target.answer(
            START_TEXT,
            reply_markup=keyboard,
            link_preview_options=link_preview_disabled()
        )

    message = target.message

    if image:
        try:
            return await message.answer_photo(
                photo=image,
                caption=START_TEXT,
                reply_markup=keyboard
            )
        except Exception as e:
            log.warning(
                "Start image failed: %s",
                e
            )

    return await message.answer(
        START_TEXT,
        reply_markup=keyboard,
        link_preview_options=link_preview_disabled()
    )


# =========================================================
# ABOUT
# =========================================================

ABOUT_TEXT = (
    "<b><i>About Us..</i></b>\n\n"
    "<i>"
    "➤ Made for : "
    '<a href="https://t.me/Anime_Hub_94">Anime Hub</a>\n'
    "➤ Owner : "
    '<a href="https://t.me/Its_Lozo">@Its_Lozo</a>\n'
    "➤ Developer : "
    '<a href="https://t.me/Its_Lozo">@Its_Lozo</a>\n\n'
    "Adios !!"
    "</i>"
)


def about_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="BACK",
                    callback_data="back_start"
                ),
                InlineKeyboardButton(
                    text="CLOSE",
                    callback_data="close"
                )
            ]
        ]
    )


@router.callback_query(
    F.data == "about"
)
async def about_callback(
    callback: CallbackQuery
):
    await callback.answer()

    try:
        await callback.message.edit_caption(
            caption=ABOUT_TEXT,
            reply_markup=about_keyboard()
        )
    except Exception:
        try:
            await callback.message.edit_text(
                ABOUT_TEXT,
                reply_markup=about_keyboard()
            )
        except Exception as e:
            log.warning(
                "About edit failed: %s",
                e
            )


@router.callback_query(
    F.data == "back_start"
)
async def back_start_callback(
    callback: CallbackQuery
):
    await callback.answer()

    try:
        await callback.message.delete()
    except Exception:
        pass

    await send_start_interface(callback)


@router.callback_query(
    F.data == "close"
)
async def close_callback(
    callback: CallbackQuery
):
    await callback.answer()

    try:
        await callback.message.delete()
    except Exception:
        pass


# =========================================================
# MEDIA DETECTION
# =========================================================

def media_kind(message: Message):

    if message.photo:
        return "photo"

    if message.video:
        return "video"

    if message.document:
        return "document"

    if message.audio:
        return "audio"

    if message.voice:
        return "voice"

    if message.animation:
        return "animation"

    if message.sticker:
        return "sticker"

    if message.text or message.caption:
        return "text"

    return None


# =========================================================
# PARSE TELEGRAM CHANNEL LINK
# =========================================================

def parse_link(link: str):

    link = link.strip()

    match = re.match(
        r"https?://t\.me/c/(\d+)/(\d+)",
        link
    )

    if match:
        return (
            int("-100" + match.group(1)),
            int(match.group(2))
        )

    match = re.match(
        r"https?://t\.me/([A-Za-z0-9_]+)/(\d+)",
        link
    )

    if match:
        return (
            "@" + match.group(1),
            int(match.group(2))
        )

    return None


# =========================================================
# FSUB
# =========================================================

async def get_fsub_channels():
    return await get_setting(
        "fsub_channels",
        []
    )


async def save_fsub_channels(items):
    await set_setting(
        "fsub_channels",
        items
    )


async def check_user_joined(
    user_id: int,
    channel_id
):

    try:
        member = await bot.get_chat_member(
            chat_id=channel_id,
            user_id=user_id
        )

        raw_status = getattr(
            member,
            "status",
            None
        )

        status = getattr(
            raw_status,
            "value",
            str(raw_status)
        )

        status = str(status).lower()

        if status in (
            "creator",
            "administrator",
            "member"
        ):
            return True

        if status == "restricted":
            return bool(
                getattr(
                    member,
                    "is_member",
                    False
                )
            )

        return False

    except Exception as e:
        log.warning(
            "FSUB check failed: %s",
            e
        )

        return False


async def build_fsub_message(
    user_id: int
):

    fsubs = await get_fsub_channels()

    if not fsubs:
        return None, None, True

    lines = [
        "🔵 <b>Hello There..!⚡</b>",
        "",
        "⭕ Please join all of our channels first "
        "then try again..!",
        "",
        "<b>Channel Subscription Status:</b>",
        ""
    ]

    buttons = []
    all_joined = True

    for index, channel in enumerate(
        fsubs,
        start=1
    ):

        channel_id = channel.get("chat_id")

        title = channel.get(
            "title",
            "Channel"
        )

        joined = await check_user_joined(
            user_id,
            channel_id
        )

        if joined:
            lines.append(
                f"{index}. <b>{title}</b> - "
                "<b>JOINED</b> ✅"
            )
        else:
            all_joined = False

            lines.append(
                f"{index}. <b>{title}</b> - "
                "<b>NOT JOINED</b> ❌"
            )

            invite = channel.get(
                "invite_link"
            )

            if invite:
                buttons.append(
                    [
                        InlineKeyboardButton(
                            text=f"Join {title}",
                            url=invite
                        )
                    ]
                )

    if not all_joined:
        buttons.append(
            [
                InlineKeyboardButton(
                    text="🔄 Try Again",
                    callback_data="fsub_check"
                )
            ]
        )

    keyboard = None

    if buttons:
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=buttons
        )

    return (
        "\n".join(lines),
        keyboard,
        all_joined
    )


async def show_fsub(message: Message):

    text, keyboard, all_joined = (
        await build_fsub_message(
            message.from_user.id
        )
    )

    if all_joined:
        return False

    image = await get_start_image()

    if image:
        try:
            await message.answer_photo(
                photo=image,
                caption=text,
                reply_markup=keyboard
            )
            return True
        except Exception:
            pass

    await message.answer(
        text,
        reply_markup=keyboard
    )

    return True


@router.callback_query(
    F.data == "fsub_check"
)
async def fsub_check_callback(
    callback: CallbackQuery
):

    text, keyboard, all_joined = (
        await build_fsub_message(
            callback.from_user.id
        )
    )

    if all_joined:
        await callback.answer(
            "✅ All channels joined!"
        )

        try:
            await callback.message.delete()
        except Exception:
            pass

        await send_start_interface(callback)
        return

    await callback.answer(
        "❌ You haven't joined all channels yet.",
        show_alert=True
    )

    try:
        await callback.message.edit_caption(
            caption=text,
            reply_markup=keyboard
        )
    except Exception:
        try:
            await callback.message.edit_text(
                text,
                reply_markup=keyboard
            )
        except Exception:
            pass


# =========================================================
# START
# =========================================================

@router.message(
    CommandStart()
)
async def start(
    message: Message
):

    ensure_user_from_message(message)

    if db.is_banned(
        message.from_user.id
    ):
        return await message.answer(
            "🚫 You are banned from using this bot."
        )

    args = (
        (message.text or "")
        .split(maxsplit=1)
    )

    # =====================================================
    # BATCH LINK
    # =====================================================

    if (
        len(args) == 2
        and args[1].startswith("batch_")
    ):

        if await show_fsub(message):
            return

        batch_id = args[1][6:]

        items = db.get_batch_items(
            batch_id
        )

        if not items:
            return await message.answer(
                "❌ Batch not found or expired."
            )

        wait_msg = await message.answer(
            "Please Wait....."
        )

        sent_messages = []

        for item in items:
            try:
                copied = await bot.copy_message(
                    chat_id=message.chat.id,
                    from_chat_id=item["channel_id"],
                    message_id=item["message_id"]
                )

                sent_messages.append(copied)

                await asyncio.sleep(0.02)

            except Exception as e:
                log.warning(
                    "Batch copy failed: %s",
                    e
                )

        try:
            await wait_msg.delete()
        except Exception:
            pass

        delete_minutes = int(
            await get_setting(
                "auto_delete_minutes",
                5
            )
        )

        if delete_minutes > 0:

            for sent in sent_messages:
                schedule_delete(
                    message.chat.id,
                    sent.message_id,
                    delete_minutes * 60
                )

            await send_auto_delete_notice(
                message.chat.id,
                delete_minutes
            )

        return

    # =====================================================
    # NORMAL START
    # =====================================================

    if await show_fsub(message):
        return

    await send_start_interface(message)


# =========================================================
# GENLINK
# =========================================================

@router.message(
    Command("genlink")
)
async def genlink(
    message: Message
):

    ensure_user_from_message(message)

    if not is_mod(
        message.from_user.id
    ):
        return await message.answer(
            "⛔ Only Owner, Admins and Moderators "
            "can generate links."
        )

    if not message.reply_to_message:
        return await message.answer(
            "Reply to any message/file and use "
            "<code>/genlink</code>."
        )

    source = message.reply_to_message

    if not media_kind(source):
        return await message.answer(
            "❌ This message cannot be stored."
        )

    file_id = db.add_file(
        source.chat.id,
        source.message_id,
        source.caption or source.text or ""
    )

    batch_id = db.create_batch(
        [file_id]
    )

    username = await get_bot_username()

    link = (
        f"https://t.me/{username}"
        f"?start=batch_{batch_id}"
    )

    await message.answer(
        "🔗 <b>Link created!</b>\n\n"
        f"{link}",
        link_preview_options=link_preview_disabled()
    )


# =========================================================
# BOT USERNAME
# =========================================================

async def get_bot_username():

    global BOT_USERNAME

    if BOT_USERNAME:
        return BOT_USERNAME

    me = await bot.get_me()

    BOT_USERNAME = me.username or ""

    return BOT_USERNAME


# =========================================================
# BATCH
# =========================================================

@router.message(
    Command("batch")
)
async def batch(
    message: Message
):

    ensure_user_from_message(message)

    if not is_mod(
        message.from_user.id
    ):
        return await message.answer(
            "⛔ Only Owner, Admins and Moderators "
            "can create batches."
        )

    parts = (
        message.text or ""
    ).split()

    if len(parts) != 3:
        return await message.answer(
            "Usage:\n"
            "<code>/batch LINK1 LINK2</code>\n\n"
            "Both links must be from the same channel."
        )

    first = parse_link(parts[1])
    second = parse_link(parts[2])

    if not first or not second:
        return await message.answer(
            "❌ Invalid Telegram channel post link."
        )

    if first[0] != second[0]:
        return await message.answer(
            "❌ Both posts must be from the same channel."
        )

    chat_id = first[0]

    lo, hi = sorted([
        first[1],
        second[1]
    ])

    file_ids = []

    # database.py doesn't expose a range query,
    # so use the files table directly through Supabase.
    result = db.db.table("files").select(
        "*"
    ).eq(
        "channel_id",
        int(chat_id)
    ).gte(
        "message_id",
        lo
    ).lte(
        "message_id",
        hi
    ).order(
        "message_id"
    ).execute()

    rows = result.data or []

    if not rows:
        return await message.answer(
            "❌ I don't have these channel posts "
            "recorded yet."
        )

    for row in rows:
        file_ids.append(
            row["file_id"]
        )

    if not file_ids:
        return await message.answer(
            "❌ No supported posts found."
        )

    batch_id = db.create_batch(
        file_ids
    )

    username = await get_bot_username()

    link = (
        f"https://t.me/{username}"
        f"?start=batch_{batch_id}"
    )

    await message.answer(
        "✅ <b>Batch created!</b>\n\n"
        f"📦 Items: <b>{len(file_ids)}</b>\n"
        f"🔗 {link}",
        link_preview_options=link_preview_disabled()
    )


# =========================================================
# PREMIUM
# =========================================================

async def notify_premium_activated(
    user_id: int,
    days: int
):

    text = (
        "🎉 <b>Congratulations!</b>\n\n"
        "Your account has been upgraded to the "
        f"Premium Ad-Free Tier for the next "
        f"<b>{days} Days</b>.\n"
        "Enjoy high-speed bypass-free file downloads!"
    )

    try:
        await bot.send_message(
            user_id,
            text
        )
        return True
    except Exception as e:
        log.warning(
            "Premium activation notification failed "
            "for %s: %s",
            user_id,
            e
        )
        return False


async def notify_premium_revoked(
    user_id: int
):

    text = (
        "🚨 <b>Notification:</b> Your premium "
        "subscription package has been manually "
        "revoked by the management team."
    )

    try:
        await bot.send_message(
            user_id,
            text
        )
        return True
    except Exception as e:
        log.warning(
            "Premium revoke notification failed "
            "for %s: %s",
            user_id,
            e
        )
        return False


# =========================================================
# ADD PREMIUM
# =========================================================

@router.message(
    Command("addpremium")
)
async def addpremium(
    message: Message
):

    ensure_user_from_message(message)

    if not is_admin(
        message.from_user.id
    ):
        return await message.answer(
            "⛔ Admin only."
        )

    parts = (
        message.text or ""
    ).split()

    if (
        len(parts) != 3
        or not parts[1].isdigit()
        or not parts[2].isdigit()
    ):
        return await message.answer(
            "Usage:\n"
            "<code>/addpremium USER_ID DAYS</code>"
        )

    user_id = int(parts[1])
    days = int(parts[2])

    if days <= 0:
        return await message.answer(
            "❌ Days must be greater than 0."
        )

    try:
        db.add_premium(
            user_id,
            days
        )

        await notify_premium_activated(
            user_id,
            days
        )

        user_name = await telegram_user_name(
            user_id
        )

        await message.answer(
            "🎉 <b>Premium Activated!</b>\n\n"
            f"👤 {profile_link(user_id, user_name)}\n"
            f"🆔 <code>{user_id}</code>\n"
            f"💎 Plan: <b>{days} Days</b>"
        )

    except Exception as e:

        log.exception(
            "Premium activation error"
        )

        await message.answer(
            f"❌ Failed to activate premium.\n"
            f"<code>{escape_html(e)}</code>"
        )


# =========================================================
# REMOVE PREMIUM
# =========================================================

@router.message(
    Command("delpremium")
)
async def delpremium(
    message: Message
):

    ensure_user_from_message(message)

    if not is_admin(
        message.from_user.id
    ):
        return await message.answer(
            "⛔ Admin only."
        )

    parts = (
        message.text or ""
    ).split()

    if (
        len(parts) != 2
        or not parts[1].isdigit()
    ):
        return await message.answer(
            "Usage:\n"
            "<code>/delpremium USER_ID</code>"
        )

    user_id = int(parts[1])

    if not db.is_premium(user_id):
        return await message.answer(
            "❌ This user does not have an active "
            "premium subscription."
        )

    user_name = await telegram_user_name(
        user_id
    )

    db.remove_premium(
        user_id
    )

    await notify_premium_revoked(
        user_id
    )

    await message.answer(
        "🗑 <b>Premium Subscription Tier Revoked!</b>\n\n"
        f"👤 {profile_link(user_id, user_name)}\n"
        f"🆔 <code>{user_id}</code>"
    )


# =========================================================
# MY PLAN
# =========================================================

@router.message(
    Command("myplan")
)
async def myplan(
    message: Message
):

    ensure_user_from_message(message)

    user_id = message.from_user.id

    premium = db.get_premium(
        user_id
    )

    user_name = (
        message.from_user.first_name
        or message.from_user.username
        or "User"
    )

    if not premium:

        return await message.answer(
            "👑 <b>YOUR PREMIUM MEMBERSHIP STATUS</b>\n\n"
            f"👤 Name: {escape_html(user_name)}\n"
            f"🆔 User ID: <code>{user_id}</code>\n"
            "💎 Plan Status: <b>Inactive</b>\n"
            "⏳ Time Remaining: <b>0 Days, 0 Hours</b>"
        )

    remaining = format_remaining(
        premium["expires_at"]
    )

    await message.answer(
        "👑 <b>YOUR PREMIUM MEMBERSHIP STATUS</b>\n\n"
        f"👤 Name: {escape_html(user_name)}\n"
        f"🆔 User ID: <code>{user_id}</code>\n"
        "💎 Plan Status: <b>Active</b>\n"
        f"⏳ Time Remaining: <b>{remaining}</b>"
    )


# =========================================================
# PREMIUM ALIAS
# =========================================================

@router.message(
    Command("premium")
)
async def premium_alias(
    message: Message
):
    await myplan(message)


# =========================================================
# ADD ADMIN
# =========================================================

@router.message(
    Command("addadmin")
)
async def addadmin(
    message: Message
):

    if not is_owner(
        message.from_user.id
    ):
        return await message.answer(
            "⛔ Owner only."
        )

    parts = (
        message.text or ""
    ).split()

    if (
        len(parts) != 2
        or not parts[1].isdigit()
    ):
        return await message.answer(
            "Usage:\n"
            "<code>/addadmin USER_ID</code>"
        )

    uid = int(parts[1])

    name = await telegram_user_name(uid)

    db.add_admin(uid)

    await message.answer(
        "✅ <b>Admin added!</b>\n\n"
        f"👤 {profile_link(uid, name)}\n"
        f"🆔 <code>{uid}</code>"
    )


# =========================================================
# DELETE ADMIN
# =========================================================

@router.message(
    Command("deladmin")
)
async def deladmin(
    message: Message
):

    if not is_owner(
        message.from_user.id
    ):
        return await message.answer(
            "⛔ Owner only."
        )

    parts = (
        message.text or ""
    ).split()

    if (
        len(parts) != 2
        or not parts[1].isdigit()
    ):
        return await message.answer(
            "Usage:\n"
            "<code>/deladmin USER_ID</code>"
        )

    uid = int(parts[1])

    db.remove_admin(uid)

    await message.answer(
        "✅ Admin removed."
    )


# =========================================================
# BAN
# =========================================================

@router.message(
    Command("ban")
)
async def ban(
    message: Message
):

    ensure_user_from_message(message)

    if not is_mod(
        message.from_user.id
    ):
        return await message.answer(
            "⛔ Moderator/Admin only."
        )

    parts = (
        message.text or ""
    ).split()

    if (
        len(parts) != 2
        or not parts[1].isdigit()
    ):
        return await message.answer(
            "Usage:\n"
            "<code>/ban USER_ID</code>"
        )

    uid = int(parts[1])

    db.ban_user(
        uid,
        message.from_user.id
    )

    await message.answer(
        "🚫 User banned."
    )


# =========================================================
# UNBAN
# =========================================================

@router.message(
    Command("unban")
)
async def unban(
    message: Message
):

    if not is_mod(
        message.from_user.id
    ):
        return await message.answer(
            "⛔ Moderator/Admin only."
        )

    parts = (
        message.text or ""
    ).split()

    if (
        len(parts) != 2
        or not parts[1].isdigit()
    ):
        return await message.answer(
            "Usage:\n"
            "<code>/unban USER_ID</code>"
        )

    uid = int(parts[1])

    db.unban_user(uid)

    await message.answer(
        "✅ User unbanned."
    )


# =========================================================
# ADMINS LIST
# =========================================================

@router.message(
    Command("admins")
)
async def admins_list(
    message: Message
):

    if not is_mod(
        message.from_user.id
    ):
        return await message.answer(
            "⛔ Admin/Moderator only."
        )

    text = (
        "👑 <b>Owner</b>\n"
        f"🆔 <code>{OWNER_ID}</code>\n\n"
        "🛡 <b>Admins</b>\n"
    )

    admin_list = db.list_admins()

    if admin_list:
        for uid in admin_list:
            name = await telegram_user_name(uid)

            text += (
                f"• {profile_link(uid, name)} "
                f"— <code>{uid}</code>\n"
            )
    else:
        text += "• None\n"

    await message.answer(text)


# =========================================================
# SET IMAGE
# =========================================================

async def set_image_from_message(
    message: Message
):

    if not message.reply_to_message:
        return False

    photo = message.reply_to_message.photo

    if not photo:
        return False

    file_id = photo[-1].file_id

    await set_setting(
        "start_image",
        file_id
    )

    return True


@router.message(
    Command("setimage")
)
async def setimage_command(
    message: Message
):

    if not is_admin(
        message.from_user.id
    ):
        return await message.answer(
            "⛔ Admin only."
        )

    if await set_image_from_message(message):
        return await message.answer(
            "✅ <b>Start image updated successfully.</b>"
        )

    await message.answer(
        "🖼 <b>Set Start Image</b>\n\n"
        "Settings → Set Image me jaakar "
        "photo bhejiye."
    )


# =========================================================
# SETTINGS KEYBOARD
# =========================================================

def settings_keyboard():

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🖼 Set Image",
                    callback_data="settings_image"
                )
            ],
            [
                InlineKeyboardButton(
                    text="⏱ Auto Delete",
                    callback_data="settings_autodelete"
                )
            ],
            [
                InlineKeyboardButton(
                    text="📢 Force Subscribe",
                    callback_data="settings_fsub"
                )
            ],
            [
                InlineKeyboardButton(
                    text="👑 Admins",
                    callback_data="settings_admins"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🛡 Moderators",
                    callback_data="settings_mods"
                )
            ],
            [
                InlineKeyboardButton(
                    text="❌ Close",
                    callback_data="close"
                )
            ]
        ]
    )


@router.message(
    Command("settings")
)
async def settings_command(
    message: Message
):

    if not is_admin(
        message.from_user.id
    ):
        return await message.answer(
            "⛔ Admin only."
        )

    await message.answer(
        "⚙️ <b>Settings</b>\n\n"
        "<i>Please choose an option below.</i>",
        reply_markup=settings_keyboard()
    )


# =========================================================
# SETTINGS IMAGE
# =========================================================

@router.callback_query(
    F.data == "settings_image"
)
async def settings_image_callback(
    callback: CallbackQuery
):

    if not is_admin(
        callback.from_user.id
    ):
        return await callback.answer(
            "⛔ Admin only.",
            show_alert=True
        )

    await callback.answer()

    await callback.message.edit_text(
        "🖼 <b>Set Start Image</b>\n\n"
        "Ab isi chat me koi bhi new photo bhej dijiye.\n"
        "Bot automatically usse start image set kar dega.",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="🔙 Back",
                        callback_data="settings_back"
                    )
                ]
            ]
        )
    )

    # Pending image state
    current = await get_setting(
        "pending_image_users",
        []
    )

    if callback.from_user.id not in current:
        current.append(
            callback.from_user.id
        )

    await set_setting(
        "pending_image_users",
        current
    )


@router.message(
    F.photo
)
async def receive_new_start_image(
    message: Message
):

    if not message.from_user:
        return

    if not is_admin(
        message.from_user.id
    ):
        return

    pending = await get_setting(
        "pending_image_users",
        []
    )

    if message.from_user.id not in pending:
        return

    file_id = message.photo[-1].file_id

    await set_setting(
        "start_image",
        file_id
    )

    pending = [
        uid
        for uid in pending
        if uid != message.from_user.id
    ]

    await set_setting(
        "pending_image_users",
        pending
    )

    await message.answer(
        "✅ <b>Start image updated successfully.</b>"
    )


# =========================================================
# AUTO DELETE SETTINGS
# =========================================================

@router.callback_query(
    F.data == "settings_autodelete"
)
async def settings_autodelete(
    callback: CallbackQuery
):

    if not is_admin(
        callback.from_user.id
    ):
        return await callback.answer(
            "⛔ Admin only.",
            show_alert=True
        )

    current = await get_setting(
        "auto_delete_minutes",
        5
    )

    await callback.answer()

    await callback.message.edit_text(
        "⏱ <b>Auto Delete Settings</b>\n\n"
        f"Current: <b>{current} minutes</b>",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="1 Minute",
                        callback_data="ad_1"
                    ),
                    InlineKeyboardButton(
                        text="5 Minutes",
                        callback_data="ad_5"
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="10 Minutes",
                        callback_data="ad_10"
                    ),
                    InlineKeyboardButton(
                        text="30 Minutes",
                        callback_data="ad_30"
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="60 Minutes",
                        callback_data="ad_60"
                    ),
                    InlineKeyboardButton(
                        text="OFF",
                        callback_data="ad_0"
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="🔙 Back",
                        callback_data="settings_back"
                    )
                ]
            ]
        )
    )


@router.callback_query(
    F.data.startswith("ad_")
)
async def set_autodelete(
    callback: CallbackQuery
):

    if not is_admin(
        callback.from_user.id
    ):
        return await callback.answer(
            "⛔ Admin only.",
            show_alert=True
        )

    minutes = int(
        callback.data.split("_")[1]
    )

    await set_setting(
        "auto_delete_minutes",
        minutes
    )

    await callback.answer(
        "✅ Auto delete updated!"
    )

    current = (
        "OFF"
        if minutes == 0
        else f"{minutes} minutes"
    )

    await callback.message.edit_text(
        "⏱ <b>Auto Delete Settings</b>\n\n"
        f"Current: <b>{current}</b>",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="🔙 Back",
                        callback_data="settings_back"
                    )
                ]
            ]
        )
    )


# =========================================================
# FSUB SETTINGS
# =========================================================

def fsub_settings_keyboard():

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="➕ Add FSub",
                    callback_data="fsub_add_info"
                )
            ],
            [
                InlineKeyboardButton(
                    text="➖ Remove FSub",
                    callback_data="fsub_remove_info"
                )
            ],
            [
                InlineKeyboardButton(
                    text="📋 Refresh List",
                    callback_data="settings_fsub"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🔙 Back",
                    callback_data="settings_back"
                )
            ]
        ]
    )


@router.callback_query(
    F.data == "settings_fsub"
)
async def settings_fsub(
    callback: CallbackQuery
):

    if not is_admin(
        callback.from_user.id
    ):
        return await callback.answer(
            "⛔ Admin only.",
            show_alert=True
        )

    fsubs = await get_fsub_channels()

    text = (
        "📢 <b>Force Subscription</b>\n\n"
        f"Channels: <b>{len(fsubs)}/4</b>\n\n"
    )

    if fsubs:
        for i, channel in enumerate(
            fsubs,
            start=1
        ):

            title = channel.get(
                "title",
                "Channel"
            )

            invite = channel.get(
                "invite_link"
            )

            if invite:
                text += (
                    f'{i}. <a href="{invite}">'
                    f"{title}</a>\n"
                )
            else:
                text += (
                    f"{i}. {title}\n"
                )
    else:
        text += "No FSub channels added.\n"

    text += (
        "\nBot ko channel me admin banana zaroori hai."
    )

    await callback.answer()

    await callback.message.edit_text(
        text,
        reply_markup=fsub_settings_keyboard()
    )


@router.callback_query(
    F.data == "fsub_add_info"
)
async def fsub_add_info(
    callback: CallbackQuery
):

    if not is_admin(
        callback.from_user.id
    ):
        return await callback.answer(
            "⛔ Admin only.",
            show_alert=True
        )

    await callback.answer()

    await callback.message.edit_text(
        "➕ <b>Add FSub Channel</b>\n\n"
        "Channel ID bhejo:\n\n"
        "<code>/addfsub -1001234567890</code>\n\n"
        "Maximum <b>4 channels</b>.",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="🔙 Back",
                        callback_data="settings_fsub"
                    )
                ]
            ]
        )
    )


@router.callback_query(
    F.data == "fsub_remove_info"
)
async def fsub_remove_info(
    callback: CallbackQuery
):

    if not is_admin(
        callback.from_user.id
    ):
        return await callback.answer(
            "⛔ Admin only.",
            show_alert=True
        )

    await callback.answer()

    await callback.message.edit_text(
        "➖ <b>Remove FSub Channel</b>\n\n"
        "Channel ID bhejo:\n\n"
        "<code>/delfsub -1001234567890</code>",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="🔙 Back",
                        callback_data="settings_fsub"
                    )
                ]
            ]
        )
    )


# =========================================================
# ADD FSUB
# =========================================================

@router.message(
    Command("addfsub")
)
async def addfsub(
    message: Message
):

    if not is_admin(
        message.from_user.id
    ):
        return await message.answer(
            "⛔ Admin only."
        )

    parts = (
        message.text or ""
    ).split()

    if len(parts) != 2:
        return await message.answer(
            "Usage:\n"
            "<code>/addfsub CHANNEL_ID</code>"
        )

    try:
        channel_id = int(parts[1])
    except ValueError:
        return await message.answer(
            "❌ Channel ID must be a number."
        )

    if channel_id >= 0:
        return await message.answer(
            "❌ Use the full private channel ID.\n\n"
            "Example:\n"
            "<code>-1001234567890</code>"
        )

    fsubs = await get_fsub_channels()

    if len(fsubs) >= 4:
        return await message.answer(
            "❌ Maximum 4 FSub channels allowed."
        )

    for channel in fsubs:
        if str(channel.get("chat_id")) == str(channel_id):
            return await message.answer(
                "❌ This channel is already added."
            )

    try:
        chat = await bot.get_chat(
            channel_id
        )
    except Exception:
        return await message.answer(
            "❌ I can't access this channel.\n\n"
            "Make sure the bot is admin in the channel."
        )

    try:
        bot_me = await bot.get_me()

        member = await bot.get_chat_member(
            chat_id=channel_id,
            user_id=bot_me.id
        )

        status_raw = getattr(
            member,
            "status",
            None
        )

        status = getattr(
            status_raw,
            "value",
            str(status_raw)
        )

        if str(status).lower() not in (
            "administrator",
            "creator"
        ):
            return await message.answer(
                "❌ Bot is not admin in this channel."
            )

    except Exception:
        return await message.answer(
            "❌ Couldn't verify bot admin status."
        )

    invite_link = None

    try:
        invite = await bot.create_chat_invite_link(
            chat_id=channel_id
        )

        invite_link = invite.invite_link

    except Exception:

        if chat.username:
            invite_link = (
                f"https://t.me/{chat.username}"
            )

    if not invite_link:
        return await message.answer(
            "❌ Couldn't create an invite link."
        )

    fsubs.append({
        "chat_id": channel_id,
        "title": chat.title or "Channel",
        "username": chat.username,
        "invite_link": invite_link
    })

    await save_fsub_channels(
        fsubs
    )

    await message.answer(
        "✅ <b>FSub channel added!</b>\n\n"
        f"📢 {escape_html(chat.title or 'Channel')}\n"
        f"🆔 <code>{channel_id}</code>"
    )


# =========================================================
# DELETE FSUB
# =========================================================

@router.message(
    Command("delfsub")
)
async def delfsub(
    message: Message
):

    if not is_admin(
        message.from_user.id
    ):
        return await message.answer(
            "⛔ Admin only."
        )

    parts = (
        message.text or ""
    ).split()

    if len(parts) != 2:
        return await message.answer(
            "Usage:\n"
            "<code>/delfsub CHANNEL_ID</code>"
        )

    try:
        channel_id = int(parts[1])
    except ValueError:
        return await message.answer(
            "❌ Invalid channel ID."
        )

    fsubs = await get_fsub_channels()

    new_list = [
        channel
        for channel in fsubs
        if str(channel.get("chat_id"))
        != str(channel_id)
    ]

    if len(new_list) == len(fsubs):
        return await message.answer(
            "❌ This channel is not in FSub."
        )

    await save_fsub_channels(
        new_list
    )

    await message.answer(
        "✅ FSub channel removed."
    )


# =========================================================
# SETTINGS ADMINS
# =========================================================

@router.callback_query(
    F.data == "settings_admins"
)
async def settings_admins(
    callback: CallbackQuery
):

    if not is_admin(
        callback.from_user.id
    ):
        return await callback.answer(
            "⛔ Admin only.",
            show_alert=True
        )

    admin_list = db.list_admins()

    text = (
        "👑 <b>Owner</b>\n"
        f"🆔 <code>{OWNER_ID}</code>\n\n"
        "🛡 <b>Admins</b>\n"
    )

    if admin_list:

        for uid in admin_list:

            name = await telegram_user_name(uid)

            text += (
                f"• {profile_link(uid, name)}\n"
                f"  🆔 <code>{uid}</code>\n"
            )

    else:
        text += "• None"

    await callback.answer()

    await callback.message.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="🔙 Back",
                        callback_data="settings_back"
                    )
                ]
            ]
        )
    )


# =========================================================
# SETTINGS MODS
# =========================================================

@router.callback_query(
    F.data == "settings_mods"
)
async def settings_mods(
    callback: CallbackQuery
):

    if not is_admin(
        callback.from_user.id
    ):
        return await callback.answer(
            "⛔ Admin only.",
            show_alert=True
        )

    await callback.answer()

    await callback.message.edit_text(
        "🛡 <b>Moderators</b>\n\n"
        "Moderator management is available "
        "through the admin controls.",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="🔙 Back",
                        callback_data="settings_back"
                    )
                ]
            ]
        )
    )


# =========================================================
# SETTINGS BACK
# =========================================================

@router.callback_query(
    F.data == "settings_back"
)
async def settings_back(
    callback: CallbackQuery
):

    if not is_admin(
        callback.from_user.id
    ):
        return await callback.answer(
            "⛔ Admin only.",
            show_alert=True
        )

    await callback.answer()

    await callback.message.edit_text(
        "⚙️ <b>Settings</b>\n\n"
        "<i>Please choose an option below.</i>",
        reply_markup=settings_keyboard()
    )


# =========================================================
# HELP
# =========================================================

def help_keyboard():

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📥 File Links",
                    callback_data="help_files"
                ),
                InlineKeyboardButton(
                    text="👑 My Plan",
                    callback_data="help_plan"
                )
            ],
            [
                InlineKeyboardButton(
                    text="ℹ️ About",
                    callback_data="about"
                ),
                InlineKeyboardButton(
                    text="❌ Close",
                    callback_data="close"
                )
            ]
        ]
    )


@router.message(
    Command("help")
)
async def help_command(
    message: Message
):

    ensure_user_from_message(message)

    await message.answer(
        "❓ <b>How can I help you?</b>\n\n"
        "<i>Choose an option below.</i>",
        reply_markup=help_keyboard()
    )


@router.callback_query(
    F.data == "help_files"
)
async def help_files(
    callback: CallbackQuery
):

    await callback.answer()

    await callback.message.edit_text(
        "📥 <b>File Links</b>\n\n"
        "Send the file link generated by the bot "
        "to get your requested file.",
        reply_markup=help_keyboard()
    )


@router.callback_query(
    F.data == "help_plan"
)
async def help_plan(
    callback: CallbackQuery
):

    await callback.answer()

    user_id = callback.from_user.id

    premium = db.get_premium(user_id)

    name = (
        callback.from_user.first_name
        or callback.from_user.username
        or "User"
    )

    if premium:

        remaining = format_remaining(
            premium["expires_at"]
        )

        text = (
            "👑 <b>YOUR PREMIUM MEMBERSHIP STATUS</b>\n\n"
            f"👤 Name: {escape_html(name)}\n"
            f"🆔 User ID: <code>{user_id}</code>\n"
            "💎 Plan Status: <b>Active</b>\n"
            f"⏳ Time Remaining: <b>{remaining}</b>"
        )

    else:

        text = (
            "👑 <b>YOUR PREMIUM MEMBERSHIP STATUS</b>\n\n"
            f"👤 Name: {escape_html(name)}\n"
            f"🆔 User ID: <code>{user_id}</code>\n"
            "💎 Plan Status: <b>Inactive</b>\n"
            "⏳ Time Remaining: <b>0 Days, 0 Hours</b>"
        )

    await callback.message.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="🔙 Back",
                        callback_data="help_back"
                    )
                ]
            ]
        )
    )


@router.callback_query(
    F.data == "help_back"
)
async def help_back(
    callback: CallbackQuery
):

    await callback.answer()

    await callback.message.edit_text(
        "❓ <b>How can I help you?</b>\n\n"
        "<i>Choose an option below.</i>",
        reply_markup=help_keyboard()
    )


# =========================================================
# BROADCAST
# =========================================================

@router.message(
    Command("broadcast")
)
async def broadcast(
    message: Message
):

    ensure_user_from_message(message)

    if not is_mod(
        message.from_user.id
    ):
        return await message.answer(
            "⛔ Moderator/Admin only."
        )

    if not message.reply_to_message:
        return await message.answer(
            "Reply to the message you want to "
            "broadcast, then use /broadcast."
        )

    users_list = db.list_users()

    sent = 0
    failed = 0

    delete_minutes = int(
        await get_setting(
            "auto_delete_minutes",
            5
        )
    )

    for uid in users_list:

        if db.is_banned(uid):
            continue

        try:

            copied = await bot.copy_message(
                chat_id=uid,
                from_chat_id=message.chat.id,
                message_id=message.reply_to_message.message_id
            )

            sent += 1

            if delete_minutes > 0:
                schedule_delete(
                    uid,
                    copied.message_id,
                    delete_minutes * 60
                )

            await asyncio.sleep(
                0.035
            )

        except Exception as e:

            failed += 1

            log.warning(
                "Broadcast failed to %s: %s",
                uid,
                e
            )

    await message.answer(
        "📢 <b>Broadcast complete</b>\n\n"
        f"✅ Sent: {sent}\n"
        f"❌ Failed: {failed}"
    )


# =========================================================
# REQUEST
# =========================================================

@router.message(
    Command("request")
)
async def request_command(
    message: Message
):

    ensure_user_from_message(message)

    parts = (
        message.text or ""
    ).split(
        maxsplit=1
    )

    if len(parts) != 2:
        return await message.answer(
            "Usage:\n"
            "<code>/request your request</code>"
        )

    request_id = db.add_request(
        message.from_user.id,
        parts[1]
    )

    await message.answer(
        "✅ <b>Request submitted.</b>\n\n"
        f"🆔 Request ID: <code>{request_id}</code>"
    )


# =========================================================
# STATS
# =========================================================

@router.message(
    Command("stats")
)
async def stats(
    message: Message
):

    if not is_admin(
        message.from_user.id
    ):
        return await message.answer(
            "⛔ Admin only."
        )

    data = db.stats()

    await message.answer(
        "📊 <b>Bot Statistics</b>\n\n"
        f"👥 Users: <b>{data['users']}</b>\n"
        f"📁 Files: <b>{data['files']}</b>\n"
        f"📦 Batches: <b>{data['batches']}</b>\n"
        f"👑 Premium: <b>{data['premium']}</b>\n"
        f"🛡 Admins: <b>{data['admins']}</b>\n"
        f"🚫 Banned: <b>{data['banned']}</b>"
    )


# =========================================================
# CLONE
# =========================================================

@router.message(
    Command("clone")
)
async def clone_info(
    message: Message
):

    if not is_owner(
        message.from_user.id
    ):
        return await message.answer(
            "⛔ Owner only."
        )

    await message.answer(
        "🔁 <b>Clone System</b>\n\n"
        "All clones can use the same Supabase database.\n\n"
        "<b>Clone-specific:</b>\n"
        "• Start Image\n"
        "• Force Subscribe Channels\n\n"
        "<b>Shared:</b>\n"
        "• Users\n"
        "• Admins\n"
        "• Premium\n"
        "• Files\n"
        "• Batches\n"
        "• Auto Delete\n"
        "• Broadcast data"
    )


# =========================================================
# ERROR HANDLER
# =========================================================

@router.errors()
async def error_handler(
    event
):

    log.exception(
        "Telegram handler error: %s",
        event.exception
    )


# =========================================================
# STARTUP
# =========================================================

async def main():

    global BOT_USERNAME

    try:

        me = await bot.get_me()

        BOT_USERNAME = (
            me.username
            or BOT_USERNAME
        )

        log.info(
            "Bot started as @%s",
            BOT_USERNAME
        )

    except Exception as e:

        log.warning(
            "Could not get bot info: %s",
            e
        )

    await bot.delete_webhook(
        drop_pending_updates=False
    )

    config = uvicorn.Config(
        app,
        host="0.0.0.0",
        port=PORT,
        log_level="info"
    )

    server = uvicorn.Server(
        config
    )

    await asyncio.gather(
        dp.start_polling(bot),
        server.serve()
    )


# =========================================================
# RUN
# =========================================================

if __name__ == "__main__":
    asyncio.run(main())
