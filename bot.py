import asyncio
import hashlib
import logging
import os
import re
import uuid
from datetime import datetime, timezone, timedelta
from typing import Optional

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
from motor.motor_asyncio import AsyncIOMotorClient


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
MONGO_URI = os.environ["MONGO_URI"]
OWNER_ID = int(os.environ["OWNER_ID"])

PORT = int(os.getenv("PORT", "10000"))

BOT_USERNAME = os.getenv("BOT_USERNAME", "").lstrip("@")
MONGO_DB = os.getenv("MONGO_DB", "file_store_bot")


# =========================================================
# CLONE INSTANCE ID
# =========================================================

INSTANCE_ID = hashlib.sha256(
    BOT_TOKEN.encode()
).hexdigest()[:20]

COMMON_SETTINGS_ID = "bot_settings"
LOCAL_SETTINGS_ID = f"bot_settings_{INSTANCE_ID}"


# =========================================================
# MONGODB
# =========================================================

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
premium = db.premium


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


async def ensure_user(message: Message):

    if not message.from_user:
        return

    await users.update_one(
        {
            "user_id": message.from_user.id
        },
        {
            "$set": {
                "user_id": message.from_user.id,
                "username": message.from_user.username,
                "first_name": message.from_user.first_name,
                "last_name": message.from_user.last_name,
                "last_seen": now()
            }
        },
        upsert=True
    )


# =========================================================
# USER NAME
# =========================================================

async def get_user_name(
    user_id: int
):

    data = await users.find_one(
        {
            "user_id": user_id
        }
    )

    if data:

        name = (
            data.get("first_name")
            or data.get("username")
        )

        if name:
            return name

    try:

        chat = await bot.get_chat(
            user_id
        )

        if chat.first_name:
            return chat.first_name

        if chat.username:
            return chat.username

    except Exception:
        pass

    return f"User {user_id}"


def profile_link(
    user_id: int,
    name: str
):

    safe_name = name or "User"

    return (
        f'<a href="tg://user?id={user_id}">'
        f'{safe_name}</a>'
    )


# =========================================================
# PREMIUM SYSTEM
# =========================================================

async def get_premium(
    user_id: int
):

    data = await premium.find_one(
        {
            "user_id": user_id
        }
    )

    if not data:
        return None

    expires_at = data.get(
        "expires_at"
    )

    if not expires_at:
        return None

    if isinstance(
        expires_at,
        str
    ):

        try:

            expires_at = datetime.fromisoformat(
                expires_at.replace(
                    "Z",
                    "+00:00"
                )
            )

        except Exception:

            return None

    if expires_at.tzinfo is None:

        expires_at = expires_at.replace(
            tzinfo=timezone.utc
        )

    if expires_at <= now():

        await premium.delete_one(
            {
                "user_id": user_id
            }
        )

        return None

    data["expires_at"] = expires_at

    return data


async def is_premium(
    user_id: int
):

    return (
        await get_premium(user_id)
    ) is not None


def premium_remaining(
    expires_at
):

    remaining = (
        expires_at - now()
    )

    total_seconds = int(
        remaining.total_seconds()
    )

    if total_seconds <= 0:
        return 0, 0

    days = (
        total_seconds // 86400
    )

    hours = (
        total_seconds % 86400
    ) // 3600

    return days, hours


async def activate_premium(
    user_id: int,
    days: int
):

    current = await get_premium(
        user_id
    )

    current_time = now()

    if current:

        old_expiry = current[
            "expires_at"
        ]

        base = max(
            current_time,
            old_expiry
        )

    else:

        base = current_time

    expires_at = (
        base + timedelta(
            days=days
        )
    )

    await premium.update_one(
        {
            "user_id": user_id
        },
        {
            "$set": {
                "user_id": user_id,
                "expires_at": expires_at,
                "activated_at": current_time,
                "days_added": days
            }
        },
        upsert=True
    )

    return expires_at


async def remove_premium(
    user_id: int
):

    result = await premium.delete_one(
        {
            "user_id": user_id
        }
    )

    return result.deleted_count > 0


# =========================================================
# SETTINGS
# =========================================================

LOCAL_SETTINGS = {
    "start_image",
    "fsub_channels",
    "pending_image_users"
}


async def get_setting(
    key,
    default=None
):

    if key in LOCAL_SETTINGS:

        data = await settings.find_one(
            {
                "_id": LOCAL_SETTINGS_ID
            }
        )

    else:

        data = await settings.find_one(
            {
                "_id": COMMON_SETTINGS_ID
            }
        )

    if not data:
        return default

    return data.get(
        key,
        default
    )


async def set_setting(
    key,
    value
):

    if key in LOCAL_SETTINGS:

        document_id = LOCAL_SETTINGS_ID

    else:

        document_id = COMMON_SETTINGS_ID

    await settings.update_one(
        {
            "_id": document_id
        },
        {
            "$set": {
                key: value
            }
        },
        upsert=True
    )


# =========================================================
# BOT USERNAME
# =========================================================

async def get_bot_username():

    global BOT_USERNAME

    if BOT_USERNAME:
        return BOT_USERNAME

    me = await bot.get_me()

    BOT_USERNAME = me.username

    return BOT_USERNAME


# =========================================================
# LINK PARSER
# =========================================================

def parse_link(
    link: str
):

    link = link.strip()

    match = re.match(
        r"https?://t\.me/c/(\d+)/(\d+)",
        link
    )

    if match:

        return (
            int(
                "-100" + match.group(1)
            ),
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
# MEDIA DETECTION
# =========================================================

def media_kind(
    message: Message
) -> Optional[str]:

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


def link_preview_disabled():

    return LinkPreviewOptions(
        is_disabled=True
    )


# =========================================================
# AUTO DELETE
# =========================================================

async def delete_message_later(
    chat_id: int,
    message_id: int,
    seconds: int
):

    try:

        await asyncio.sleep(
            seconds
        )

        await bot.delete_message(
            chat_id,
            message_id
        )

    except Exception as e:

        log.debug(
            "Auto delete failed: %s",
            e
        )


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
                    text="👑 MY PLAN",
                    callback_data="my_plan"
                )
            ],
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


async def send_start_interface(
    target
):

    image = await get_setting(
        "start_image",
        None
    )

    keyboard = start_keyboard()

    if isinstance(
        target,
        Message
    ):

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


# =========================================================
# BACK
# =========================================================

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

    await send_start_interface(
        callback
    )


# =========================================================
# CLOSE
# =========================================================

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
# MY PLAN
# =========================================================

def my_plan_keyboard():

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔄 Refresh",
                    callback_data="my_plan"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🔙 Back",
                    callback_data="back_start"
                ),
                InlineKeyboardButton(
                    text="❌ Close",
                    callback_data="close"
                )
            ]
        ]
    )


async def build_my_plan_text(
    user_id: int
):

    user = await users.find_one(
        {
            "user_id": user_id
        }
    )

    if user:

        name = (
            user.get("first_name")
            or user.get("username")
            or "User"
        )

    else:

        name = await get_user_name(
            user_id
        )

    plan = await get_premium(
        user_id
    )

    if not plan:

        return (
            "👑 <b>YOUR PREMIUM MEMBERSHIP STATUS</b>\n\n"
            f"👤 Name: {name}\n"
            f"🆔 User ID: {user_id}\n"
            "💎 Plan Status: Inactive\n"
            "⏳ Time Remaining: 0 Days, 0 Hours"
        )

    days, hours = premium_remaining(
        plan["expires_at"]
    )

    return (
        "👑 <b>YOUR PREMIUM MEMBERSHIP STATUS</b>\n\n"
        f"👤 Name: {name}\n"
        f"🆔 User ID: {user_id}\n"
        "💎 Plan Status: Active\n"
        f"⏳ Time Remaining: {days} Days, {hours} Hours"
    )


@router.message(
    Command("myplan")
)
async def myplan_command(
    message: Message
):

    await ensure_user(
        message
    )

    text = await build_my_plan_text(
        message.from_user.id
    )

    await message.answer(
        text,
        reply_markup=my_plan_keyboard()
    )


@router.callback_query(
    F.data == "my_plan"
)
async def my_plan_callback(
    callback: CallbackQuery
):

    await callback.answer()

    text = await build_my_plan_text(
        callback.from_user.id
    )

    try:

        await callback.message.edit_text(
            text,
            reply_markup=my_plan_keyboard()
        )

    except Exception:

        try:

            await callback.message.edit_caption(
                caption=text,
                reply_markup=my_plan_keyboard()
            )

        except Exception as e:

            log.warning(
                "My Plan refresh failed: %s",
                e
            )


# =========================================================
# CHANNEL POST MEMORY
# =========================================================

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
            "message_id": message.message_id
        },
        {
            "$set": {
                "chat_id": message.chat.id,
                "message_id": message.message_id,
                "kind": kind,
                "created_at": message.date,
                "caption": message.caption,
                "text": message.text
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
                "username": message.chat.username
            }
        },
        upsert=True
    )


# =========================================================
# FORCE SUBSCRIPTION
# =========================================================

async def get_fsub_channels():

    return await get_setting(
        "fsub_channels",
        []
    )


async def save_fsub_channels(
    items
):

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

        status = str(
            status
        ).lower()

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
            "FSUB CHECK ERROR | user=%s | channel=%s | %s",
            user_id,
            channel_id,
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

        channel_id = channel.get(
            "chat_id"
        )

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


async def show_fsub(
    message: Message
):

    if not message.from_user:
        return False

    text, keyboard, all_joined = (
        await build_fsub_message(
            message.from_user.id
        )
    )

    if all_joined:
        return False

    image = await get_setting(
        "start_image",
        None
    )

    if image:

        try:

            await message.answer_photo(
                photo=image,
                caption=text,
                reply_markup=keyboard
            )

            return True

        except Exception as e:

            log.warning(
                "FSub image failed: %s",
                e
            )

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

        await send_start_interface(
            callback
        )

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

        except Exception as e:

            log.warning(
                "FSub refresh failed: %s",
                e
            )


# =========================================================
# START
# =========================================================

@router.message(
    CommandStart()
)
async def start(
    message: Message
):

    await ensure_user(
        message
    )

    if await users.find_one(
        {
            "user_id": message.from_user.id,
            "banned": True
        }
    ):

        return await message.answer(
            "🚫 You are banned from using this bot."
        )

    args = (
        (message.text or "")
        .split(
            maxsplit=1
        )
    )

    # =====================================================
    # BATCH LINK
    # =====================================================

    if (
        len(args) == 2
        and args[1].startswith("batch_")
    ):

        has_fsub = await show_fsub(
            message
        )

        if has_fsub:
            return

        batch_id = args[1][6:]

        batch_data = await batches.find_one(
            {
                "_id": batch_id
            }
        )

        if not batch_data:

            return await message.answer(
                "❌ Batch not found or expired."
            )

        items = batch_data.get(
            "items",
            []
        )

        if not items:

            return await message.answer(
                "❌ This batch is empty."
            )

        wait_msg = await message.answer(
            "Please Wait....."
        )

        sent_messages = []

        for item in items:

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
                    0.02
                )

            except Exception as e:

                log.warning(
                    "Batch copy failed %s/%s: %s",
                    item["chat_id"],
                    item["message_id"],
                    e
                )

        try:
            await wait_msg.delete()
        except Exception:
            pass

        # =================================================
        # PREMIUM = AD FREE
        # =================================================

        user_is_premium = await is_premium(
            message.from_user.id
        )

        if not user_is_premium:

            delete_minutes = await get_setting(
                "auto_delete_minutes",
                5
            )

            if (
                delete_minutes
                and delete_minutes > 0
            ):

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

    has_fsub = await show_fsub(
        message
    )

    if has_fsub:
        return

    await send_start_interface(
        message
    )


# =========================================================
# GENLINK
# =========================================================

@router.message(
    Command("genlink")
)
async def genlink(
    message: Message
):

    await ensure_user(
        message
    )

    if not await is_mod(
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

    kind = media_kind(
        source
    )

    if not kind:

        return await message.answer(
            "❌ This message cannot be stored."
        )

    bid = uuid.uuid4().hex[:16]

    item = {
        "chat_id": source.chat.id,
        "message_id": source.message_id
    }

    await batches.insert_one(
        {
            "_id": bid,
            "items": [item],
            "created_by": message.from_user.id,
            "created_at": now(),
            "type": "genlink"
        }
    )

    username = await get_bot_username()

    link = (
        f"https://t.me/{username}"
        f"?start=batch_{bid}"
    )

    await message.answer(
        "🔗 <b>Link created!</b>\n\n"
        f"{link}",
        link_preview_options=link_preview_disabled()
    )


# =========================================================
# BATCH
# =========================================================

@router.message(
    Command("batch")
)
async def batch(
    message: Message
):

    await ensure_user(
        message
    )

    if not await is_mod(
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

    first = parse_link(
        parts[1]
    )

    second = parse_link(
        parts[2]
    )

    if not first or not second:

        return await message.answer(
            "❌ Invalid Telegram channel post link."
        )

    if first[0] != second[0]:

        return await message.answer(
            "❌ Both posts must be from the same channel."
        )

    chat_id = first[0]

    lo, hi = sorted(
        [
            first[1],
            second[1]
        ]
    )

    docs = await posts.find(
        {
            "chat_id": chat_id,
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
            "❌ I don't have these channel posts "
            "recorded yet.\n\n"
            "Make sure the bot is admin in the channel "
            "and that the posts were published after "
            "the bot was added."
        )

    items = []

    for doc in docs:

        if doc.get("kind") not in (
            "photo",
            "video",
            "document",
            "audio",
            "voice",
            "animation",
            "sticker",
            "text"
        ):
            continue

        items.append(
            {
                "chat_id": doc["chat_id"],
                "message_id": doc["message_id"]
            }
        )

    if not items:

        return await message.answer(
            "❌ No supported media/text posts found "
            "between these two links."
        )

    bid = uuid.uuid4().hex[:16]

    await batches.insert_one(
        {
            "_id": bid,
            "items": items,
            "created_by": message.from_user.id,
            "created_at": now(),
            "type": "batch"
        }
    )

    username = await get_bot_username()

    link = (
        f"https://t.me/{username}"
        f"?start=batch_{bid}"
    )

    await message.answer(
        "✅ <b>Batch created!</b>\n\n"
        f"📦 Items: <b>{len(items)}</b>\n"
        f"🔗 {link}",
        link_preview_options=link_preview_disabled()
    )


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

    uid = int(
        parts[1]
    )

    name = await get_user_name(
        uid
    )

    await admins.update_one(
        {
            "user_id": uid
        },
        {
            "$set": {
                "user_id": uid,
                "name": name
            }
        },
        upsert=True
    )

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


# =========================================================
# ADD MOD
# =========================================================

@router.message(
    Command("addmod")
)
async def addmod(
    message: Message
):

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
            "<code>/addmod USER_ID</code>"
        )

    uid = int(
        parts[1]
    )

    name = await get_user_name(
        uid
    )

    await mods.update_one(
        {
            "user_id": uid
        },
        {
            "$set": {
                "user_id": uid,
                "name": name
            }
        },
        upsert=True
    )

    await message.answer(
        "✅ <b>Moderator added!</b>\n\n"
        f"👤 {profile_link(uid, name)}\n"
        f"🆔 <code>{uid}</code>"
    )


# =========================================================
# DELETE MOD
# =========================================================

@router.message(
    Command("delmod")
)
async def delmod(
    message: Message
):

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
            "<code>/delmod USER_ID</code>"
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


# =========================================================
# ADMINS
# =========================================================

@router.message(
    Command("admins")
)
async def admins_list(
    message: Message
):

    if not await is_mod(
        message.from_user.id
    ):

        return await message.answer(
            "⛔ Admin/Moderator only."
        )

    owner_name = "Owner"

    owner_data = await users.find_one(
        {
            "user_id": OWNER_ID
        }
    )

    if owner_data:

        owner_name = (
            owner_data.get("first_name")
            or owner_data.get("username")
            or "Owner"
        )

    text = (
        "👑 <b>Owner</b>\n"
        f"{profile_link(OWNER_ID, owner_name)}\n"
        f"🆔 <code>{OWNER_ID}</code>\n\n"
        "🛡 <b>Admins</b>\n"
    )

    admin_list = await admins.find().to_list(
        length=None
    )

    if admin_list:

        for admin in admin_list:

            uid = admin["user_id"]

            name = (
                admin.get("name")
                or await get_user_name(uid)
            )

            text += (
                f"• {profile_link(uid, name)} "
                f"— <code>{uid}</code>\n"
            )

    else:

        text += "• None\n"

    text += "\n🛡 <b>Moderators</b>\n"

    mod_list = await mods.find().to_list(
        length=None
    )

    if mod_list:

        for mod in mod_list:

            uid = mod["user_id"]

            name = (
                mod.get("name")
                or await get_user_name(uid)
            )

            text += (
                f"• {profile_link(uid, name)} "
                f"— <code>{uid}</code>\n"
            )

    else:

        text += "• None\n"

    await message.answer(
        text
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
            "<code>/ban USER_ID</code>"
        )

    uid = int(
        parts[1]
    )

    await users.update_one(
        {
            "user_id": uid
        },
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


# =========================================================
# UNBAN
# =========================================================

@router.message(
    Command("unban")
)
async def unban(
    message: Message
):

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
            "<code>/unban USER_ID</code>"
        )

    uid = int(
        parts[1]
    )

    await users.update_one(
        {
            "user_id": uid
        },
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


# =========================================================
# ADD PREMIUM
# =========================================================

@router.message(
    Command("addpremium")
)
async def addpremium(
    message: Message
):

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
        len(parts) != 3
        or not parts[1].isdigit()
        or not parts[2].isdigit()
    ):

        return await message.answer(
            "Usage:\n"
            "<code>/addpremium USER_ID DAYS</code>"
        )

    user_id = int(
        parts[1]
    )

    days = int(
        parts[2]
    )

    if days <= 0:

        return await message.answer(
            "❌ Days must be greater than 0."
        )

    expires_at = await activate_premium(
        user_id,
        days
    )

    name = await get_user_name(
        user_id
    )

    # MANAGEMENT MESSAGE
    await message.answer(
        "🎉 <b>Premium Activated!</b>\n\n"
        f"👤 {profile_link(user_id, name)}\n"
        f"🆔 <code>{user_id}</code>\n"
        f"💎 Plan: <b>{days} Days</b>"
    )

    # USER NOTIFICATION
    try:

        await bot.send_message(
            user_id,
            "🎉 <b>Congratulations!</b>\n\n"
            "Your account has been upgraded to the "
            f"Premium Ad-Free Tier for the next "
            f"{days} Days.\n"
            "Enjoy high-speed bypass-free file downloads!"
        )

    except Exception as e:

        log.warning(
            "Premium activation notification failed "
            "for %s: %s",
            user_id,
            e
        )


# =========================================================
# DELETE PREMIUM
# =========================================================

@router.message(
    Command("delpremium")
)
async def delpremium(
    message: Message
):

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
            "<code>/delpremium USER_ID</code>"
        )

    user_id = int(
        parts[1]
    )

    removed = await remove_premium(
        user_id
    )

    if not removed:

        return await message.answer(
            "❌ This user does not have "
            "an active premium subscription."
        )

    name = await get_user_name(
        user_id
    )

    # MANAGEMENT MESSAGE
    await message.answer(
        "🗑 <b>Premium Subscription Tier Revoked!</b>\n\n"
        f"👤 {profile_link(user_id, name)}\n"
        f"🆔 <code>{user_id}</code>"
    )

    # USER NOTIFICATION
    try:

        await bot.send_message(
            user_id,
            "🚨 <b>Notification:</b> "
            "Your premium subscription package has been "
            "manually revoked by the management team."
        )

    except Exception as e:

        log.warning(
            "Premium revoke notification failed "
            "for %s: %s",
            user_id,
            e
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

    if not await is_mod(
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

    delete_minutes = await get_setting(
        "auto_delete_minutes",
        5
    )

    recipient_list = await recipients.to_list(
        length=None
    )

    for user in recipient_list:

        uid = user["user_id"]

        try:

            copied = await bot.copy_message(
                chat_id=uid,
                from_chat_id=message.chat.id,
                message_id=message.reply_to_message.message_id
            )

            await broadcasts.insert_one(
                {
                    "broadcast_message_id":
                        copied.message_id,
                    "user_id": uid,
                    "created_at": now()
                }
            )

            sent += 1

            # Premium users are ad-free
            if not await is_premium(uid):

                if (
                    delete_minutes
                    and delete_minutes > 0
                ):

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
# BROADCAST REPLY
# =========================================================

@router.message(
    F.reply_to_message
)
async def capture_broadcast_reply(
    message: Message
):

    if not message.from_user:
        return

    broadcast_data = await broadcasts.find_one(
        {
            "user_id": message.chat.id,
            "broadcast_message_id":
                message.reply_to_message.message_id
        }
    )

    if not broadcast_data:
        return

    info = (
        "📩 <b>Broadcast Reply</b>\n\n"
        f"👤 {message.from_user.full_name}\n"
        f"🆔 <code>{message.from_user.id}</code>\n"
        f"💬 {message.text or '[media/message]'}"
    )

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
            "Broadcast reply forwarding failed: %s",
            e
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


# =========================================================
# SETTINGS COMMAND
# =========================================================

@router.message(
    Command("settings")
)
async def settings_command(
    message: Message
):

    if not await is_admin(
        message.from_user.id
    ):

        return await message.answer(
            "⛔ Admin only."
        )

    await message.answer(
        "⚙️ <b>Sir, yahan se aap bot ki "
        "settings manage kar sakte hain.</b>\n\n"
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

    if not await is_admin(
        callback.from_user.id
    ):

        return await callback.answer(
            "⛔ Admin only.",
            show_alert=True
        )

    await callback.answer()

    await callback.message.edit_text(
        "🖼 <b>Set Start Image</b>\n\n"
        "Ab yahin koi bhi new photo bhej dijiye.\n"
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

    await settings.update_one(
        {
            "_id": LOCAL_SETTINGS_ID
        },
        {
            "$addToSet": {
                "pending_image_users":
                    callback.from_user.id
            }
        },
        upsert=True
    )


# =========================================================
# IMAGE RECEIVER
# =========================================================

@router.message(
    F.photo
)
async def receive_new_start_image(
    message: Message
):

    if not message.from_user:
        return

    if not await is_admin(
        message.from_user.id
    ):
        return

    data = await settings.find_one(
        {
            "_id": LOCAL_SETTINGS_ID
        }
    )

    pending_users = []

    if data:

        pending_users = data.get(
            "pending_image_users",
            []
        )

    if message.from_user.id not in pending_users:
        return

    file_id = (
        message.photo[-1].file_id
    )

    await set_setting(
        "start_image",
        file_id
    )

    await settings.update_one(
        {
            "_id": LOCAL_SETTINGS_ID
        },
        {
            "$pull": {
                "pending_image_users":
                    message.from_user.id
            }
        }
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

    if not await is_admin(
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
        f"Current: <b>{current} minutes</b>\n\n"
        "Files sent to users and the auto-delete "
        "notice will be deleted after the selected time.\n\n"
        "👑 Premium users are always Ad-Free.",
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

    if not await is_admin(
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
        f"Current: <b>{current}</b>\n\n"
        "👑 Premium users are always Ad-Free.",
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

    if not await is_admin(
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

        text += (
            "No FSub channels added.\n"
        )

    text += (
        "\n<b>Add:</b>\n"
        "<code>/addfsub CHANNEL_ID</code>\n\n"
        "<b>Remove:</b>\n"
        "<code>/delfsub CHANNEL_ID</code>\n\n"
        "Bot ko channel me admin banana zaroori hai."
    )

    await callback.answer()

    await callback.message.edit_text(
        text,
        reply_markup=fsub_settings_keyboard()
    )


# =========================================================
# FSUB ADD INFO
# =========================================================

@router.callback_query(
    F.data == "fsub_add_info"
)
async def fsub_add_info(
    callback: CallbackQuery
):

    if not await is_admin(
        callback.from_user.id
    ):

        return await callback.answer(
            "⛔ Admin only.",
            show_alert=True
        )

    await callback.answer()

    await callback.message.edit_text(
        "➕ <b>Add FSub Channel</b>\n\n"
        "Channel ka ID bhejo:\n\n"
        "<code>/addfsub -1001234567890</code>\n\n"
        "Maximum <b>4 channels</b> add kar sakte ho.\n"
        "Bot ko us channel ka admin hona chahiye.",
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
# FSUB REMOVE INFO
# =========================================================

@router.callback_query(
    F.data == "fsub_remove_info"
)
async def fsub_remove_info(
    callback: CallbackQuery
):

    if not await is_admin(
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

    if not await is_admin(
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
            "<code>/addfsub CHANNEL_ID</code>\n\n"
            "Example:\n"
            "<code>/addfsub -1001234567890</code>"
        )

    try:

        channel_id = int(
            parts[1]
        )

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

        if str(
            channel.get("chat_id")
        ) == str(channel_id):

            return await message.answer(
                "❌ This channel is already added."
            )

    try:

        chat = await bot.get_chat(
            channel_id
        )

    except Exception as e:

        log.warning(
            "Cannot access FSub channel %s: %s",
            channel_id,
            e
        )

        return await message.answer(
            "❌ I can't access this channel.\n\n"
            "Make sure:\n"
            "• Channel ID is correct\n"
            "• Bot is admin in the channel\n"
            "• Bot has permission to invite users"
        )

    try:

        bot_me = await bot.get_me()

        bot_member = await bot.get_chat_member(
            chat_id=channel_id,
            user_id=bot_me.id
        )

        bot_status_raw = getattr(
            bot_member,
            "status",
            None
        )

        bot_status = getattr(
            bot_status_raw,
            "value",
            str(bot_status_raw)
        )

        bot_status = str(
            bot_status
        ).lower()

        if bot_status not in (
            "administrator",
            "creator"
        ):

            return await message.answer(
                "❌ Bot is not admin in this channel.\n\n"
                "Please make the bot an administrator first."
            )

    except Exception as e:

        log.warning(
            "Bot admin verification failed: %s",
            e
        )

        return await message.answer(
            "❌ I couldn't verify the bot's admin status.\n\n"
            "Please make sure the bot is admin in the channel."
        )

    invite_link = None

    try:

        invite = await bot.create_chat_invite_link(
            chat_id=channel_id
        )

        invite_link = invite.invite_link

    except Exception as e:

        log.warning(
            "Invite link creation failed: %s",
            e
        )

        if chat.username:

            invite_link = (
                f"https://t.me/"
                f"{chat.username}"
            )

    if not invite_link:

        return await message.answer(
            "❌ Couldn't create an invite link.\n\n"
            "Make sure the bot has permission "
            "to invite users."
        )

    data = {
        "chat_id": channel_id,
        "title": chat.title or "Channel",
        "username": chat.username,
        "invite_link": invite_link
    }

    fsubs.append(
        data
    )

    await save_fsub_channels(
        fsubs
    )

    await message.answer(
        "✅ <b>FSub channel added!</b>\n\n"
        f'📢 <a href="{invite_link}">'
        f'{chat.title or "Channel"}</a>\n'
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

    if not await is_admin(
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

        channel_id = int(
            parts[1]
        )

    except ValueError:

        return await message.answer(
            "❌ Invalid channel ID."
        )

    fsubs = await get_fsub_channels()

    new_list = [
        channel
        for channel in fsubs
        if str(
            channel.get("chat_id")
        ) != str(channel_id)
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
# FSUB LIST
# =========================================================

@router.message(
    Command("fsub")
)
async def fsub_list(
    message: Message
):

    if not await is_admin(
        message.from_user.id
    ):

        return await message.answer(
            "⛔ Admin only."
        )

    fsubs = await get_fsub_channels()

    if not fsubs:

        return await message.answer(
            "📢 <b>FSub Channels</b>\n\n"
            "No channels added."
        )

    text = (
        "📢 <b>FSub Channels</b>\n\n"
    )

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
                f'   🆔 <code>{channel["chat_id"]}</code>\n\n'
            )

        else:

            text += (
                f"{i}. {title}\n"
                f'   🆔 <code>{channel["chat_id"]}</code>\n\n'
            )

    await message.answer(
        text
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

    if not await is_admin(
        callback.from_user.id
    ):

        return await callback.answer(
            "⛔ Admin only.",
            show_alert=True
        )

    await callback.answer()

    admins_data = await admins.find().to_list(
        length=None
    )

    text = "👑 <b>Owner</b>\n"

    text += (
        f'{profile_link(OWNER_ID, "@Its_Lozo")}\n'
        f"🆔 <code>{OWNER_ID}</code>\n\n"
    )

    text += "🛡 <b>Admins</b>\n"

    if not admins_data:

        text += "• None\n"

    else:

        for admin in admins_data:

            uid = admin["user_id"]

            name = (
                admin.get("name")
                or await get_user_name(uid)
            )

            text += (
                f"• {profile_link(uid, name)}\n"
                f"  🆔 <code>{uid}</code>\n"
            )

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

    if not await is_admin(
        callback.from_user.id
    ):

        return await callback.answer(
            "⛔ Admin only.",
            show_alert=True
        )

    await callback.answer()

    mods_data = await mods.find().to_list(
        length=None
    )

    text = "🛡 <b>Moderators</b>\n\n"

    if not mods_data:

        text += "• None"

    else:

        for mod in mods_data:

            uid = mod["user_id"]

            name = (
                mod.get("name")
                or await get_user_name(uid)
            )

            text += (
                f"• {profile_link(uid, name)}\n"
                f"  🆔 <code>{uid}</code>\n"
            )

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
# SETTINGS BACK
# =========================================================

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
            "⛔ Admin only.",
            show_alert=True
        )

    await callback.answer()

    await callback.message.edit_text(
        "⚙️ <b>Sir, yahan se aap bot ki "
        "settings manage kar sakte hain.</b>\n\n"
        "<i>Please choose an option below.</i>",
        reply_markup=settings_keyboard()
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
        "All clones same MongoDB database use karenge.\n\n"
        "Every clone me:\n"
        "• Same <code>MONGO_URI</code>\n"
        "• Same <code>MONGO_DB</code>\n"
        "• Different <code>BOT_TOKEN</code>\n"
        "• Different <code>BOT_USERNAME</code>\n\n"
        "<b>Clone-specific:</b>\n"
        "• Start Image\n"
        "• Force Subscribe Channels\n\n"
        "<b>Shared:</b>\n"
        "• Users\n"
        "• Admins\n"
        "• Moderators\n"
        "• Batches\n"
        "• Premium\n"
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

        log.info(
            "Instance settings ID: %s",
            LOCAL_SETTINGS_ID
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
