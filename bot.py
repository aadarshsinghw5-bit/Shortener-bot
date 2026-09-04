import asyncio
import html
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
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from fastapi import FastAPI
from motor.motor_asyncio import AsyncIOMotorClient
import uvicorn


# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(name)s - %(message)s"
)

log = logging.getLogger("file-store-bot")


# ============================================================
# ENVIRONMENT VARIABLES
# ============================================================

BOT_TOKEN = os.environ["BOT_TOKEN"]
MONGO_URI = os.environ["MONGO_URI"]

OWNER_ID = int(os.environ["OWNER_ID"])

PORT = int(os.getenv("PORT", "10000"))

BOT_USERNAME = os.getenv("BOT_USERNAME", "").lstrip("@")

# Start image URL
START_IMAGE_URL = os.getenv("START_IMAGE_URL", "").strip()

# Optional owner username
OWNER_USERNAME = os.getenv("OWNER_USERNAME", "Its_Lozo").lstrip("@")

# Database name
MONGO_DB = os.getenv("MONGO_DB", "file_store_bot")


# ============================================================
# MONGO
# ============================================================

mongo = AsyncIOMotorClient(MONGO_URI)

db = mongo[MONGO_DB]

users = db.users
admins = db.admins
mods = db.mods
channels = db.channels
posts = db.posts
batches = db.batches
broadcasts = db.broadcasts
settings = db.settings
fsub_channels = db.fsub_channels


# ============================================================
# BOT
# ============================================================

bot = Bot(
    BOT_TOKEN,
    default=DefaultBotProperties(
        parse_mode=ParseMode.HTML
    )
)

dp = Dispatcher()
router = Router()

dp.include_router(router)


# ============================================================
# FASTAPI HEALTH SERVER
# ============================================================

app = FastAPI()


@app.api_route("/", methods=["GET", "HEAD"])
async def health():
    return {
        "ok": True,
        "service": "telegram-file-store-bot"
    }


@app.api_route("/health", methods=["GET", "HEAD"])
async def health2():
    return {
        "ok": True
    }


# ============================================================
# HELPERS
# ============================================================

def now():
    return datetime.now(timezone.utc)


def is_owner(user_id: int) -> bool:
    return user_id == OWNER_ID


async def is_admin(user_id: int) -> bool:
    if is_owner(user_id):
        return True

    return (
        await admins.find_one(
            {"user_id": user_id}
        )
        is not None
    )


async def is_mod(user_id: int) -> bool:
    if await is_admin(user_id):
        return True

    return (
        await mods.find_one(
            {"user_id": user_id}
        )
        is not None
    )


def escape(text):
    if text is None:
        return ""

    return html.escape(str(text))


async def ensure_user(message: Message):
    if not message.from_user:
        return

    await users.update_one(
        {"user_id": message.from_user.id},
        {
            "$set": {
                "user_id": message.from_user.id,
                "username": message.from_user.username,
                "first_name": message.from_user.first_name,
                "last_name": message.from_user.last_name,
                "last_seen": now(),
            }
        },
        upsert=True,
    )


# ============================================================
# AUTO DELETE SETTINGS
# ============================================================

async def get_auto_delete_seconds():
    data = await settings.find_one(
        {"_id": "global"}
    )

    if not data:
        return 300

    return int(
        data.get(
            "auto_delete_seconds",
            300
        )
    )


async def set_auto_delete_seconds(seconds: int):
    await settings.update_one(
        {"_id": "global"},
        {
            "$set": {
                "auto_delete_seconds": seconds
            }
        },
        upsert=True,
    )


def format_duration(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds} seconds"

    minutes = seconds // 60

    if minutes < 60:
        return f"{minutes} minute" if minutes == 1 else f"{minutes} minutes"

    hours = minutes // 60

    if hours < 24:
        return f"{hours} hour" if hours == 1 else f"{hours} hours"

    days = hours // 24

    return f"{days} day" if days == 1 else f"{days} days"


# ============================================================
# MEDIA / MESSAGE TYPE
# ============================================================

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


# ============================================================
# TELEGRAM POST LINK PARSER
# ============================================================

def parse_link(link: str):
    link = link.strip()

    # Private channel
    m = re.match(
        r"https?://t\.me/c/(\d+)/(\d+)",
        link
    )

    if m:
        channel_id = int("-100" + m.group(1))
        message_id = int(m.group(2))

        return channel_id, message_id

    # Public channel
    m = re.match(
        r"https?://t\.me/([A-Za-z0-9_]+)/(\d+)",
        link
    )

    if m:
        username = "@" + m.group(1)
        message_id = int(m.group(2))

        return username, message_id

    return None


# ============================================================
# BOT USERNAME
# ============================================================

async def get_bot_username():
    if BOT_USERNAME:
        return BOT_USERNAME

    me = await bot.get_me()

    return me.username


# ============================================================
# PROFILE LINK
# ============================================================

def profile_link(user_id: int, name: str):
    return (
        f'<a href="tg://user?id={user_id}">'
        f'{escape(name)}'
        f'</a>'
    )


async def get_user_display(user_id: int):
    try:
        chat = await bot.get_chat(user_id)

        name = chat.full_name or chat.username or str(user_id)

        return profile_link(
            user_id,
            name
        )

    except Exception:
        data = await users.find_one(
            {"user_id": user_id}
        )

        if data:
            name = (
                data.get("first_name")
                or data.get("username")
                or str(user_id)
            )

            return profile_link(
                user_id,
                name
            )

        return profile_link(
            user_id,
            str(user_id)
        )


# ============================================================
# START MENU
# ============================================================

def start_keyboard():

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="HELP",
                    callback_data="help"
                ),
                InlineKeyboardButton(
                    text="CLOSE",
                    callback_data="close"
                ),
            ]
        ]
    )


async def send_start(message: Message):

    owner_link = (
        f'<a href="https://t.me/{OWNER_USERNAME}">'
        f'@{OWNER_USERNAME}'
        f'</a>'
    )

    text = (
        "Hi There...! 💥\n\n"
        "I am a file-store bot.\n"
        "I can generate links directly with no problems.\n\n"
        f"My Owner: {owner_link}"
    )

    if START_IMAGE_URL:

        try:
            return await message.answer_photo(
                photo=START_IMAGE_URL,
                caption=text,
                reply_markup=start_keyboard(),
            )

        except Exception as e:
            log.warning(
                "Start image failed: %s",
                e
            )

    return await message.answer(
        text,
        reply_markup=start_keyboard()
    )


# ============================================================
# HELP MENU
# ============================================================

def help_keyboard():

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
                ),
            ]
        ]
    )


async def help_text():

    owner_link = (
        f'<a href="https://t.me/{OWNER_USERNAME}">'
        f'@{OWNER_USERNAME}'
        f'</a>'
    )

    return (
        "<b>About Us..</b>\n\n"
        "➤ <i>Made for : "
        '<a href="https://t.me/Anime_Hub_94">'
        "Anime Hub"
        "</a></i>\n"
        f"➤ <i>Owner : {owner_link}</i>\n"
        f"➤ <i>Developer : {owner_link}</i>\n\n"
        "Adios !!"
    )


# ============================================================
# F-SUB
# ============================================================

async def get_fsub_channels():
    return await fsub_channels.find(
        {}
    ).sort(
        "created_at",
        1
    ).to_list(
        length=4
    )


async def check_fsub(user_id: int):

    channels_list = await get_fsub_channels()

    if not channels_list:
        return True, []

    statuses = []

    for channel in channels_list:

        chat_id = channel["chat_id"]

        joined = False

        try:

            member = await bot.get_chat_member(
                chat_id,
                user_id
            )

            if member.status in (
                "creator",
                "administrator",
                "member"
            ):
                joined = True

            elif member.status == "restricted" and getattr(
                member,
                "is_member",
                False
            ):
                joined = True

        except Exception as e:
            log.warning(
                "FSub check failed for %s: %s",
                chat_id,
                e
            )

        statuses.append(
            {
                "data": channel,
                "joined": joined
            }
        )

    return (
        all(
            x["joined"]
            for x in statuses
        ),
        statuses
    )


async def fsub_message(
    message: Message,
    statuses
):

    lines = [
        "Hello There..!⚡",
        "",
        "🔘Please join all of our channels first then",
        "try again...!",
        "",
        "<b>Channel Subscription Status:</b>",
        "",
    ]

    keyboard = []

    number = 1

    for item in statuses:

        channel = item["data"]
        joined = item["joined"]

        title = channel.get(
            "title",
            "Channel"
        )

        if joined:

            lines.append(
                f"{number}. {escape(title)} - "
                "<b>Joined</b> ✅"
            )

        else:

            lines.append(
                f"{number}. {escape(title)} - "
                "<b>Not Joined</b> ❌"
            )

            invite = channel.get(
                "invite_link"
            )

            if invite:
                keyboard.append(
                    [
                        InlineKeyboardButton(
                            text=title[:30],
                            url=invite
                        )
                    ]
                )

        number += 1

    keyboard.append(
        [
            InlineKeyboardButton(
                text="Try Again",
                callback_data="fsub_check"
            )
        ]
    )

    keyboard.append(
        [
            InlineKeyboardButton(
                text="CLOSE",
                callback_data="close"
            )
        ]
    )

    return await message.answer(
        "\n".join(lines),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=keyboard
        )
    )


# ============================================================
# F-SUB CHECK BEFORE CONTENT
# ============================================================

async def require_fsub(message: Message):

    complete, statuses = await check_fsub(
        message.from_user.id
    )

    if complete:
        return True

    await fsub_message(
        message,
        statuses
    )

    return False


# ============================================================
# START
# ============================================================

@router.message(CommandStart())
async def start(message: Message):

    await ensure_user(message)

    # Check banned
    user = await users.find_one(
        {"user_id": message.from_user.id}
    )

    if user and user.get("banned"):
        return await message.answer(
            "🚫 You are banned from using this bot."
        )

    # FSub first
    complete, statuses = await check_fsub(
        message.from_user.id
    )

    if not complete:

        args = (
            message.text or ""
        ).split(maxsplit=1)

        # Show FSub instead of delivering content
        await fsub_message(
            message,
            statuses
        )

        return

    args = (
        message.text or ""
    ).split(
        maxsplit=1
    )

    # ========================================================
    # BATCH LINK
    # ========================================================

    if (
        len(args) == 2
        and args[1].startswith("batch_")
    ):

        batch_id = args[1][6:]

        batch = await batches.find_one(
            {"_id": batch_id}
        )

        if not batch:
            return await message.answer(
                "❌ Batch not found or expired."
            )

        wait_message = await message.answer(
            "Please Wait....."
        )

        sent_messages = []

        for item in batch.get(
            "items",
            []
        ):

            try:

                copied = await bot.copy_message(
                    chat_id=message.chat.id,
                    from_chat_id=item["chat_id"],
                    message_id=item["message_id"]
                )

                sent_messages.append(
                    copied
                )

                await asyncio.sleep(
                    0.03
                )

            except Exception as e:

                log.warning(
                    "Batch copy failed %s/%s: %s",
                    item["chat_id"],
                    item["message_id"],
                    e
                )

        try:
            await wait_message.delete()
        except Exception:
            pass

        if not sent_messages:
            return await message.answer(
                "❌ Could not send the files."
            )

        # Auto delete
        delete_seconds = await get_auto_delete_seconds()

        duration = format_duration(
            delete_seconds
        )

        notice = await message.answer(
            f"This File is deleting automatically "
            f"in {duration}. "
            f"Forward in your Saved Messages..!"
        )

        async def delete_later():

            await asyncio.sleep(
                delete_seconds
            )

            for sent in sent_messages:

                try:
                    await bot.delete_message(
                        message.chat.id,
                        sent.message_id
                    )

                except Exception:
                    pass

            try:
                await notice.delete()
            except Exception:
                pass

        asyncio.create_task(
            delete_later()
        )

        return

    # Normal start
    await send_start(message)


# ============================================================
# GENLINK
# ============================================================

@router.message(Command("genlink"))
async def genlink(message: Message):

    await ensure_user(message)

    # ONLY Owner/Admin/Moderator
    if not await is_mod(
        message.from_user.id
    ):
        return await message.answer(
            "⛔ Only Owner/Admin/Moderator can use /genlink."
        )

    # Must reply to ANY message
    if not message.reply_to_message:

        return await message.answer(
            "Reply to any message/file and use /genlink."
        )

    source = message.reply_to_message

    # Accept any copyable Telegram message
    kind = media_kind(source)

    if not kind:

        return await message.answer(
            "❌ This message type cannot be stored."
        )

    bid = uuid.uuid4().hex[:16]

    item = {
        "chat_id": source.chat.id,
        "message_id": source.message_id,
    }

    await batches.insert_one(
        {
            "_id": bid,
            "items": [item],
            "created_by": message.from_user.id,
            "created_at": now(),
            "type": "single",
        }
    )

    username = await get_bot_username()

    link = (
        f"https://t.me/{username}"
        f"?start=batch_{bid}"
    )

    await message.answer(
        "🔗 <b>Link created</b>\n\n"
        f"{link}"
    )


# ============================================================
# CHANNEL POSTS MEMORY
# ============================================================

@router.channel_post()
async def remember_channel_post(
    message: Message
):

    kind = media_kind(
        message
    )

    if not kind:
        return

    await posts.update_one(
        {
            "chat_id": message.chat.id,
            "message_id": message.message_id,
        },
        {
            "$set": {
                "chat_id": message.chat.id,
                "message_id": message.message_id,
                "kind": kind,
                "has_media": bool(
                    message.photo
                    or message.video
                    or message.document
                    or message.audio
                    or message.voice
                    or message.animation
                    or message.sticker
                ),
                "created_at": message.date,
                "caption": message.caption,
                "text": message.text,
            }
        },
        upsert=True
    )

    await channels.update_one(
        {
            "chat_id": message.chat.id
        },
        {
            "$set": {
                "chat_id": message.chat.id,
                "title": message.chat.title,
                "username": message.chat.username,
            }
        },
        upsert=True
    )


# ============================================================
# BATCH
# ============================================================

@router.message(Command("batch"))
async def batch(message: Message):

    await ensure_user(message)

    if not await is_mod(
        message.from_user.id
    ):
        return await message.answer(
            "⛔ You don't have permission."
        )

    parts = (
        message.text or ""
    ).split()

    if len(parts) != 3:

        return await message.answer(
            "Usage:\n"
            "/batch LINK1 LINK2"
        )

    a = parse_link(
        parts[1]
    )

    b = parse_link(
        parts[2]
    )

    if not a or not b:

        return await message.answer(
            "❌ Invalid Telegram post link."
        )

    if a[0] != b[0]:

        return await message.answer(
            "❌ Both links must be from the same channel."
        )

    lo, hi = sorted(
        [
            a[1],
            b[1]
        ]
    )

    docs = await posts.find(
        {
            "chat_id": a[0],
            "message_id": {
                "$gte": lo,
                "$lte": hi
            }
        }
    ).sort(
        "message_id",
        1
    ).to_list(
        length=None
    )

    if not docs:

        return await message.answer(
            "❌ I don't have these channel posts recorded yet.\n\n"
            "Make sure the bot is admin in the channel "
            "and the posts were published after the bot was added."
        )

    items = []

    for d in docs:

        # Only text/media messages
        if not d.get("kind"):
            continue

        items.append(
            {
                "chat_id": d["chat_id"],
                "message_id": d["message_id"],
            }
        )

    if not items:

        return await message.answer(
            "❌ No supported text/media posts found "
            "between these two links."
        )

    bid = uuid.uuid4().hex[:16]

    await batches.insert_one(
        {
            "_id": bid,
            "items": items,
            "created_by": message.from_user.id,
            "created_at": now(),
            "type": "batch",
        }
    )

    username = await get_bot_username()

    link = (
        f"https://t.me/{username}"
        f"?start=batch_{bid}"
    )

    await message.answer(
        "✅ <b>Batch created</b>\n\n"
        f"📦 Items: {len(items)}\n"
        f"🔗 {link}"
    )


# ============================================================
# ADMINS
# ============================================================

@router.message(Command("admins"))
async def admins_list(message: Message):

    await ensure_user(message)

    if not await is_mod(
        message.from_user.id
    ):
        return await message.answer(
            "⛔ Moderator/Admin only."
        )

    owner_name = await get_user_display(
        OWNER_ID
    )

    text = (
        "👑 <b>Owner</b>\n"
        f"{owner_name}\n"
        f"🆔 <code>{OWNER_ID}</code>\n\n"
        "🛡 <b>Admins</b>\n"
    )

    admin_list = await admins.find(
        {}
    ).to_list(
        length=None
    )

    if not admin_list:

        text += "• None\n"

    else:

        for admin in admin_list:

            uid = admin["user_id"]

            name = await get_user_display(
                uid
            )

            text += (
                f"• {name}\n"
                f"  🆔 <code>{uid}</code>\n"
            )

    await message.answer(
        text
    )


# ============================================================
# MODERATORS
# ============================================================

@router.message(Command("mods"))
async def mods_list(message: Message):

    await ensure_user(message)

    if not await is_mod(
        message.from_user.id
    ):
        return await message.answer(
            "⛔ Moderator/Admin only."
        )

    text = (
        "🛡 <b>Moderators</b>\n\n"
    )

    mod_list = await mods.find(
        {}
    ).to_list(
        length=None
    )

    if not mod_list:

        text += "• None"

    else:

        for mod in mod_list:

            uid = mod["user_id"]

            name = await get_user_display(
                uid
            )

            text += (
                f"• {name}\n"
                f"  🆔 <code>{uid}</code>\n"
            )

    await message.answer(
        text
    )


# ============================================================
# ADD ADMIN
# ============================================================

@router.message(Command("addadmin"))
async def addadmin(message: Message):

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
            "/addadmin USER_ID"
        )

    uid = int(
        parts[1]
    )

    await admins.update_one(
        {"user_id": uid},
        {
            "$set": {
                "user_id": uid
            }
        },
        upsert=True
    )

    await message.answer(
        f"✅ Admin added.\n"
        f"🆔 <code>{uid}</code>"
    )


# ============================================================
# DELETE ADMIN
# ============================================================

@router.message(Command("deladmin"))
async def deladmin(message: Message):

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
            "/deladmin USER_ID"
        )

    uid = int(
        parts[1]
    )

    await admins.delete_one(
        {
            "user_id": uid
        }
    )

    await message.answer(
        "✅ Admin removed."
    )


# ============================================================
# ADD MOD
# ============================================================

@router.message(Command("addmod"))
async def addmod(message: Message):

    if not await is_admin(
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
            "/addmod USER_ID"
        )

    uid = int(
        parts[1]
    )

    await mods.update_one(
        {"user_id": uid},
        {
            "$set": {
                "user_id": uid
            }
        },
        upsert=True
    )

    await message.answer(
        f"✅ Moderator added.\n"
        f"🆔 <code>{uid}</code>"
    )


# ============================================================
# DELETE MOD
# ============================================================

@router.message(Command("delmod"))
async def delmod(message: Message):

    if not await is_admin(
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
            "/delmod USER_ID"
        )

    uid = int(
        parts[1]
    )

    await mods.delete_one(
        {
            "user_id": uid
        }
    )

    await message.answer(
        "✅ Moderator removed."
    )


# ============================================================
# BAN
# ============================================================

@router.message(Command("ban"))
async def ban(message: Message):

    if not await is_mod(
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
            "/ban USER_ID"
        )

    uid = int(
        parts[1]
    )

    await users.update_one(
        {"user_id": uid},
        {
            "$set": {
                "banned": True
            }
        },
        upsert=True
    )

    await message.answer(
        "🚫 User banned."
    )


# ============================================================
# UNBAN
# ============================================================

@router.message(Command("unban"))
async def unban(message: Message):

    if not await is_mod(
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
            "/unban USER_ID"
        )

    uid = int(
        parts[1]
    )

    await users.update_one(
        {"user_id": uid},
        {
            "$set": {
                "banned": False
            }
        },
        upsert=True
    )

    await message.answer(
        "✅ User unbanned."
    )


# ============================================================
# SETTINGS MENU
# ============================================================

def settings_keyboard():

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🗑 Auto Delete",
                    callback_data="settings_autodelete"
                )
            ],
            [
                InlineKeyboardButton(
                    text="📢 FSub Channels",
                    callback_data="settings_fsub"
                )
            ],
            [
                InlineKeyboardButton(
                    text="👑 Admins",
                    callback_data="settings_admins"
                ),
                InlineKeyboardButton(
                    text="🛡 Moderators",
                    callback_data="settings_mods"
                )
            ],
            [
                InlineKeyboardButton(
                    text="CLOSE",
                    callback_data="close"
                )
            ]
        ]
    )


@router.message(Command("settings"))
async def settings_command(message: Message):

    if not await is_admin(
        message.from_user.id
    ):
        return await message.answer(
            "⛔ Admin only."
        )

    seconds = await get_auto_delete_seconds()

    text = (
        "⚙️ <b>Bot Settings</b>\n\n"
        f"🗑 Auto Delete: "
        f"<b>{format_duration(seconds)}</b>\n"
        f"📢 FSub: <b>Maximum 4 Channels</b>"
    )

    await message.answer(
        text,
        reply_markup=settings_keyboard()
    )


# ============================================================
# SET AUTO DELETE
# ============================================================

@router.callback_query(
    F.data == "settings_autodelete"
)
async def settings_autodelete(
    callback: CallbackQuery
):

    if not await is_admin(
        callback.from_user.id
    ):
        return await callback.answer(
            "Admin only.",
            show_alert=True
        )

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="1 Minute",
                    callback_data="ad_60"
                ),
                InlineKeyboardButton(
                    text="5 Minutes",
                    callback_data="ad_300"
                )
            ],
            [
                InlineKeyboardButton(
                    text="10 Minutes",
                    callback_data="ad_600"
                ),
                InlineKeyboardButton(
                    text="30 Minutes",
                    callback_data="ad_1800"
                )
            ],
            [
                InlineKeyboardButton(
                    text="1 Hour",
                    callback_data="ad_3600"
                )
            ],
            [
                InlineKeyboardButton(
                    text="BACK",
                    callback_data="settings_back"
                )
            ]
        ]
    )

    await callback.message.edit_text(
        "🗑 <b>Auto Delete Settings</b>\n\n"
        "Select how long delivered files should remain:",
        reply_markup=keyboard
    )

    await callback.answer()


@router.callback_query(
    F.data.startswith("ad_")
)
async def set_auto_delete_callback(
    callback: CallbackQuery
):

    if not await is_admin(
        callback.from_user.id
    ):
        return await callback.answer(
            "Admin only.",
            show_alert=True
        )

    seconds = int(
        callback.data.split("_")[1]
    )

    await set_auto_delete_seconds(
        seconds
    )

    await callback.message.edit_text(
        "✅ <b>Auto Delete Updated</b>\n\n"
        f"Files will now be deleted after "
        f"<b>{format_duration(seconds)}</b>."
    )

    await callback.answer(
        "Auto delete updated."
    )


# ============================================================
# FSUB SETTINGS
# ============================================================

@router.callback_query(
    F.data == "settings_fsub"
)
async def settings_fsub(
    callback: CallbackQuery
):

    if not await is_admin(
        callback.from_user.id
    ):
        return await callback.answer(
            "Admin only.",
            show_alert=True
        )

    channels_list = await get_fsub_channels()

    text = (
        "📢 <b>FSub Channels</b>\n\n"
    )

    keyboard = []

    if not channels_list:

        text += "No FSub channels added.\n"

    else:

        for i, channel in enumerate(
            channels_list,
            1
        ):

            title = channel.get(
                "title",
                "Unknown"
            )

            invite = channel.get(
                "invite_link"
            )

            if invite:

                text += (
                    f"{i}. <a href=\"{escape(invite)}\">"
                    f"{escape(title)}"
                    f"</a>\n"
                )

            else:

                text += (
                    f"{i}. {escape(title)}\n"
                )

            keyboard.append(
                [
                    InlineKeyboardButton(
                        text=f"❌ Remove {title[:20]}",
                        callback_data=f"fsub_remove_{channel['chat_id']}"
                    )
                ]
            )

    if len(channels_list) < 4:

        keyboard.append(
            [
                InlineKeyboardButton(
                    text="➕ Add Channel",
                    callback_data="fsub_add"
                )
            ]
        )

    keyboard.append(
        [
            InlineKeyboardButton(
                text="BACK",
                callback_data="settings_back"
            )
        ]
    )

    await callback.message.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=keyboard
        ),
        disable_web_page_preview=True
    )

    await callback.answer()


# ============================================================
# FSM-LIKE PENDING ACTION
# ============================================================

pending_actions = {}


# ============================================================
# ADD FSUB
# ============================================================

@router.callback_query(
    F.data == "fsub_add"
)
async def fsub_add(
    callback: CallbackQuery
):

    if not await is_admin(
        callback.from_user.id
    ):
        return await callback.answer(
            "Admin only.",
            show_alert=True
        )

    channels_list = await get_fsub_channels()

    if len(channels_list) >= 4:

        return await callback.answer(
            "Maximum 4 FSub channels allowed.",
            show_alert=True
        )

    pending_actions[
        callback.from_user.id
    ] = "add_fsub"

    await callback.message.edit_text(
        "➕ <b>Add FSub Channel</b>\n\n"
        "Send the <b>Channel ID</b> now.\n\n"
        "Example:\n"
        "<code>-1001234567890</code>\n\n"
        "Make sure the bot is admin in that channel."
    )

    await callback.answer()


# ============================================================
# REMOVE FSUB
# ============================================================

@router.callback_query(
    F.data.startswith("fsub_remove_")
)
async def fsub_remove(
    callback: CallbackQuery
):

    if not await is_admin(
        callback.from_user.id
    ):
        return await callback.answer(
            "Admin only.",
            show_alert=True
        )

    raw_id = callback.data.replace(
        "fsub_remove_",
        "",
        1
    )

    try:
        chat_id = int(raw_id)
    except Exception:
        return await callback.answer(
            "Invalid channel.",
            show_alert=True
        )

    await fsub_channels.delete_one(
        {
            "chat_id": chat_id
        }
    )

    await callback.answer(
        "Channel removed."
    )

    # Refresh settings
    channels_list = await get_fsub_channels()

    text = (
        "📢 <b>FSub Channels</b>\n\n"
    )

    keyboard = []

    for i, channel in enumerate(
        channels_list,
        1
    ):

        title = channel.get(
            "title",
            "Unknown"
        )

        invite = channel.get(
            "invite_link"
        )

        if invite:

            text += (
                f"{i}. <a href=\"{escape(invite)}\">"
                f"{escape(title)}"
                f"</a>\n"
            )

        else:

            text += (
                f"{i}. {escape(title)}\n"
            )

        keyboard.append(
            [
                InlineKeyboardButton(
                    text=f"❌ Remove {title[:20]}",
                    callback_data=f"fsub_remove_{channel['chat_id']}"
                )
            ]
        )

    if len(channels_list) < 4:

        keyboard.append(
            [
                InlineKeyboardButton(
                    text="➕ Add Channel",
                    callback_data="fsub_add"
                )
            ]
        )

    keyboard.append(
        [
            InlineKeyboardButton(
                text="BACK",
                callback_data="settings_back"
            )
        ]
    )

    await callback.message.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=keyboard
        )
    )


# ============================================================
# HANDLE FSUB CHANNEL ID
# ============================================================

@router.message(
    F.text
)
async def handle_pending_text(
    message: Message
):

    user_id = message.from_user.id

    action = pending_actions.get(
        user_id
    )

    if action != "add_fsub":
        return

    if not await is_admin(
        user_id
    ):
        pending_actions.pop(
            user_id,
            None
        )

        return

    if not message.text.strip().lstrip("-").isdigit():

        return await message.answer(
            "❌ Invalid Channel ID.\n\n"
            "Example:\n"
            "<code>-1001234567890</code>"
        )

    chat_id = int(
        message.text.strip()
    )

    channels_list = await get_fsub_channels()

    if len(channels_list) >= 4:

        pending_actions.pop(
            user_id,
            None
        )

        return await message.answer(
            "❌ Maximum 4 FSub channels allowed."
        )

    # Check channel
    try:

        chat = await bot.get_chat(
            chat_id
        )

    except Exception as e:

        log.warning(
            "Could not get FSub channel: %s",
            e
        )

        return await message.answer(
            "❌ Could not access this channel.\n\n"
            "Make sure:\n"
            "• Channel ID is correct\n"
            "• Bot is admin in the channel"
        )

    title = chat.title or "Channel"

    # Invite link
    invite_link = None

    if chat.username:

        invite_link = (
            f"https://t.me/{chat.username}"
        )

    else:

        try:

            invite = await bot.create_chat_invite_link(
                chat_id=chat_id
            )

            invite_link = invite.invite_link

        except Exception as e:

            log.warning(
                "Invite creation failed: %s",
                e
            )

    await fsub_channels.update_one(
        {
            "chat_id": chat_id
        },
        {
            "$set": {
                "chat_id": chat_id,
                "title": title,
                "username": chat.username,
                "invite_link": invite_link,
                "created_at": now(),
            }
        },
        upsert=True
    )

    pending_actions.pop(
        user_id,
        None
    )

    if invite_link:

        await message.answer(
            "✅ <b>FSub Channel Added</b>\n\n"
            f"📢 <a href=\"{escape(invite_link)}\">"
            f"{escape(title)}"
            f"</a>\n"
            f"🆔 <code>{chat_id}</code>"
        )

    else:

        await message.answer(
            "✅ <b>FSub Channel Added</b>\n\n"
            f"📢 {escape(title)}\n"
            f"🆔 <code>{chat_id}</code>\n\n"
            "⚠️ Invite link could not be created."
        )


# ============================================================
# SETTINGS ADMINS
# ============================================================

@router.callback_query(
    F.data == "settings_admins"
)
async def settings_admins(
    callback: CallbackQuery
):

    if not await is_admin(
        callback.from_user.id
    ):
        return await callback.answer(
            "Admin only.",
            show_alert=True
        )

    owner_name = await get_user_display(
        OWNER_ID
    )

    text = (
        "👑 <b>Owner</b>\n"
        f"{owner_name}\n"
        f"🆔 <code>{OWNER_ID}</code>\n\n"
        "🛡 <b>Admins</b>\n"
    )

    admin_list = await admins.find(
        {}
    ).to_list(
        length=None
    )

    if not admin_list:

        text += "• None\n"

    else:

        for admin in admin_list:

            uid = admin["user_id"]

            name = await get_user_display(
                uid
            )

            text += (
                f"• {name}\n"
                f"  🆔 <code>{uid}</code>\n"
            )

    await callback.message.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="BACK",
                        callback_data="settings_back"
                    )
                ]
            ]
        )
    )

    await callback.answer()


# ============================================================
# SETTINGS MODS
# ============================================================

@router.callback_query(
    F.data == "settings_mods"
)
async def settings_mods(
    callback: CallbackQuery
):

    if not await is_admin(
        callback.from_user.id
    ):
        return await callback.answer(
            "Admin only.",
            show_alert=True
        )

    text = "🛡 <b>Moderators</b>\n\n"

    mod_list = await mods.find(
        {}
    ).to_list(
        length=None
    )

    if not mod_list:

        text += "• None"

    else:

        for mod in mod_list:

            uid = mod["user_id"]

            name = await get_user_display(
                uid
            )

            text += (
                f"• {name}\n"
                f"  🆔 <code>{uid}</code>\n"
            )

    await callback.message.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="BACK",
                        callback_data="settings_back"
                    )
                ]
            ]
        )
    )

    await callback.answer()


# ============================================================
# SETTINGS BACK
# ============================================================

@router.callback_query(
    F.data == "settings_back"
)
async def settings_back(
    callback: CallbackQuery
):

    if not await is_admin(
        callback.from_user.id
    ):
        return await callback.answer(
            "Admin only.",
            show_alert=True
        )

    seconds = await get_auto_delete_seconds()

    text = (
        "⚙️ <b>Bot Settings</b>\n\n"
        f"🗑 Auto Delete: "
        f"<b>{format_duration(seconds)}</b>\n"
        f"📢 FSub: <b>Maximum 4 Channels</b>"
    )

    await callback.message.edit_text(
        text,
        reply_markup=settings_keyboard()
    )

    await callback.answer()


# ============================================================
# HELP CALLBACK
# ============================================================

@router.callback_query(
    F.data == "help"
)
async def help_callback(
    callback: CallbackQuery
):

    text = await help_text()

    await callback.message.edit_text(
        text,
        reply_markup=help_keyboard()
    )

    await callback.answer()


# ============================================================
# BACK TO START
# ============================================================

@router.callback_query(
    F.data == "back_start"
)
async def back_start(
    callback: CallbackQuery
):

    await callback.message.delete()

    await send_start(
        callback.message
    )

    await callback.answer()


# ============================================================
# CLOSE
# ============================================================

@router.callback_query(
    F.data == "close"
)
async def close_callback(
    callback: CallbackQuery
):

    try:

        await callback.message.delete()

    except Exception:
        pass

    await callback.answer()


# ============================================================
# FSUB TRY AGAIN
# ============================================================

@router.callback_query(
    F.data == "fsub_check"
)
async def fsub_check(
    callback: CallbackQuery
):

    complete, statuses = await check_fsub(
        callback.from_user.id
    )

    if not complete:

        # Update same FSub message
        lines = [
            "Hello There..!⚡",
            "",
            "🔘Please join all of our channels first then",
            "try again...!",
            "",
            "<b>Channel Subscription Status:</b>",
            "",
        ]

        keyboard = []

        number = 1

        for item in statuses:

            channel = item["data"]
            joined = item["joined"]

            title = channel.get(
                "title",
                "Channel"
            )

            if joined:

                lines.append(
                    f"{number}. {escape(title)} - "
                    "<b>Joined</b> ✅"
                )

            else:

                lines.append(
                    f"{number}. {escape(title)} - "
                    "<b>Not Joined</b> ❌"
                )

                invite = channel.get(
                    "invite_link"
                )

                if invite:

                    keyboard.append(
                        [
                            InlineKeyboardButton(
                                text=title[:30],
                                url=invite
                            )
                        ]
                    )

            number += 1

        keyboard.append(
            [
                InlineKeyboardButton(
                    text="Try Again",
                    callback_data="fsub_check"
                )
            ]
        )

        keyboard.append(
            [
                InlineKeyboardButton(
                    text="CLOSE",
                    callback_data="close"
                )
            ]
        )

        await callback.message.edit_text(
            "\n".join(lines),
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=keyboard
            )
        )

        await callback.answer(
            "You haven't joined all channels.",
            show_alert=False
        )

        return

    # All joined
    try:
        await callback.message.delete()
    except Exception:
        pass

    await send_start(
        callback.message
    )

    await callback.answer(
        "All channels joined!"
    )


# ============================================================
# BROADCAST
# ============================================================

@router.message(Command("broadcast"))
async def broadcast(
    message: Message
):

    if not await is_mod(
        message.from_user.id
    ):
        return await message.answer(
            "⛔ Moderator/Admin only."
        )

    if not message.reply_to_message:

        return await message.answer(
            "Reply to the message you want to broadcast, "
            "then use /broadcast."
        )

    recipients = users.find(
        {
            "banned": {
                "$ne": True
            }
        },
        {
            "user_id": 1
        }
    )

    sent = 0
    failed = 0

    delete_seconds = await get_auto_delete_seconds()

    for u in await recipients.to_list(
        length=None
    ):

        uid = u["user_id"]

        try:

            copied = await bot.copy_message(
                chat_id=uid,
                from_chat_id=message.chat.id,
                message_id=message.reply_to_message.message_id
            )

            # Save mapping for replies
            await broadcasts.insert_one(
                {
                    "broadcast_message_id": copied.message_id,
                    "user_id": uid,
                    "sender_chat_id": uid,
                    "created_at": now(),
                }
            )

            sent += 1

            # Auto delete broadcast
            async def delete_broadcast(
                chat_id=uid,
                message_id=copied.message_id
            ):

                await asyncio.sleep(
                    delete_seconds
                )

                try:

                    await bot.delete_message(
                        chat_id,
                        message_id
                    )

                except Exception:
                    pass

            asyncio.create_task(
                delete_broadcast()
            )

            await asyncio.sleep(
                0.035
            )

        except Exception as e:

            failed += 1

            log.warning(
                "Broadcast failed for %s: %s",
                uid,
                e
            )

    await message.answer(
        "📢 <b>Broadcast complete</b>\n\n"
        f"✅ Sent: {sent}\n"
        f"❌ Failed: {failed}"
    )


# ============================================================
# BROADCAST REPLY
# ============================================================

@router.message(
    F.reply_to_message
)
async def capture_broadcast_reply(
    message: Message
):

    if not message.from_user:
        return

    b = await broadcasts.find_one(
        {
            "user_id": message.chat.id,
            "broadcast_message_id":
                message.reply_to_message.message_id
        }
    )

    if not b:
        return

    info = (
        "📩 <b>Broadcast Reply</b>\n\n"
        f"👤 {escape(message.from_user.full_name)}\n"
        f"🆔 <code>{message.from_user.id}</code>\n"
    )

    if message.text:

        info += (
            f"💬 {escape(message.text)}"
        )

    else:

        info += "💬 [Media/Message]"

    try:

        await bot.send_message(
            OWNER_ID,
            info
        )

        await bot.forward_message(
            OWNER_ID,
            message.chat.id,
            message.message_id
        )

    except Exception as e:

        log.warning(
            "Reply forwarding failed: %s",
            e
        )


# ============================================================
# CLONE INFO
# ============================================================

@router.message(Command("clone"))
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
        "All clones use the same MongoDB database.\n\n"
        "Set the same:\n"
        "• <code>MONGO_URI</code>\n"
        "• <code>MONGO_DB</code>\n\n"
        "Each clone needs its own:\n"
        "• <code>BOT_TOKEN</code>\n"
        "• <code>BOT_USERNAME</code>\n\n"
        "⚠️ Important:\n"
        "Telegram links containing an old bot username "
        "cannot automatically switch to a new bot if the "
        "old bot is banned. The username in the link must "
        "be changed to the clone username."
    )


# ============================================================
# ERROR HANDLER
# ============================================================

@router.errors()
async def error_handler(
    event
):

    log.exception(
        "Unhandled Telegram update error: %s",
        event.exception
    )


# ============================================================
# MAIN
# ============================================================

async def main():

    log.info(
        "Starting bot..."
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

    log.info(
        "Health server running on port %s",
        PORT
    )

    await asyncio.gather(
        dp.start_polling(bot),
        server.serve()
    )


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":

    try:

        asyncio.run(
            main()
        )

    except KeyboardInterrupt:

        log.info(
            "Bot stopped."
        )
