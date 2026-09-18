import asyncio
import hashlib
import logging
import os
import re
import uuid
from datetime import datetime, timezone, timedelta
from typing import Optional

from aiogram import Bot, Dispatcher, F, Router
from aiogram.exceptions import TelegramRetryAfter
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
    LinkPreviewOptions,
)
from fastapi import FastAPI
import uvicorn
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import ReturnDocument

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(name)s - %(message)s")
log = logging.getLogger("file-store-bot")

BOT_TOKEN = os.environ["BOT_TOKEN"]
MONGO_URI = os.environ["MONGO_URI"]
OWNER_ID = int(os.environ["OWNER_ID"])
PORT = int(os.getenv("PORT", "10000"))
BOT_USERNAME = os.getenv("BOT_USERNAME", "").lstrip("@")
MONGO_DB = os.getenv("MONGO_DB", "file_store_bot")

INSTANCE_ID = hashlib.sha256(BOT_TOKEN.encode()).hexdigest()[:20]
COMMON_SETTINGS_ID = "bot_settings"
LOCAL_SETTINGS_ID = f"bot_settings_{INSTANCE_ID}"

mongo = AsyncIOMotorClient(MONGO_URI)
db = mongo[MONGO_DB]

# =========================================================
# DATABASE ISOLATION MODEL
# =========================================================
# SHARED across every clone (do NOT isolate these):
#   admins, mods, channels, posts, batches
#
# CLONE-SPECIFIC: 
#   users, broadcasts
#
# LOCAL SETTINGS (already clone-specific):
#   start_image, fsub_channels
#
# Existing/old links stay compatible because batches/posts remain in
# the original shared collection names.
admins = db.admins
mods = db.mods
channels = db.channels
posts = db.posts
batches = db.batches

users = db[f"users_{INSTANCE_ID}"]
broadcasts = db[f"broadcasts_{INSTANCE_ID}"]

# Legacy users collection from versions where users were shared.
# It is read only for migration/compatibility; new activity is stored
# in the clone-specific users collection.
legacy_users = db.users

settings = db.settings

bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()
router = Router()
dp.include_router(router)
app = FastAPI()

# Performance tuning: keep user-facing operations responsive while staying
# below Telegram/MongoDB pressure limits.
BROADCAST_CONCURRENCY = max(1, min(int(os.getenv("BROADCAST_CONCURRENCY", "8")), 20))
SETTING_CACHE_TTL = float(os.getenv("SETTING_CACHE_TTL", "15"))
_setting_cache = {}
_setting_cache_expiry = {}

@app.api_route("/", methods=["GET", "HEAD"])
async def health():
    return {"ok": True, "service": "telegram-file-store-bot"}

@app.api_route("/health", methods=["GET", "HEAD"])
async def health2():
    return {"ok": True}


def now():
    return datetime.now(timezone.utc)


def is_owner(user_id: int) -> bool:
    return user_id == OWNER_ID

async def is_admin(user_id: int) -> bool:
    return is_owner(user_id) or await admins.find_one({"user_id": user_id}) is not None

async def is_mod(user_id: int) -> bool:
    return await is_admin(user_id) or await mods.find_one({"user_id": user_id}) is not None

async def ensure_user(message: Message):
    """Fast user upsert: one Mongo round-trip and no legacy lookup per /start."""
    if not message.from_user:
        return None
    u = message.from_user
    return await users.find_one_and_update(
        {"user_id": u.id},
        {"$set": {
            "user_id": u.id,
            "username": u.username,
            "first_name": u.first_name,
            "last_name": u.last_name,
            "last_seen": now(),
        }},
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )


async def migrate_legacy_users_once():
    """Copy the old shared audience into this clone only when this clone has
    no users yet. Future users remain completely clone-specific."""
    try:
        if await users.count_documents({}) > 0:
            return
        legacy_docs = await legacy_users.find({}, {"_id": 0}).to_list(length=None)
        if not legacy_docs:
            return
        operations = []
        for doc in legacy_docs:
            uid = doc.get("user_id")
            if uid:
                operations.append({"update_one": {
                    "filter": {"user_id": uid},
                    "update": {"$setOnInsert": doc},
                    "upsert": True,
                }})
        if operations:
            # Motor/PyMongo accept a list of WriteModel objects, so build the
            # operations using UpdateOne below for compatibility.
            from pymongo import UpdateOne
            await users.bulk_write([
                UpdateOne(x["update_one"]["filter"], x["update_one"]["update"], upsert=True)
                for x in operations
            ], ordered=False)
            log.info("Migrated %s legacy users into clone users collection.", len(operations))
    except Exception as e:
        log.warning("Legacy user migration failed: %s", e)


LOCAL_SETTINGS = {"start_image", "fsub_channels"}

async def get_setting(key, default=None):
    document_id = LOCAL_SETTINGS_ID if key in LOCAL_SETTINGS else COMMON_SETTINGS_ID
    cached = _setting_cache.get((document_id, key))
    if cached is not None and _setting_cache_expiry.get((document_id, key), 0) > asyncio.get_running_loop().time():
        return cached
    data = await settings.find_one({"_id": document_id}, {key: 1})
    value = default if not data else data.get(key, default)
    _setting_cache[(document_id, key)] = value
    _setting_cache_expiry[(document_id, key)] = asyncio.get_running_loop().time() + SETTING_CACHE_TTL
    return value

async def set_setting(key, value):
    document_id = LOCAL_SETTINGS_ID if key in LOCAL_SETTINGS else COMMON_SETTINGS_ID
    await settings.update_one({"_id": document_id}, {"$set": {key: value}}, upsert=True)
    _setting_cache[(document_id, key)] = value
    _setting_cache_expiry[(document_id, key)] = asyncio.get_running_loop().time() + SETTING_CACHE_TTL


async def get_bot_username():
    global BOT_USERNAME
    if BOT_USERNAME:
        return BOT_USERNAME
    me = await bot.get_me()
    BOT_USERNAME = me.username
    return BOT_USERNAME


def profile_link(user_id: int, name: str):
    return f'<a href="tg://user?id={user_id}">{name or "User"}</a>'

async def get_user_name(user_id: int):
    data = await users.find_one({"user_id": user_id})
    if data:
        name = data.get("first_name") or data.get("username")
        if name:
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


def parse_link(link: str):
    link = link.strip()
    m = re.match(r"https?://t\.me/c/(\d+)/(\d+)", link)
    if m:
        return int("-100" + m.group(1)), int(m.group(2))
    m = re.match(r"https?://t\.me/([A-Za-z0-9_]+)/(\d+)", link)
    if m:
        return "@" + m.group(1), int(m.group(2))
    return None


def media_kind(message: Message) -> Optional[str]:
    if message.photo: return "photo"
    if message.video: return "video"
    if message.document: return "document"
    if message.audio: return "audio"
    if message.voice: return "voice"
    if message.animation: return "animation"
    if message.sticker: return "sticker"
    if message.text: return "text"
    return None


def get_file_unique_id(message: Message) -> Optional[str]:
    if message.photo: return message.photo[-1].file_unique_id
    if message.video: return message.video.file_unique_id
    if message.document: return message.document.file_unique_id
    if message.audio: return message.audio.file_unique_id
    if message.voice: return message.voice.file_unique_id
    if message.animation: return message.animation.file_unique_id
    if message.sticker: return message.sticker.file_unique_id
    return None


def link_preview_disabled():
    return LinkPreviewOptions(is_disabled=True)

async def delete_message_later(chat_id: int, message_id: int, seconds: int):
    try:
        await asyncio.sleep(seconds)
        await bot.delete_message(chat_id, message_id)
    except Exception as e:
        log.debug("Auto delete failed: %s", e)


def schedule_delete(chat_id: int, message_id: int, seconds: int):
    asyncio.create_task(delete_message_later(chat_id, message_id, seconds))

async def send_auto_delete_notice(chat_id: int, minutes: int):
    if minutes <= 0:
        return None
    msg = await bot.send_message(chat_id, f"<i>This File is deleting automatically in {minutes} minutes. Forward in your Saved Messages..!</i>")
    schedule_delete(chat_id, msg.message_id, minutes * 60)
    return msg

START_TEXT = (
    "<b><i>Hi There...! 💥</i></b>\n\n"
    "<i>I am a file-store bot.\nI can generate links directly with no problems.</i>\n\n"
    '<b>My Owner:</b> <a href="https://t.me/Its_Lozo">@Its_Lozo</a>'
)

def start_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="ABOUT", callback_data="about"), InlineKeyboardButton(text="CLOSE", callback_data="close")]])

async def send_start_interface(target):
    image = await get_setting("start_image", None)
    keyboard = start_keyboard()
    if isinstance(target, Message):
        if image:
            try: return await target.answer_photo(photo=image, caption=START_TEXT, reply_markup=keyboard)
            except Exception as e: log.warning("Start image failed: %s", e)
        return await target.answer(START_TEXT, reply_markup=keyboard, link_preview_options=link_preview_disabled())
    message = target.message
    if image:
        try: return await message.answer_photo(photo=image, caption=START_TEXT, reply_markup=keyboard)
        except Exception as e: log.warning("Start image failed: %s", e)
    return await message.answer(START_TEXT, reply_markup=keyboard, link_preview_options=link_preview_disabled())

ABOUT_TEXT = (
    "<b><i>About Us..</i></b>\n\n<i>➤ Made for : <a href=\"https://t.me/Anime_Hub_94\">Anime Hub</a>\n"
    "➤ Owner : <a href=\"https://t.me/Its_Lozo\">@Its_Lozo</a>\n"
    "➤ Developer : <a href=\"https://t.me/Its_Lozo\">@Its_Lozo</a>\n\nAdios !!</i>"
)

def about_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="BACK", callback_data="back_start"), InlineKeyboardButton(text="CLOSE", callback_data="close")]])

@router.callback_query(F.data == "about")
async def about_callback(callback: CallbackQuery):
    await callback.answer()
    try: await callback.message.edit_caption(caption=ABOUT_TEXT, reply_markup=about_keyboard())
    except Exception:
        try: await callback.message.edit_text(ABOUT_TEXT, reply_markup=about_keyboard())
        except Exception as e: log.warning("About edit failed: %s", e)

@router.callback_query(F.data == "back_start")
async def back_start_callback(callback: CallbackQuery):
    await callback.answer()
    try: await callback.message.delete()
    except Exception: pass
    await send_start_interface(callback)

@router.callback_query(F.data == "close")
async def close_callback(callback: CallbackQuery):
    await callback.answer()
    try: await callback.message.delete()
    except Exception: pass

@router.channel_post()
async def remember_channel_post(message: Message):
    kind = media_kind(message)
    if not kind: return
    await asyncio.gather(
        posts.update_one(
            {"chat_id": message.chat.id, "message_id": message.message_id},
            {"$set": {
                "chat_id": message.chat.id, "message_id": message.message_id,
                "kind": kind, "file_unique_id": get_file_unique_id(message),
                "created_at": message.date, "caption": message.caption, "text": message.text,
            }}, upsert=True),
        channels.update_one(
            {"chat_id": message.chat.id},
            {"$set": {"chat_id": message.chat.id, "title": message.chat.title, "username": message.chat.username}},
            upsert=True),
    )

async def get_fsub_channels(): return await get_setting("fsub_channels", [])
async def save_fsub_channels(items): await set_setting("fsub_channels", items)

async def check_user_joined(user_id: int, channel_id):
    try:
        member = await bot.get_chat_member(channel_id, user_id)
        raw = getattr(member, "status", None)
        status = str(getattr(raw, "value", raw)).lower()
        if status in ("creator", "administrator", "member"): return True
        if status == "restricted": return bool(getattr(member, "is_member", False))
        return False
    except Exception as e:
        log.warning("FSUB CHECK ERROR user=%s channel=%s: %s", user_id, channel_id, e)
        return False

async def build_fsub_message(user_id: int):
    fsubs = await get_fsub_channels()
    if not fsubs:
        return None, None, True

    # Telegram membership checks are independent, so do them concurrently.
    results = await asyncio.gather(
        *(check_user_joined(user_id, channel.get("chat_id")) for channel in fsubs),
        return_exceptions=True,
    )
    lines = ["🔵 <b>Hello There..!⚡</b>", "", "⭕ Please join all of our channels first then try again..!", "", "<b>Channel Subscription Status:</b>", ""]
    buttons, all_joined = [], True
    for i, (channel, result) in enumerate(zip(fsubs, results), 1):
        joined = bool(result) if not isinstance(result, Exception) else False
        title = channel.get("title", "Channel")
        if joined:
            lines.append(f"{i}. <b>{title}</b> - <b>JOINED</b> ✅")
        else:
            all_joined = False
            lines.append(f"{i}. <b>{title}</b> - <b>NOT JOINED</b> ❌")
            invite = channel.get("invite_link")
            if invite:
                buttons.append([InlineKeyboardButton(text=f"Join {title}", url=invite)])
    if not all_joined:
        buttons.append([InlineKeyboardButton(text="🔄 Try Again", callback_data="fsub_check")])
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=buttons) if buttons else None, all_joined


async def show_fsub(message: Message):
    if not message.from_user: return False
    text, keyboard, all_joined = await build_fsub_message(message.from_user.id)
    if all_joined: return False
    image = await get_setting("start_image", None)
    if image:
        try:
            await message.answer_photo(photo=image, caption=text, reply_markup=keyboard); return True
        except Exception: pass
    await message.answer(text, reply_markup=keyboard); return True

@router.callback_query(F.data == "fsub_check")
async def fsub_check_callback(callback: CallbackQuery):
    text, keyboard, all_joined = await build_fsub_message(callback.from_user.id)
    if all_joined:
        await callback.answer("✅ All channels joined!")
        try: await callback.message.delete()
        except Exception: pass
        await send_start_interface(callback); return
    await callback.answer("❌ You haven't joined all channels yet.", show_alert=True)
    try: await callback.message.edit_caption(caption=text, reply_markup=keyboard)
    except Exception:
        try: await callback.message.edit_text(text, reply_markup=keyboard)
        except Exception: pass

@router.message(CommandStart())
async def start(message: Message):
    await ensure_user(message)
    if await users.find_one({"user_id": message.from_user.id, "banned": True}):
        return await message.answer("🚫 You are banned from using this bot.")
    args = (message.text or "").split(maxsplit=1)
    if len(args) == 2 and args[1].startswith("batch_"):
        if await show_fsub(message): return
        batch_data = await batches.find_one({"_id": args[1][6:]})
        if not batch_data: return await message.answer("❌ Batch not found or expired.")
        items = batch_data.get("items", [])
        if not items: return await message.answer("❌ This batch is empty.")
        wait_msg = await message.answer("Please Wait.....")
        sent_messages = []
        for item in items:
            try:
                copied = await bot.copy_message(chat_id=message.chat.id, from_chat_id=item["chat_id"], message_id=item["message_id"])
                sent_messages.append(copied); await asyncio.sleep(0.02)
            except Exception as e: log.warning("Batch copy failed: %s", e)
        try: await wait_msg.delete()
        except Exception: pass
        minutes = await get_setting("auto_delete_minutes", 5)
        if minutes and minutes > 0:
            for sent in sent_messages: schedule_delete(message.chat.id, sent.message_id, minutes * 60)
            await send_auto_delete_notice(message.chat.id, minutes)
        return
    if await show_fsub(message): return
    await send_start_interface(message)

@router.message(Command("genlink"))
async def genlink(message: Message):
    await ensure_user(message)
    if not await is_mod(message.from_user.id): return await message.answer("⛔ Only Owner, Admins and Moderators can generate links.")
    if not message.reply_to_message: return await message.answer("Reply to any message/file and use <code>/genlink</code>.")
    if not media_kind(message.reply_to_message): return await message.answer("❌ This message cannot be stored.")
    bid = uuid.uuid4().hex[:16]
    await batches.insert_one({"_id": bid, "items": [{"chat_id": message.reply_to_message.chat.id, "message_id": message.reply_to_message.message_id}], "created_by": message.from_user.id, "created_at": now(), "type": "genlink"})
    username = await get_bot_username()
    await message.answer(f"🔗 <b>Link created!</b>\n\nhttps://t.me/{username}?start=batch_{bid}", link_preview_options=link_preview_disabled())

@router.message(Command("batch"))
async def batch(message: Message):
    await ensure_user(message)
    if not await is_mod(message.from_user.id): return await message.answer("⛔ Only Owner, Admins and Moderators can create batches.")
    parts = (message.text or "").split()
    if len(parts) != 3: return await message.answer("Usage:\n<code>/batch LINK1 LINK2</code>\n\nBoth links must be from the same channel.")
    first, second = parse_link(parts[1]), parse_link(parts[2])
    if not first or not second: return await message.answer("❌ Invalid Telegram channel post link.")
    if first[0] != second[0]: return await message.answer("❌ Both posts must be from the same channel.")
    lo, hi = sorted([first[1], second[1]])
    docs = await posts.find({"chat_id": first[0], "message_id": {"$gte": lo, "$lte": hi}}).sort("message_id", 1).to_list(length=None)
    allowed = {"photo","video","document","audio","voice","animation","sticker","text"}
    items, seen = [], set()
    for doc in docs:
        key = (doc.get("chat_id"), doc.get("message_id"))
        if doc.get("kind") in allowed and key not in seen:
            seen.add(key); items.append({"chat_id": key[0], "message_id": key[1]})
    if not items: return await message.answer("❌ No supported media/text posts found between these two links.")
    bid = uuid.uuid4().hex[:16]
    await batches.insert_one({"_id": bid, "items": items, "created_by": message.from_user.id, "created_at": now(), "type": "batch"})
    username = await get_bot_username()
    await message.answer(f"✅ <b>Batch created!</b>\n\n📦 Items: <b>{len(items)}</b>\n🔗 https://t.me/{username}?start=batch_{bid}", link_preview_options=link_preview_disabled())

# ---------------- STAFF ----------------

def user_id_arg(message: Message):
    parts = (message.text or "").split()
    return int(parts[1]) if len(parts) == 2 and parts[1].isdigit() else None

@router.message(Command("addadmin"))
async def addadmin(message: Message):
    if not is_owner(message.from_user.id): return await message.answer("⛔ Owner only.")
    uid = user_id_arg(message)
    if uid is None: return await message.answer("Usage:\n<code>/addadmin USER_ID</code>")
    name = await get_user_name(uid); await admins.update_one({"user_id":uid},{"$set":{"user_id":uid,"name":name}},upsert=True)
    await message.answer(f"✅ <b>Admin added!</b>\n\n👤 {profile_link(uid,name)}\n🆔 <code>{uid}</code>")

@router.message(Command("deladmin"))
async def deladmin(message: Message):
    if not is_owner(message.from_user.id): return await message.answer("⛔ Owner only.")
    uid = user_id_arg(message)
    if uid is None: return await message.answer("Usage:\n<code>/deladmin USER_ID</code>")
    await admins.delete_one({"user_id":uid}); await message.answer("✅ Admin removed.")

@router.message(Command("addmod"))
async def addmod(message: Message):
    if not await is_admin(message.from_user.id): return await message.answer("⛔ Admin only.")
    uid = user_id_arg(message)
    if uid is None: return await message.answer("Usage:\n<code>/addmod USER_ID</code>")
    name = await get_user_name(uid); await mods.update_one({"user_id":uid},{"$set":{"user_id":uid,"name":name}},upsert=True)
    await message.answer(f"✅ <b>Moderator added!</b>\n\n👤 {profile_link(uid,name)}\n🆔 <code>{uid}</code>")

@router.message(Command("delmod"))
async def delmod(message: Message):
    if not await is_admin(message.from_user.id): return await message.answer("⛔ Admin only.")
    uid = user_id_arg(message)
    if uid is None: return await message.answer("Usage:\n<code>/delmod USER_ID</code>")
    await mods.delete_one({"user_id":uid}); await message.answer("✅ Moderator removed.")

@router.message(Command("admins"))
async def admins_list(message: Message):
    if not await is_mod(message.from_user.id): return await message.answer("⛔ Admin/Moderator only.")
    owner_data = await users.find_one({"user_id":OWNER_ID}); owner_name=(owner_data or {}).get("first_name") or (owner_data or {}).get("username") or "Owner"
    text=f"👑 <b>Owner</b>\n{profile_link(OWNER_ID,owner_name)}\n🆔 <code>{OWNER_ID}</code>\n\n🛡 <b>Admins</b>\n"
    data=await admins.find().to_list(length=None)
    text += "".join(f"• {profile_link(x['user_id'],x.get('name') or await get_user_name(x['user_id']))} — <code>{x['user_id']}</code>\n" for x in data) if data else "• None\n"
    text += "\n🛡 <b>Moderators</b>\n"; data=await mods.find().to_list(length=None)
    text += "".join(f"• {profile_link(x['user_id'],x.get('name') or await get_user_name(x['user_id']))} — <code>{x['user_id']}</code>\n" for x in data) if data else "• None\n"
    await message.answer(text)

@router.message(Command("ban"))
async def ban(message: Message):
    if not await is_mod(message.from_user.id): return await message.answer("⛔ Moderator/Admin only.")
    uid=user_id_arg(message)
    if uid is None: return await message.answer("Usage:\n<code>/ban USER_ID</code>")
    await users.update_one({"user_id":uid},{"$set":{"banned":True}},upsert=True); await message.answer("🚫 User banned.")

@router.message(Command("unban"))
async def unban(message: Message):
    if not await is_mod(message.from_user.id): return await message.answer("⛔ Moderator/Admin only.")
    uid=user_id_arg(message)
    if uid is None: return await message.answer("Usage:\n<code>/unban USER_ID</code>")
    await users.update_one({"user_id":uid},{"$set":{"banned":False}},upsert=True); await message.answer("✅ User unbanned.")

# ---------------- BROADCAST ----------------

def parse_broadcast_duration(command_text: str):
    parts=(command_text or "").split()
    if len(parts)==1: return 0,None
    if len(parts)!=2: return None,"Usage:\n<code>/broadcast</code>\n<code>/broadcast 24h</code>"
    m=re.fullmatch(r"([1-9]\d*)h",parts[1].lower())
    if not m: return None,"❌ Invalid time.\n\nUse format like:\n<code>/broadcast 1h</code>\n<code>/broadcast 24h</code>"
    return int(m.group(1))*3600,None

def broadcast_interface_keyboard(delete_seconds:int):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📢 SEND BROADCAST",callback_data=f"broadcast_confirm:{delete_seconds}")],
        [InlineKeyboardButton(text="❌ CANCEL",callback_data="broadcast_cancel")],
    ])

def format_broadcast_duration(seconds:int):
    if seconds<=0:return "PERMANENT ♾️"
    hours=seconds//3600
    return "1 HOUR" if hours==1 else f"{hours} HOURS"

@router.message(Command("broadcast"))
async def broadcast(message: Message):
    if not await is_mod(message.from_user.id): return await message.answer("⛔ Moderator/Admin only.")
    if not message.reply_to_message:
        return await message.answer("📢 <b>Broadcast</b>\n\nReply to the message you want to broadcast.\n\n<b>Permanent:</b>\n<code>/broadcast</code>\n\n<b>Auto Delete:</b>\n<code>/broadcast 24h</code>")
    delete_seconds,error=parse_broadcast_duration(message.text or "")
    if error:return await message.answer(error)
    source=message.reply_to_message
    if not media_kind(source):return await message.answer("❌ This message type cannot be broadcast.")
    await migrate_legacy_users_once()
    count=await users.count_documents({"banned":{"$ne":True}})
    interface_text=("📢 <b>BROADCAST INTERFACE</b>\n\n━━━━━━━━━━━━━━━━━━\n"
                    f"📨 Message Type: <b>{media_kind(source).upper()}</b>\n"
                    f"👥 Recipients: <b>{count}</b>\n"
                    f"⏱ Lifespan: <b>{format_broadcast_duration(delete_seconds)}</b>\n"
                    "━━━━━━━━━━━━━━━━━━\n\n")
    interface_text += ("♾️ <b>Permanent Broadcast</b>\nThe broadcasted message will remain in users' chats permanently." if delete_seconds==0 else f"🗑 <b>Auto Delete Enabled</b>\nThe broadcasted message will be deleted after <b>{delete_seconds//3600} hour(s)</b>.")
    interface_text += "\n\nPress <b>SEND BROADCAST</b> to continue."

    # IMPORTANT: save the exact source message in MongoDB. Do not depend on
    # interface.reply_to_message, because Telegram does not guarantee that a
    # bot's answer message contains reply_to_message.
    session_id=uuid.uuid4().hex
    interface=await message.answer(interface_text,reply_markup=broadcast_interface_keyboard(delete_seconds))
    await broadcasts.insert_one({
        "_id": f"session_{session_id}", "type": "broadcast_session",
        "session_id": session_id, "owner_user_id": message.from_user.id,
        "interface_chat_id": interface.chat.id, "interface_message_id": interface.message_id,
        "source_chat_id": source.chat.id, "source_message_id": source.message_id,
        "delete_seconds": delete_seconds, "created_at": now(), "status": "pending",
    })

async def copy_broadcast_message(uid: int, source_chat_id: int, source_message_id: int):
    """Copy with FloodWait retry. copy_message keeps the broadcast clean and
    works for text, media, stickers, animations, etc."""
    for attempt in range(3):
        try:
            return await bot.copy_message(
                chat_id=uid,
                from_chat_id=source_chat_id,
                message_id=source_message_id,
            )
        except TelegramRetryAfter as e:
            wait = int(getattr(e, "retry_after", 1)) + 1
            log.warning("FloodWait while broadcasting to %s; sleeping %ss", uid, wait)
            await asyncio.sleep(wait)
        except Exception:
            raise
    raise RuntimeError("Telegram FloodWait did not clear after retries")


async def execute_broadcast(source_chat_id: int, source_message_id: int, delete_seconds: int):
    # Import the old audience only once for a fresh clone.
    await migrate_legacy_users_once()
    recipients = await users.find(
        {"banned": {"$ne": True}, "user_id": {"$exists": True}},
        {"user_id": 1},
    ).to_list(length=None)

    seen = set()
    user_ids = []
    for user in recipients:
        uid = user.get("user_id")
        if uid and uid not in seen:
            seen.add(uid)
            user_ids.append(uid)

    total = len(user_ids)
    successful = blocked_wiped = deleted_wiped = failed = 0
    semaphore = asyncio.Semaphore(BROADCAST_CONCURRENCY)

    async def send_one(uid):
        async with semaphore:
            try:
                copied = await copy_broadcast_message(uid, source_chat_id, source_message_id)
                return ("ok", uid, copied.message_id, None)
            except Exception as e:
                err = str(e).lower()
                if "bot was blocked" in err or "user is deactivated" in err or "forbidden" in err:
                    return ("deactivated" if "deactivated" in err else "blocked", uid, None, str(e))
                if "chat not found" in err or "user not found" in err:
                    return ("deleted", uid, None, str(e))
                log.warning("Broadcast failed to %s: %s", uid, e)
                return ("failed", uid, None, str(e))

    # Small chunks prevent thousands of asyncio Tasks from being created at once.
    chunk_size = max(BROADCAST_CONCURRENCY * 5, 20)
    for pos in range(0, len(user_ids), chunk_size):
        chunk = user_ids[pos:pos + chunk_size]
        results = await asyncio.gather(*(send_one(uid) for uid in chunk))
        deliveries = []
        blocked_ids, deleted_ids = [], []
        created = now()
        delete_at = created + timedelta(seconds=delete_seconds) if delete_seconds > 0 else None

        for status, uid, message_id, error in results:
            if status == "ok":
                successful += 1
                deliveries.append({
                    "type": "broadcast_delivery",
                    "broadcast_message_id": message_id,
                    "user_id": uid,
                    "source_chat_id": source_chat_id,
                    "source_message_id": source_message_id,
                    "created_at": created,
                    "delete_at": delete_at,
                    "delete_seconds": delete_seconds,
                    "deleted": False,
                })
            elif status == "blocked":
                blocked_wiped += 1
                blocked_ids.append(uid)
            elif status in ("deactivated", "deleted"):
                deleted_wiped += 1
                deleted_ids.append(uid)
            else:
                failed += 1

        if deliveries:
            await broadcasts.insert_many(deliveries, ordered=False)
        wipe_ids = blocked_ids + deleted_ids
        if wipe_ids:
            await users.delete_many({"user_id": {"$in": wipe_ids}})

    return {
        "total_users": total,
        "successful": successful,
        "blocked_wiped": blocked_wiped,
        "deleted_wiped": deleted_wiped,
        "failed": failed,
    }


@router.callback_query(F.data.startswith("broadcast_confirm:"))
async def broadcast_confirm(callback: CallbackQuery):
    if not await is_mod(callback.from_user.id): return await callback.answer("⛔ Moderator/Admin only.",show_alert=True)
    try: delete_seconds=int(callback.data.split(":",1)[1])
    except Exception:return await callback.answer("❌ Invalid broadcast settings.",show_alert=True)

    # FIX: identify the session by the callback/interface message IDs,
    # then read the original replied message from MongoDB.
    session=await broadcasts.find_one({
        "type":"broadcast_session",
        "interface_chat_id":callback.message.chat.id,
        "interface_message_id":callback.message.message_id,
        "status":"pending",
    })
    if not session:
        return await callback.answer("❌ Broadcast session expired.",show_alert=True)
    if int(session.get("delete_seconds",-1)) != delete_seconds:
        return await callback.answer("❌ Broadcast session expired.",show_alert=True)

    # Claim atomically so two taps cannot start the same broadcast twice.
    claimed=await broadcasts.find_one_and_update(
        {"_id":session["_id"],"status":"pending"},
        {"$set":{"status":"running","started_at":now()}},
        return_document=ReturnDocument.AFTER,
    )
    if not claimed:
        return await callback.answer("⚠️ Broadcast is already running or finished.",show_alert=True)

    await callback.answer("📢 Broadcast started...")
    try: await callback.message.edit_text("📢 <b>BROADCAST STARTED</b>\n\n⏳ Sending message to users...\nPlease wait...")
    except Exception: pass
    try:
        result=await execute_broadcast(session["source_chat_id"],session["source_message_id"],delete_seconds)
        report=("📢 <u><b>BROADCAST COMPLETED!</b></u>\n\n📊 <b>Stats Report:</b>\n"
                f"• Total Users DB: <b>{result['total_users']}</b>\n• Successful: <b>{result['successful']}</b>\n"
                f"• Blocked Users Wiped: <b>{result['blocked_wiped']}</b>\n• Deleted Accounts Wiped: <b>{result['deleted_wiped']}</b>\n"
                f"• Unsuccessful/Failed: <b>{result['failed']}</b>\n\n⚙️ <b>Config Mode:</b> {'PBROADCAST' if delete_seconds==0 else 'TBROADCAST'}\n"
                f"⏱ <b>Task Lifespan:</b> {format_broadcast_duration(delete_seconds)}")
        await broadcasts.update_one({"_id":session["_id"]},{"$set":{"status":"completed","completed_at":now(),"result":result}})
        try: await callback.message.edit_text(report)
        except Exception: await callback.message.answer(report)
    except Exception as e:
        log.exception("Broadcast execution error: %s",e)
        await broadcasts.update_one({"_id":session["_id"]},{"$set":{"status":"failed","failed_at":now(),"error":str(e)[:2000]}})
        try: await callback.message.edit_text(f"❌ <b>Broadcast failed.</b>\n\n<code>{str(e)[:1000]}</code>")
        except Exception: pass

@router.callback_query(F.data=="broadcast_cancel")
async def broadcast_cancel(callback:CallbackQuery):
    if not await is_mod(callback.from_user.id):return await callback.answer("⛔ Moderator/Admin only.",show_alert=True)
    session=await broadcasts.find_one_and_update(
        {"type":"broadcast_session","interface_chat_id":callback.message.chat.id,"interface_message_id":callback.message.message_id,"status":"pending"},
        {"$set":{"status":"cancelled","cancelled_at":now()}},return_document=True)
    await callback.answer("❌ Broadcast cancelled.")
    try: await callback.message.edit_text("❌ <b>Broadcast cancelled.</b>")
    except Exception: pass

@router.message(F.reply_to_message)
async def capture_broadcast_reply(message: Message):
    if not message.from_user:return
    record=await broadcasts.find_one({"type":"broadcast_delivery","user_id":message.chat.id,"broadcast_message_id":message.reply_to_message.message_id})
    if not record:return
    info=(f"📩 <b>Broadcast Reply</b>\n\n👤 {message.from_user.full_name}\n🆔 <code>{message.from_user.id}</code>\n💬 {message.text or '[media/message]'}")
    try:
        await bot.send_message(OWNER_ID,info)
        await bot.forward_message(OWNER_ID,message.chat.id,message.message_id)
    except Exception as e:log.warning("Broadcast reply forwarding failed: %s",e)

# ---------------- SETTINGS / FSUB ----------------

def settings_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🖼 Set Image",callback_data="settings_image")],
        [InlineKeyboardButton(text="⏱ Auto Delete",callback_data="settings_autodelete")],
        [InlineKeyboardButton(text="📢 Force Subscribe",callback_data="settings_fsub")],
        [InlineKeyboardButton(text="👑 Admins",callback_data="settings_admins")],
        [InlineKeyboardButton(text="🛡 Moderators",callback_data="settings_mods")],
        [InlineKeyboardButton(text="❌ Close",callback_data="close")],
    ])

@router.message(Command("settings"))
async def settings_command(message:Message):
    if not await is_admin(message.from_user.id):return await message.answer("⛔ Admin only.")
    await message.answer("⚙️ <b>Sir, yahan se aap bot ki settings manage kar sakte hain.</b>\n\n<i>Please choose an option below.</i>",reply_markup=settings_keyboard())

@router.message(Command("setimage"))
async def setimage(message:Message):
    if not await is_admin(message.from_user.id):return await message.answer("⛔ Admin only.")
    if message.reply_to_message and message.reply_to_message.photo:
        await set_setting("start_image",message.reply_to_message.photo[-1].file_id);return await message.answer("✅ <b>Start image updated successfully.</b>")
    await message.answer("🖼 <b>Set Start Image</b>\n\nPhoto ke reply me <code>/setimage</code> use karo.")

@router.callback_query(F.data=="settings_image")
async def settings_image_callback(callback:CallbackQuery):
    if not await is_admin(callback.from_user.id):return await callback.answer("⛔ Admin only.",show_alert=True)
    await callback.answer();await callback.message.edit_text("🖼 <b>Set Start Image</b>\n\nAb yahin koi bhi new photo bhej dijiye.",reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Back",callback_data="settings_back")]]))
    await settings.update_one({"_id":LOCAL_SETTINGS_ID},{"$addToSet":{"pending_image_users":callback.from_user.id}},upsert=True)

@router.message(F.photo)
async def receive_new_start_image(message:Message):
    if not message.from_user or not await is_admin(message.from_user.id):return
    data=await settings.find_one({"_id":LOCAL_SETTINGS_ID});pending=(data or {}).get("pending_image_users",[])
    if message.from_user.id not in pending:return
    await set_setting("start_image",message.photo[-1].file_id)
    await settings.update_one({"_id":LOCAL_SETTINGS_ID},{"$pull":{"pending_image_users":message.from_user.id}})
    await message.answer("✅ <b>Start image updated successfully.</b>")

@router.callback_query(F.data=="settings_autodelete")
async def settings_autodelete(callback:CallbackQuery):
    if not await is_admin(callback.from_user.id):return await callback.answer("⛔ Admin only.",show_alert=True)
    current=await get_setting("auto_delete_minutes",5);await callback.answer()
    kb=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="1 Minute",callback_data="ad_1"),InlineKeyboardButton(text="5 Minutes",callback_data="ad_5")],[InlineKeyboardButton(text="10 Minutes",callback_data="ad_10"),InlineKeyboardButton(text="30 Minutes",callback_data="ad_30")],[InlineKeyboardButton(text="60 Minutes",callback_data="ad_60"),InlineKeyboardButton(text="OFF",callback_data="ad_0")],[InlineKeyboardButton(text="🔙 Back",callback_data="settings_back")]])
    await callback.message.edit_text(f"⏱ <b>Auto Delete Settings</b>\n\nCurrent: <b>{current} minutes</b>",reply_markup=kb)

@router.callback_query(F.data.startswith("ad_"))
async def set_autodelete(callback:CallbackQuery):
    if not await is_admin(callback.from_user.id):return await callback.answer("⛔ Admin only.",show_alert=True)
    minutes=int(callback.data.split("_")[1]);await set_setting("auto_delete_minutes",minutes);await callback.answer("✅ Auto delete updated!");await callback.message.edit_text(f"⏱ <b>Auto Delete Settings</b>\n\nCurrent: <b>{'OFF' if minutes==0 else f'{minutes} minutes'}</b>",reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Back",callback_data="settings_back")]]))

def fsub_settings_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="➕ Add FSub",callback_data="fsub_add_info")],[InlineKeyboardButton(text="➖ Remove FSub",callback_data="fsub_remove_info")],[InlineKeyboardButton(text="📋 Refresh List",callback_data="settings_fsub")],[InlineKeyboardButton(text="🔙 Back",callback_data="settings_back")]])

@router.callback_query(F.data=="settings_fsub")
async def settings_fsub(callback:CallbackQuery):
    if not await is_admin(callback.from_user.id):
        return await callback.answer("⛔ Admin only.", show_alert=True)

    fsubs = await get_fsub_channels()
    text = f"📢 <b>Force Subscription</b>\n\nChannels: <b>{len(fsubs)}/4</b>\n\n"

    for i, c in enumerate(fsubs, 1):
        title = c.get("title") or "Channel"
        invite = c.get("invite_link")
        if invite:
            text += f'{i}. <a href="{invite}"><b>{title}</b></a>\n'
        else:
            text += f"{i}. <b>{title}</b>\n"

    text += (
        "\n<b>Add:</b> <code>/addfsub CHANNEL_ID</code>\n"
        "<b>Remove:</b> <code>/delfsub CHANNEL_ID</code>\n\n"
        "Bot ko channel me admin banana zaroori hai."
    )

    await callback.answer()
    try:
        await callback.message.edit_text(text, reply_markup=fsub_settings_keyboard())
    except Exception as e:
        log.warning("FSub settings edit failed: %s", e)
        try:
            await callback.message.edit_caption(caption=text, reply_markup=fsub_settings_keyboard())
        except Exception:
            pass

@router.callback_query(F.data=="fsub_add_info")
async def fsub_add_info(callback:CallbackQuery):
    if not await is_admin(callback.from_user.id):return await callback.answer("⛔ Admin only.",show_alert=True)
    await callback.answer();await callback.message.edit_text("➕ <b>Add FSub Channel</b>\n\n<code>/addfsub -1001234567890</code>",reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Back",callback_data="settings_fsub")]]))

@router.callback_query(F.data=="fsub_remove_info")
async def fsub_remove_info(callback:CallbackQuery):
    if not await is_admin(callback.from_user.id):return await callback.answer("⛔ Admin only.",show_alert=True)
    await callback.answer();await callback.message.edit_text("➖ <b>Remove FSub Channel</b>\n\n<code>/delfsub -1001234567890</code>",reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Back",callback_data="settings_fsub")]]))

@router.message(Command("addfsub"))
async def addfsub(message:Message):
    if not await is_admin(message.from_user.id):return await message.answer("⛔ Admin only.")
    parts=(message.text or "").split()
    if len(parts)!=2 or not parts[1].lstrip('-').isdigit():return await message.answer("Usage:\n<code>/addfsub -1001234567890</code>")
    cid=int(parts[1]);fsubs=await get_fsub_channels()
    if cid>=0:return await message.answer("❌ Use the full private channel ID.")
    if len(fsubs)>=4:return await message.answer("❌ Maximum 4 FSub channels allowed.")
    if any(str(c.get("chat_id"))==str(cid) for c in fsubs):return await message.answer("❌ This channel is already added.")
    try: chat=await bot.get_chat(cid);me=await bot.get_me();member=await bot.get_chat_member(cid,me.id);status=str(getattr(getattr(member,'status',None),'value',getattr(member,'status',None))).lower()
    except Exception as e:return await message.answer(f"❌ I can't access this channel.\n\n<code>{str(e)[:500]}</code>")
    if status not in ("administrator","creator"):return await message.answer("❌ Bot is not admin in this channel.")
    invite=None
    try:invite=(await bot.create_chat_invite_link(cid)).invite_link
    except Exception:
        if chat.username:invite=f"https://t.me/{chat.username}"
    if not invite:return await message.answer("❌ Couldn't create an invite link.")
    fsubs.append({"chat_id":cid,"title":chat.title or "Channel","username":chat.username,"invite_link":invite});await save_fsub_channels(fsubs);await message.answer(f"✅ <b>FSub channel added!</b>\n\n📢 <a href=\"{invite}\">{chat.title or 'Channel'}</a>\n🆔 <code>{cid}</code>")

@router.message(Command("delfsub"))
async def delfsub(message:Message):
    if not await is_admin(message.from_user.id):return await message.answer("⛔ Admin only.")
    uid=user_id_arg(message)
    if uid is None:return await message.answer("Usage:\n<code>/delfsub CHANNEL_ID</code>")
    fsubs=await get_fsub_channels();new=[c for c in fsubs if str(c.get('chat_id'))!=str(uid)]
    if len(new)==len(fsubs):return await message.answer("❌ This channel is not in FSub.")
    await save_fsub_channels(new);await message.answer("✅ FSub channel removed.")

@router.message(Command("fsub"))
async def fsub_list(message:Message):
    if not await is_admin(message.from_user.id):return await message.answer("⛔ Admin only.")
    fsubs=await get_fsub_channels()
    if not fsubs:return await message.answer("📢 <b>FSub Channels</b>\n\nNo channels added.")
    text="📢 <b>FSub Channels</b>\n\n"+"".join(f"{i}. {c.get('title','Channel')}\n   🆔 <code>{c['chat_id']}</code>\n\n" for i,c in enumerate(fsubs,1));await message.answer(text)

@router.callback_query(F.data=="settings_admins")
async def settings_admins(callback:CallbackQuery):
    if not await is_admin(callback.from_user.id):
        return await callback.answer("⛔ Admin only.", show_alert=True)

    await callback.answer()

    data = await admins.find().sort("user_id", 1).to_list(length=None)
    text = (
        "👑 <b>Owner</b>\n"
        f"{profile_link(OWNER_ID, '@Its_Lozo')}\n"
        f"🆔 <code>{OWNER_ID}</code>\n\n"
        "🛡 <b>Admins</b>\n"
    )

    if data:
        for item in data:
            uid = item.get("user_id")
            if not uid:
                continue
            name = item.get("name") or await get_user_name(uid)
            text += f"• {profile_link(uid, name)}\n  🆔 <code>{uid}</code>\n"
    else:
        text += "• None\n"

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[[
            InlineKeyboardButton(
                text="🔙 Back",
                callback_data="settings_back"
            )
        ]]
    )

    try:
        await callback.message.edit_text(text, reply_markup=keyboard)
    except Exception as e:
        log.warning("Settings admins edit failed: %s", e)
        try:
            await callback.message.edit_caption(caption=text, reply_markup=keyboard)
        except Exception:
            pass

@router.callback_query(F.data=="settings_mods")
async def settings_mods(callback:CallbackQuery):
    if not await is_admin(callback.from_user.id):return await callback.answer("⛔ Admin only.",show_alert=True)
    data=await mods.find().to_list(length=None);text="🛡 <b>Moderators</b>\n\n"+ ("".join(f"• {profile_link(x['user_id'],x.get('name') or await get_user_name(x['user_id']))}\n  🆔 <code>{x['user_id']}</code>\n" for x in data) if data else "• None")
    await callback.answer();await callback.message.edit_text(text,reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Back",callback_data="settings_back")]]))

@router.callback_query(F.data=="settings_back")
async def settings_back(callback:CallbackQuery):
    if not await is_admin(callback.from_user.id):return await callback.answer("⛔ Admin only.",show_alert=True)
    await callback.answer();await callback.message.edit_text("⚙️ <b>Sir, yahan se aap bot ki settings manage kar sakte hain.</b>\n\n<i>Please choose an option below.</i>",reply_markup=settings_keyboard())

@router.message(Command("clone"))
async def clone_info(message:Message):
    if not is_owner(message.from_user.id):return await message.answer("⛔ Owner only.")
    await message.answer(
        "🔁 <b>Clone System</b>\n\n"
        "All clones same MongoDB database use karenge.\n\n"
        "<b>Clone-wise separate:</b>\n"
        "• Users\n• Broadcasts & broadcast replies\n• Start Image\n• Force Subscribe\n\n"
        "<b>Shared across clones:</b>\n"
        "• Admins\n• Moderators\n• Channel/Post records\n• Batches / Genlinks\n\n"
        "<b>Compatibility:</b> Purane batch/file links ko intentionally preserve kiya gaya hai, isliye old links continue working rahenge.\n\n"
        "Same MongoDB + same database use ho sakta hai; BOT_TOKEN se har clone ka unique data namespace banta hai."
    )

@router.message(Command("stats"))
async def stats(message:Message):
    if not await is_mod(message.from_user.id):return await message.answer("⛔ Moderator/Admin only.")
    total=await users.count_documents({});active=await users.count_documents({"banned":{"$ne":True}});banned=await users.count_documents({"banned":True})
    text=(f"📊 <b>BOT STATISTICS</b>\n\n👥 <b>Users</b>\n• Total Users: <b>{total}</b>\n• Active Users: <b>{active}</b>\n• Banned Users: <b>{banned}</b>\n\n🛡 <b>Staff</b>\n• Admins: <b>{await admins.count_documents({})}</b>\n• Moderators: <b>{await mods.count_documents({})}</b>\n\n📦 <b>Storage</b>\n• Saved Posts: <b>{await posts.count_documents({})}</b>\n• Batches/Links: <b>{await batches.count_documents({})}</b>\n\n📢 <b>Force Subscribe</b>\n• Channels: <b>{len(await get_fsub_channels())}</b>/4")
    await message.answer(text)

async def broadcast_delete_worker():
    log.info("Broadcast auto-delete worker started.")
    while True:
        try:
            expired=await broadcasts.find({"type":"broadcast_delivery","delete_at":{"$lte":now()},"deleted":{"$ne":True}}).to_list(length=100)
            for item in expired:
                uid=item.get("user_id");mid=item.get("broadcast_message_id")
                if uid and mid:
                    try:await bot.delete_message(uid,mid)
                    except Exception:pass
                await broadcasts.update_one({"_id":item["_id"]},{"$set":{"deleted":True,"deleted_at":now()}})
        except Exception as e:log.warning("Broadcast delete worker error: %s",e)
        await asyncio.sleep(30)

@router.errors()
async def error_handler(event):
    log.exception("Telegram handler error: %s",event.exception)

async def ensure_indexes():
    """Create only safe non-unique indexes; existing duplicate data cannot break startup."""
    specs = [
        (users, [("user_id", 1)]),
        (users, [("banned", 1)]),
        (admins, [("user_id", 1)]),
        (mods, [("user_id", 1)]),
        (posts, [("chat_id", 1), ("message_id", 1)]),
        (posts, [("chat_id", 1), ("message_id", 1), ("file_unique_id", 1)]),
        (batches, [("_id", 1)]),
        (broadcasts, [("type", 1), ("user_id", 1), ("broadcast_message_id", 1)]),
        (broadcasts, [("type", 1), ("delete_at", 1), ("deleted", 1)]),
        (broadcasts, [("type", 1), ("interface_chat_id", 1), ("interface_message_id", 1), ("status", 1)]),
    ]
    async def create(collection, keys):
        try:
            await collection.create_index(keys)
        except Exception as e:
            log.warning("Index creation skipped for %s: %s", collection.name, e)
    await asyncio.gather(*(create(c, k) for c, k in specs))

async def main():
    global BOT_USERNAME
    await ensure_indexes()
    try:
        me=await bot.get_me();BOT_USERNAME=me.username or BOT_USERNAME;log.info("Bot started as @%s",BOT_USERNAME)
    except Exception as e:log.warning("Could not get bot info: %s",e)
    try:
        await migrate_legacy_users_once()
    except Exception as e:
        log.warning("Startup user migration failed: %s", e)
    await bot.delete_webhook(drop_pending_updates=False)
    server=uvicorn.Server(uvicorn.Config(app,host="0.0.0.0",port=PORT,log_level="info"))
    worker=asyncio.create_task(broadcast_delete_worker())
    try:await asyncio.gather(dp.start_polling(bot),server.serve())
    finally:
        worker.cancel()
        try:await worker
        except asyncio.CancelledError:pass
        await bot.session.close()
        mongo.close()

if __name__=="__main__":asyncio.run(main())
