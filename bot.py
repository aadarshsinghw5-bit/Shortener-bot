import os
import logging
import asyncio
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import quote

from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto
from telegram.constants import ChatMemberStatus
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes, MessageHandler, filters

from database import Database
from shortener import Shortener

load_dotenv()
logging.basicConfig(format="%(asctime)s | %(levelname)s | %(name)s | %(message)s", level=logging.INFO)
log = logging.getLogger("file-store-bot")
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.WARNING)
logging.getLogger("telegram.ext").setLevel(logging.WARNING)

BOT_TOKEN = os.environ["BOT_TOKEN"]
BOT_USERNAME = os.environ["BOT_USERNAME"].lstrip("@")
OWNER_ID = int(os.environ["OWNER_ID"])
DB_CHANNEL_ID = int(os.environ["DB_CHANNEL_ID"])

db = Database()
shortener = Shortener(db)

# In-memory pending settings actions. These are intentionally short-lived UI states.
pending_actions = {}


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.end_headers()
        self.wfile.write(b'{"ok":true,"service":"telegram-file-store-bot"}')

    def log_message(self, format, *args):
        return


def start_health_server():
    port = int(os.environ.get("PORT", "10000"))
    server = ThreadingHTTPServer(("0.0.0.0", port), HealthHandler)
    log.info("Health server running on port %s", port)
    server.serve_forever()


def start_image():
    return db.get_setting("start_image", "")


def start_caption():
    return (
        "<i>ʜɪ ᴛʜᴇʀᴇ....! 💥</i>\n\n"
        "ɪ ᴀᴍ ᴀ ꜰɪʟᴇ-ꜱᴛᴏʀᴇ ʙᴏᴛ.\n"
        "ɪ ᴄᴀɴ ɢᴇɴᴇʀᴀᴛᴇ ʟɪɴᴋꜱ ᴅɪʀᴇᴄᴛʟʏ ᴡɪᴛʜ ɴᴏ ᴘʀᴏʙʟᴇᴍꜱ.\n\n"
        '<b>ᴍʏ ᴏᴡɴᴇʀ:</b> <a href="https://t.me/Its_Lozo">@ɪᴛꜱ_ʟᴏᴢᴏ</a>'
    )


def about_caption():
    return (
        "<i>ᴀʙᴏᴜᴛ ᴜꜱ..</i>\n\n"
        '➤ ᴍᴀᴅᴇ ꜰᴏʀ : <a href="https://t.me/Anime_Hub_94">ᴀɴɪᴍᴇ ʜᴜʙ</a>\n'
        '➤ ᴏᴡɴᴇʀ : <a href="https://t.me/Its_Lozo">@ɪᴛꜱ_ʟᴏᴢᴏ</a>\n'
        '➤ ᴅᴇᴠᴇʟᴏᴘᴇʀ : <a href="https://t.me/Its_Lozo">@ɪᴛꜱ_ʟᴏᴢᴏ</a>\n\n'
        "ᴀᴅɪᴏꜱ !!"
    )


def start_keyboard():
    return InlineKeyboardMarkup([[InlineKeyboardButton("ᴀʙᴏᴜᴛ", callback_data="about"), InlineKeyboardButton("ᴄʟᴏꜱᴇ", callback_data="close")]])


def about_keyboard():
    return InlineKeyboardMarkup([[InlineKeyboardButton("ʙᴀᴄᴋ", callback_data="back"), InlineKeyboardButton("ᴄʟᴏꜱᴇ", callback_data="close")]])


async def render_start(message):
    image = start_image()
    if image:
        try:
            await message.reply_photo(photo=image, caption=start_caption(), parse_mode="HTML", reply_markup=start_keyboard())
            return
        except Exception:
            log.exception("Could not send configured start image")
    await message.reply_text(start_caption(), parse_mode="HTML", reply_markup=start_keyboard())


async def edit_start(query):
    image = start_image()
    if image:
        try:
            await query.edit_message_media(media=InputMediaPhoto(media=image, caption=start_caption(), parse_mode="HTML"), reply_markup=start_keyboard())
            return
        except Exception:
            log.exception("Could not restore start image")
    try:
        await query.edit_message_text(text=start_caption(), parse_mode="HTML", reply_markup=start_keyboard())
    except Exception:
        pass


async def edit_about(query):
    try:
        await query.edit_message_caption(caption=about_caption(), parse_mode="HTML", reply_markup=about_keyboard())
    except Exception:
        await query.edit_message_text(text=about_caption(), parse_mode="HTML", reply_markup=about_keyboard())


async def is_fsub_member(bot, user_id):
    missing = []
    for row in db.list_fsub():
        try:
            member = await bot.get_chat_member(int(row["channel_id"]), user_id)
            if member.status in (ChatMemberStatus.LEFT, ChatMemberStatus.BANNED):
                missing.append(row)
        except Exception:
            missing.append(row)
    return missing


def fsub_keyboard(rows):
    buttons = []
    for row in rows:
        if row.get("invite_link"):
            buttons.append([InlineKeyboardButton(f"ᴊᴏɪɴ {row.get('title') or 'ᴄʜᴀɴɴᴇʟ'}", url=row["invite_link"])])
    buttons.append([InlineKeyboardButton("✅ ᴄʜᴇᴄᴋ ᴊᴏɪɴ", callback_data="check_fsub")])
    return InlineKeyboardMarkup(buttons)


def main_link(token):
    return f"https://t.me/{BOT_USERNAME}?start={token}"


def share_url(url):
    return f"https://t.me/share/url?url={quote(url, safe='')}"


def share_keyboard(url):
    return InlineKeyboardMarkup([[InlineKeyboardButton("↗ ꜱʜᴀʀᴇ ᴜʀʟ", url=share_url(url))]])


def auth(uid):
    return uid == OWNER_ID or db.is_admin(uid)


async def genlink(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not auth(uid):
        return await update.message.reply_text("❌ ʏᴏᴜ ᴀʀᴇ ɴᴏᴛ ᴀᴜᴛʜᴏʀɪᴢᴇᴅ.")
    replied = update.message.reply_to_message
    if not replied:
        return await update.message.reply_text("ʀᴇᴘʟʏ ᴛᴏ ᴀ ᴍᴇꜱꜱᴀɢᴇ/ꜰɪʟᴇ ᴀɴᴅ ᴜꜱᴇ /ɢᴇɴʟɪɴᴋ.")

    try:
        copied = await context.bot.copy_message(chat_id=DB_CHANNEL_ID, from_chat_id=replied.chat_id, message_id=replied.message_id)
        file_id = db.add_file(DB_CHANNEL_ID, copied.message_id, copied.caption or copied.text or "")
        token = db.create_main_link(f"file:{file_id}")
        url = main_link(token)
        await context.bot.edit_message_reply_markup(chat_id=DB_CHANNEL_ID, message_id=copied.message_id, reply_markup=share_keyboard(url))
    except Exception:
        log.exception("genlink failed")
        return await update.message.reply_text("❌ ɢᴇɴʟɪɴᴋ ɢᴇɴᴇʀᴀᴛɪᴏɴ ꜰᴀɪʟᴇᴅ. ᴍᴀᴋᴇ ꜱᴜʀᴇ ᴛʜᴇ ʙᴏᴛ ɪꜱ ᴀᴅᴍɪɴ ɪɴ ᴛʜᴇ ᴅʙ ᴄʜᴀɴɴᴇʟ.")

    await update.message.reply_text(
        f"✅ <b>ɢᴇɴʟɪɴᴋ ʀᴇᴀᴅʏ</b>\n\n{url}",
        parse_mode="HTML", disable_web_page_preview=True, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("↗ ꜱʜᴀʀᴇ ᴜʀʟ", url=share_url(url))]])
    )


def parse_message_link(link):
    m = re.fullmatch(r"https?://t\.me/c/(\d+)/(\d+)", link.strip())
    if m:
        return int("-100" + m.group(1)), int(m.group(2))
    m = re.fullmatch(r"https?://t\.me/([^/]+)/(\d+)", link.strip())
    if m:
        return m.group(1), int(m.group(2))
    return None


async def batch(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not auth(uid):
        return await update.message.reply_text("❌ ʏᴏᴜ ᴀʀᴇ ɴᴏᴛ ᴀᴜᴛʜᴏʀɪᴢᴇᴅ.")
    if len(context.args) != 2:
        return await update.message.reply_text("ᴜꜱᴀɢᴇ:\n/ʙᴀᴛᴄʜ <ꜰɪʀꜱᴛ_ᴅʙ_ʟɪɴᴋ> <ʟᴀꜱᴛ_ᴅʙ_ʟɪɴᴋ>")

    first = parse_message_link(context.args[0]); last = parse_message_link(context.args[1])
    if not first or not last:
        return await update.message.reply_text("❌ ɪɴᴠᴀʟɪᴅ ᴛᴇʟᴇɢʀᴀᴍ ᴘᴏꜱᴛ ʟɪɴᴋ.")
    try:
        first_chat = await context.bot.get_chat(first[0]) if isinstance(first[0], str) else None
        last_chat = await context.bot.get_chat(last[0]) if isinstance(last[0], str) else None
        first_channel = first_chat.id if first_chat else first[0]
        last_channel = last_chat.id if last_chat else last[0]
    except Exception:
        return await update.message.reply_text("❌ ᴄᴏᴜʟᴅ ɴᴏᴛ ʀᴇꜱᴏʟᴠᴇ ᴛʜᴇ ᴅʙ ᴄʜᴀɴɴᴇʟ ʟɪɴᴋ.")
    if first_channel != DB_CHANNEL_ID or last_channel != DB_CHANNEL_ID:
        return await update.message.reply_text("❌ ʙᴏᴛʜ ʟɪɴᴋꜱ ᴍᴜꜱᴛ ʙᴇ ᴘᴏꜱᴛꜱ ꜰʀᴏᴍ ᴛʜᴇ ᴄᴏɴꜰɪɢᴜʀᴇᴅ ᴅʙ ᴄʜᴀɴɴᴇʟ.")

    lo, hi = sorted((first[1], last[1]))
    rows = db.list_files_between(DB_CHANNEL_ID, lo, hi)
    if not rows:
        return await update.message.reply_text("❌ ɴᴏ ᴅʙ ᴘᴏꜱᴛꜱ ꜰᴏᴜɴᴅ ʙᴇᴛᴡᴇᴇɴ ᴛʜᴇꜱᴇ ʟɪɴᴋꜱ.")
    batch_id = db.create_batch([r["file_id"] for r in rows])
    token = db.create_main_link(f"batch:{batch_id}")
    url = main_link(token)

    # Put the batch share URL under every post in the selected range.
    for row in rows:
        try:
            await context.bot.edit_message_reply_markup(chat_id=DB_CHANNEL_ID, message_id=row["message_id"], reply_markup=share_keyboard(url))
        except Exception:
            log.exception("Could not add batch share button to post %s", row["message_id"])

    await update.message.reply_text(
        f"✅ <b>ʙᴀᴛᴄʜ ʟɪɴᴋ ʀᴇᴀᴅʏ</b>\n\n📦 ɪᴛᴇᴍꜱ: <b>{len(rows)}</b>\n\n{url}",
        parse_mode="HTML", disable_web_page_preview=True,
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("↗ ꜱʜᴀʀᴇ ᴜʀʟ", url=share_url(url))]])
    )


async def send_download_page(message, short_url):
    image = start_image()
    caption = (
        "<b>📊 ʜᴇʏ ʙʀᴏ/ꜱɪꜱ,</b>\n\n"
        "➜ <b>ʏᴏᴜʀ ʟɪɴᴋ ɪꜱ ʀᴇᴀᴅʏ, ᴋɪɴᴅʟʏ ᴄʟɪᴄᴋ ᴏɴ\nᴅᴏᴡɴʟᴏᴀᴅ ʙᴜᴛᴛᴏɴ! 👇</b>\n\n"
        'ᴛᴏ ʙᴜʏ ᴘʀᴇᴍɪᴜᴍ, ᴄᴏɴᴛᴀᴄᴛ: <a href="https://t.me/Its_Lozo">@ɪᴛꜱ_ʟᴏᴢᴏ</a>'
    )
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("• ᴄʟɪᴄᴋ ʜᴇʀᴇ ᴛᴏ ᴅᴏᴡɴʟᴏᴀᴅ •", url=short_url)],
        [InlineKeyboardButton("ᴘʀᴇᴍɪᴜᴍ", url="https://t.me/PremiumHub094"), InlineKeyboardButton("ᴛᴜᴛᴏʀɪᴀʟ", url="https://t.me/Tutorial_Hub_94/4")],
    ])
    if image:
        try:
            await message.reply_photo(photo=image, caption=caption, parse_mode="HTML", reply_markup=keyboard)
            return
        except Exception:
            log.exception("Could not send download page image")
    await message.reply_text(caption, parse_mode="HTML", reply_markup=keyboard)


async def deliver_target(update, target):
    if target.startswith("file:"):
        row = db.get_file(target.split(":", 1)[1])
        if not row:
            return await update.message.reply_text("❌ ꜰɪʟᴇ ɴᴏᴛ ꜰᴏᴜɴᴅ.")
        try:
            await update.get_bot().copy_message(chat_id=update.effective_chat.id, from_chat_id=row["channel_id"], message_id=row["message_id"])
        except Exception:
            log.exception("File delivery failed")
            await update.message.reply_text("❌ ᴄᴏᴜʟᴅ ɴᴏᴛ ᴅᴇʟɪᴠᴇʀ ᴛʜᴇ ꜰɪʟᴇ.")
        return

    if target.startswith("batch:"):
        rows = db.get_batch_items(target.split(":", 1)[1])
        if not rows:
            return await update.message.reply_text("❌ ʙᴀᴛᴄʜ ɴᴏᴛ ꜰᴏᴜɴᴅ.")
        for row in rows:
            try:
                await update.get_bot().copy_message(chat_id=update.effective_chat.id, from_chat_id=row["channel_id"], message_id=row["message_id"])
                await asyncio.sleep(0.15)
            except Exception:
                log.exception("Batch item delivery failed")
        return

    await update.message.reply_text("❌ ɪɴᴠᴀʟɪᴅ ʟɪɴᴋ ᴛᴀʀɢᴇᴛ.")


async def verify(update, context, token):
    uid = update.effective_user.id
    missing = await is_fsub_member(context.bot, uid)
    if missing:
        await update.message.reply_text("⚡ <b>ᴊᴏɪɴ ʀᴇǫᴜɪʀᴇᴅ</b>\n\nᴊᴏɪɴ ᴀʟʟ ʀᴇǫᴜɪʀᴇᴅ ᴄʜᴀɴɴᴇʟꜱ, ᴛʜᴇɴ ᴛᴀᴘ <b>ᴄʜᴇᴄᴋ ᴊᴏɪɴ</b>.", parse_mode="HTML", reply_markup=fsub_keyboard(missing))
        return

    session_target = db.consume_shortener_session(token, uid)
    if not session_target:
        return await update.message.reply_text("❌ ᴛʜɪꜱ ꜱʜᴏʀᴛᴇɴᴇʀ ʟɪɴᴋ ɪꜱ ᴇxᴘɪʀᴇᴅ, ᴀʟʀᴇᴀᴅʏ ᴜꜱᴇᴅ, ᴏʀ ʙᴇʟᴏɴɢꜱ ᴛᴏ ᴀɴᴏᴛʜᴇʀ ᴜꜱᴇʀ.")
    await deliver_target(update, session_target)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    db.add_user(user.id, user.username or "", user.first_name or "")
    if db.is_banned(user.id):
        return await update.message.reply_text("🚫 ʏᴏᴜ ᴀʀᴇ ʙᴀɴɴᴇᴅ ꜰʀᴏᴍ ᴜꜱɪɴɢ ᴛʜɪꜱ ʙᴏᴛ.")

    if context.args:
        arg = context.args[0]
        if arg.startswith("verify_"):
            return await verify(update, context, arg[7:])
        ml = db.get_main_link(arg)
        if ml:
            target = ml["target"]
            if db.is_premium(user.id):
                missing = await is_fsub_member(context.bot, user.id)
                if missing:
                    return await update.message.reply_text("⚡ <b>ᴊᴏɪɴ ʀᴇǫᴜɪʀᴇᴅ</b>", parse_mode="HTML", reply_markup=fsub_keyboard(missing))
                return await deliver_target(update, target)
            missing = await is_fsub_member(context.bot, user.id)
            if missing:
                return await update.message.reply_text("⚡ <b>ᴊᴏɪɴ ʀᴇǫᴜɪʀᴇᴅ</b>\n\nᴊᴏɪɴ ᴀʟʟ ʀᴇǫᴜɪʀᴇᴅ ᴄʜᴀɴɴᴇʟꜱ, ᴛʜᴇɴ ᴛᴀᴘ <b>ᴄʜᴇᴄᴋ ᴊᴏɪɴ</b>.", parse_mode="HTML", reply_markup=fsub_keyboard(missing))
            short_url = shortener.create(user.id, target, BOT_USERNAME)
            if not short_url:
                return await update.message.reply_text("⚠️ ꜱʜᴏʀᴛᴇɴᴇʀ ɪꜱ ɴᴏᴛ ᴄᴏɴꜰɪɢᴜʀᴇᴅ ᴄᴏʀʀᴇᴄᴛʟʏ.")
            return await send_download_page(update.message, short_url)

    await render_start(update.message)


async def callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id

    if query.data == "close":
        try: await query.message.delete()
        except Exception: pass
        return
    if query.data == "about": return await edit_about(query)
    if query.data == "back": return await edit_start(query)

    if query.data == "check_fsub":
        missing = await is_fsub_member(context.bot, uid)
        if missing:
            await query.answer("❌ ᴊᴏɪɴ ᴀʟʟ ᴄʜᴀɴɴᴇʟꜱ ꜰɪʀꜱᴛ.", show_alert=True)
        else:
            await query.message.delete()
            await render_start(query.message.chat)
        return

    if not auth(uid):
        return

    if query.data == "settings":
        await query.edit_message_text("<b>⚙️ ꜱᴇᴛᴛɪɴɢꜱ</b>\n\nᴄʜᴏᴏꜱᴇ ᴀ ꜱᴇᴛᴛɪɴɢ:", parse_mode="HTML", reply_markup=settings_keyboard())
        return
    if query.data == "setimage_ui":
        pending_actions[uid] = "setimage"
        await query.message.reply_text("🖼️ <b>ꜱᴇɴᴅ ᴀ ᴘʜᴏᴛᴏ ɴᴏᴡ.</b>\n\nᴛʜɪꜱ ᴡɪʟʟ ʙᴇ ᴜꜱᴇᴅ ᴀꜱ ᴛʜᴇ ꜱᴛᴀʀᴛ ᴀɴᴅ ᴅᴏᴡɴʟᴏᴀᴅ-ᴘᴀɢᴇ ɪᴍᴀɢᴇ.", parse_mode="HTML")
        return
    if query.data == "admins_ui":
        lines = ["<b>👥 ᴀᴅᴍɪɴ ʟɪꜱᴛ</b>", "", f"👑 <a href=\"tg://user?id={OWNER_ID}\">ᴏᴡɴᴇʀ</a> — <code>{OWNER_ID}</code>"]
        for row in db.list_admins():
            aid = int(row["user_id"]); lines.append(f"• <a href=\"tg://user?id={aid}\">ᴀᴅᴍɪɴ</a> — <code>{aid}</code>")
        await query.edit_message_text("\n".join(lines), parse_mode="HTML", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("➕ ᴀᴅᴅ ᴀᴅᴍɪɴ", callback_data="add_admin_ui"), InlineKeyboardButton("➖ ʀᴇᴍᴏᴠᴇ", callback_data="remove_admin_ui")],[InlineKeyboardButton("↩️ ʙᴀᴄᴋ", callback_data="settings")]]))
        return
    if query.data == "add_admin_ui":
        pending_actions[uid] = "add_admin"
        await query.message.reply_text("➕ <b>ꜱᴇɴᴅ ᴛʜᴇ ᴀᴅᴍɪɴ ᴜꜱᴇʀ ɪᴅ.</b>", parse_mode="HTML")
        return
    if query.data == "remove_admin_ui":
        pending_actions[uid] = "remove_admin"
        await query.message.reply_text("➖ <b>ꜱᴇɴᴅ ᴛʜᴇ ᴀᴅᴍɪɴ ᴜꜱᴇʀ ɪᴅ.</b>", parse_mode="HTML")
        return
    if query.data == "fsub_ui":
        pending_actions[uid] = "add_fsub"
        await query.message.reply_text("➕ <b>ꜰꜱᴜʙ ᴄʜᴀɴɴᴇʟ</b>\n\nꜰᴏʀᴍᴀᴛ:\n<code>-1001234567890 | https://t.me/+invite | Channel Name</code>", parse_mode="HTML")
        return
    if query.data == "fsub_list_ui":
        rows = db.list_fsub()
        lines = ["<b>📢 ꜰꜱᴜʙ ᴄʜᴀɴɴᴇʟꜱ</b>", ""]
        for r in rows:
            lines.append(f"• {r.get('title','CHANNEL')} — <code>{r.get('channel_id')}</code>")
        if not rows: lines.append("ɴᴏ ᴄʜᴀɴɴᴇʟꜱ ᴀᴅᴅᴇᴅ.")
        await query.edit_message_text("\n".join(lines), parse_mode="HTML", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("➕ ᴀᴅᴅ", callback_data="fsub_ui")],[InlineKeyboardButton("↩️ ʙᴀᴄᴋ", callback_data="settings")]]))
        return
    if query.data == "del_fsub_ui":
        pending_actions[uid] = "remove_fsub"
        await query.message.reply_text("➖ <b>ꜱᴇɴᴅ ᴛʜᴇ ꜰꜱᴜʙ ᴄʜᴀɴɴᴇʟ ɪᴅ ᴛᴏ ʀᴇᴍᴏᴠᴇ.</b>", parse_mode="HTML")
        return


def settings_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🖼️ ꜱᴇᴛ ɪᴍᴀɢᴇ", callback_data="setimage_ui"), InlineKeyboardButton("👥 ᴀᴅᴍɪɴꜱ", callback_data="admins_ui")],
        [InlineKeyboardButton("➕ ᴀᴅᴅ ꜰꜱᴜʙ", callback_data="fsub_ui"), InlineKeyboardButton("📢 ꜰꜱᴜʙ ʟɪꜱᴛ", callback_data="fsub_list_ui")],
        [InlineKeyboardButton("➖ ʀᴇᴍᴏᴠᴇ ꜰꜱᴜʙ", callback_data="del_fsub_ui")],
    ])


async def settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not auth(uid): return await update.message.reply_text("❌ ʏᴏᴜ ᴀʀᴇ ɴᴏᴛ ᴀᴜᴛʜᴏʀɪᴢᴇᴅ.")
    await update.message.reply_text("<b>⚙️ ꜱᴇᴛᴛɪɴɢꜱ</b>\n\nᴄʜᴏᴏꜱᴇ ᴀ ꜱᴇᴛᴛɪɴɢ:", parse_mode="HTML", reply_markup=settings_keyboard())


async def addsubs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not auth(uid): return
    if len(context.args) != 2:
        return await update.message.reply_text("ᴜꜱᴀɢᴇ: /ᴀᴅᴅꜱᴜʙꜱ <ᴜꜱᴇʀ_ɪᴅ> <ᴅᴀʏꜱ>")
    try:
        target = int(context.args[0]); days = int(context.args[1])
        if days <= 0: raise ValueError("days must be positive")
        db.add_user(target)
        expiry = db.add_premium(target, days)
        await update.message.reply_text(f"✅ ᴘʀᴇᴍɪᴜᴍ ᴀᴅᴅᴇᴅ\n\n👤 ᴜꜱᴇʀ ɪᴅ: <code>{target}</code>\n📅 ᴅᴀʏꜱ: <b>{days}</b>\n⏳ ᴇxᴘɪʀᴇꜱ: <code>{expiry.isoformat()}</code>", parse_mode="HTML")
    except Exception as e:
        await update.message.reply_text(f"❌ {e}")


async def removesubs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not auth(uid): return
    if len(context.args) != 1:
        return await update.message.reply_text("ᴜꜱᴀɢᴇ: /ʀᴇᴍᴏᴠᴇꜱᴜʙꜱ <ᴜꜱᴇʀ_ɪᴅ>")
    try:
        target = int(context.args[0]); db.remove_premium(target)
        await update.message.reply_text(f"✅ ᴘʀᴇᴍɪᴜᴍ ʀᴇᴍᴏᴠᴇᴅ\n\n👤 ᴜꜱᴇʀ ɪᴅ: <code>{target}</code>", parse_mode="HTML")
    except Exception as e:
        await update.message.reply_text(f"❌ {e}")


async def settings_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    action = pending_actions.get(uid)
    if not action or not auth(uid): return

    if action == "setimage":
        if not update.message.photo:
            return await update.message.reply_text("🖼️ ᴘʟᴇᴀꜱᴇ ꜱᴇɴᴅ ᴀ ᴘʜᴏᴛᴏ.")
        db.set_setting("start_image", update.message.photo[-1].file_id)
        pending_actions.pop(uid, None)
        return await update.message.reply_text("✅ Start image updated successfully.")

    text = (update.message.text or "").strip()
    if action in ("add_admin", "remove_admin"):
        try: target = int(text)
        except ValueError: return await update.message.reply_text("❌ ᴠᴀʟɪᴅ ᴜꜱᴇʀ ɪᴅ ꜱᴇɴᴅ ᴋᴀʀᴏ.")
        if action == "add_admin": db.add_admin(target); msg = "✅ ᴀᴅᴍɪɴ ᴀᴅᴅᴇᴅ."
        else: db.remove_admin(target); msg = "✅ ᴀᴅᴍɪɴ ʀᴇᴍᴏᴠᴇᴅ."
        pending_actions.pop(uid, None)
        return await update.message.reply_text(msg)

    if action == "add_fsub":
        parts = [p.strip() for p in text.split("|", 2)]
        if len(parts) != 3: return await update.message.reply_text("❌ ᴜꜱᴇ: <code>CHANNEL_ID | INVITE_LINK | TITLE</code>", parse_mode="HTML")
        db.add_fsub(parts[0], parts[1], parts[2]); pending_actions.pop(uid, None)
        return await update.message.reply_text("✅ ꜰꜱᴜʙ ᴄʜᴀɴɴᴇʟ ᴀᴅᴅᴇᴅ.")

    if action == "remove_fsub":
        db.del_fsub(text); pending_actions.pop(uid, None)
        return await update.message.reply_text("✅ ꜰꜱᴜʙ ᴄʜᴀɴɴᴇʟ ʀᴇᴍᴏᴠᴇᴅ.")


async def channel_post_indexer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    post = update.channel_post
    if not post or post.chat_id != DB_CHANNEL_ID: return
    try: db.add_file(DB_CHANNEL_ID, post.message_id, post.caption or post.text or "")
    except Exception: log.exception("Could not index DB channel post")


def main():
    threading.Thread(target=start_health_server, daemon=True).start()
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("genlink", genlink))
    app.add_handler(CommandHandler("batch", batch))
    app.add_handler(CommandHandler("settings", settings))
    app.add_handler(CommandHandler("addsubs", addsubs))
    app.add_handler(CommandHandler("removesubs", removesubs))
    app.add_handler(CallbackQueryHandler(callback))
    app.add_handler(MessageHandler(filters.PHOTO | (filters.TEXT & ~filters.COMMAND), settings_input), group=1)
    app.add_handler(MessageHandler(filters.ALL, channel_post_indexer), group=10)
    log.info("Bot starting")
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True, close_loop=False)


if __name__ == "__main__":
    main()
