import os
import logging
import asyncio
import re
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from datetime import datetime, timezone
from urllib.parse import quote

from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto
from telegram.constants import ChatMemberStatus
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, ContextTypes, filters

from database import Database
from shortener import Shortener

load_dotenv()
logging.basicConfig(format="%(asctime)s | %(levelname)s | %(name)s | %(message)s", level=logging.INFO)
log = logging.getLogger("file-store-bot")
for name in ("httpx", "httpcore", "telegram", "telegram.ext"):
    logging.getLogger(name).setLevel(logging.WARNING)

BOT_TOKEN = os.environ["BOT_TOKEN"]
BOT_USERNAME = os.environ["BOT_USERNAME"].lstrip("@")
OWNER_ID = int(os.environ["OWNER_ID"])
DB_CHANNEL_ID = int(os.environ["DB_CHANNEL_ID"])

db = Database()
shortener = Shortener(db)
_pending_image = set()
_pending_autodelete = set()
_pending_admin = set()
_pending_fsub = set()


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
    server = HTTPServer(("0.0.0.0", port), HealthHandler)
    log.info("Health server running on port %s", port)
    server.serve_forever()


def start_image():
    return db.get_setting("start_image", "")


def start_caption():
    return (
        "<i>ʜɪ ᴛʜᴇʀᴇ....! 💥</i>\n\n"
        "ɪ ᴀᴍ ᴀ ꜰɪʟᴇ-ꜱᴛᴏʀᴇ ʙᴏᴛ.\n"
        "ɪ ᴄᴀɴ ɢᴇɴᴇʀᴀᴛᴇ ʟɪɴᴋꜱ ᴅɪʀᴇᴄᴛʟʏ ᴡɪᴛʜ ɴᴏ ᴘʀᴏʙʟᴇᴍꜱ.\n\n"
        "<b>ᴍʏ ᴏᴡɴᴇʀ:</b> <a href=\"https://t.me/Its_Lozo\">@ɪᴛꜱ_ʟᴏᴢᴏ</a>"
    )


def about_caption():
    return (
        "<b>ᴀʙᴏᴜᴛ ᴜꜱ..</b>\n\n"
        "➤ ᴍᴀᴅᴇ ꜰᴏʀ : <a href=\"https://t.me/Anime_Hub_94\">ᴀɴɪᴍᴇ ʜᴜʙ</a>\n"
        "➤ ᴏᴡɴᴇʀ : <a href=\"https://t.me/Its_Lozo\">@ɪᴛꜱ_ʟᴏᴢᴏ</a>\n"
        "➤ ᴅᴇᴠᴇʟᴏᴘᴇʀ : <a href=\"https://t.me/Its_Lozo\">@ɪᴛꜱ_ʟᴏᴢᴏ</a>\n\n"
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
            pass
    try:
        await query.edit_message_text(start_caption(), parse_mode="HTML", reply_markup=start_keyboard())
    except Exception:
        pass


async def edit_about(query):
    try:
        await query.edit_message_caption(caption=about_caption(), parse_mode="HTML", reply_markup=about_keyboard())
    except Exception:
        await query.edit_message_text(about_caption(), parse_mode="HTML", reply_markup=about_keyboard())


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


def main_link_url(token):
    return f"https://t.me/{BOT_USERNAME}?start=link_{token}"


def share_url(url):
    return f"https://t.me/share/url?url={quote(url, safe='')}"


def admin_ok(uid):
    return uid == OWNER_ID or db.is_admin(uid)


def settings_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🖼️ ꜱᴇᴛ ɪᴍᴀɢᴇ", callback_data="set_image"), InlineKeyboardButton("🗑️ ᴀᴜᴛᴏ ᴅᴇʟᴇᴛᴇ", callback_data="auto_delete")],
        [InlineKeyboardButton("👮 ᴀᴅᴍɪɴꜱ", callback_data="admins"), InlineKeyboardButton("📢 ꜰꜱᴜʙ", callback_data="fsub")],
        [InlineKeyboardButton("✖️ ᴄʟᴏꜱᴇ", callback_data="settings_close")],
    ])


async def genlink(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not admin_ok(uid):
        return await update.message.reply_text("❌ ʏᴏᴜ ᴀʀᴇ ɴᴏᴛ ᴀᴜᴛʜᴏʀɪᴢᴇᴅ.")
    replied = update.message.reply_to_message
    if not replied:
        return await update.message.reply_text("ʀᴇᴘʟʏ ᴛᴏ ᴀɴʏ ᴍᴇꜱꜱᴀɢᴇ ᴀɴᴅ ᴜꜱᴇ /ɢᴇɴʟɪɴᴋ.")
    try:
        main = db.create_main_link("pending")
        url = main_link_url(main)
        markup = InlineKeyboardMarkup([[InlineKeyboardButton("↗ ꜱʜᴀʀᴇ ᴜʀʟ", url=share_url(url))]])
        copied = await context.bot.copy_message(chat_id=DB_CHANNEL_ID, from_chat_id=replied.chat_id, message_id=replied.message_id, reply_markup=markup)
        target = f"message:{DB_CHANNEL_ID}:{copied.message_id}"
        db.db.table("main_links").update({"target": target}).eq("token", main).execute()
        await update.message.reply_text(f"✅ <b>ɢᴇɴʟɪɴᴋ ɢᴇɴᴇʀᴀᴛᴇᴅ</b>\n\n{url}", parse_mode="HTML", disable_web_page_preview=True, reply_markup=markup)
    except Exception:
        log.exception("Genlink failed")
        await update.message.reply_text("❌ ɢᴇɴʟɪɴᴋ ɢᴇɴᴇʀᴀᴛᴇ ɴᴀʜɪ ʜᴜᴀ.")


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
    if not admin_ok(uid):
        return await update.message.reply_text("❌ ʏᴏᴜ ᴀʀᴇ ɴᴏᴛ ᴀᴜᴛʜᴏʀɪᴢᴇᴅ.")
    if len(context.args) != 2:
        return await update.message.reply_text("ᴜꜱᴀɢᴇ:\n/ʙᴀᴛᴄʜ <ꜰɪʀꜱᴛ ᴅʙ ʟɪɴᴋ> <ʟᴀꜱᴛ ᴅʙ ʟɪɴᴋ>")
    first, last = parse_message_link(context.args[0]), parse_message_link(context.args[1])
    if not first or not last:
        return await update.message.reply_text("❌ ɪɴᴠᴀʟɪᴅ ᴛᴇʟᴇɢʀᴀᴍ ᴘᴏꜱᴛ ʟɪɴᴋ.")
    try:
        first_channel = (await context.bot.get_chat(first[0])).id if isinstance(first[0], str) else first[0]
        last_channel = (await context.bot.get_chat(last[0])).id if isinstance(last[0], str) else last[0]
    except Exception:
        return await update.message.reply_text("❌ ᴄᴏᴜʟᴅ ɴᴏᴛ ʀᴇꜱᴏʟᴠᴇ ᴛʜᴇ ᴅʙ ᴄʜᴀɴɴᴇʟ.")
    if first_channel != DB_CHANNEL_ID or last_channel != DB_CHANNEL_ID:
        return await update.message.reply_text("❌ ʙᴏᴛʜ ʟɪɴᴋꜱ ᴍᴜꜱᴛ ʙᴇ ꜰʀᴏᴍ ᴛʜᴇ ᴅʙ ᴄʜᴀɴɴᴇʟ.")
    lo, hi = sorted((first[1], last[1]))
    rows = db.list_files_between(DB_CHANNEL_ID, lo, hi)
    if not rows:
        return await update.message.reply_text("❌ ɴᴏ ᴅʙ ᴘᴏꜱᴛꜱ ꜰᴏᴜɴᴅ ɪɴ ᴛʜɪꜱ ʀᴀɴɢᴇ.")
    batch_id = db.create_batch([r["file_id"] for r in rows])
    main = db.create_main_link(f"batch:{batch_id}")
    url = main_link_url(main)
    markup = InlineKeyboardMarkup([[InlineKeyboardButton("↗ ꜱʜᴀʀᴇ ᴜʀʟ", url=share_url(url))]])
    try:
        await context.bot.send_message(DB_CHANNEL_ID, "📦 <b>ʙᴀᴛᴄʜ ꜱʜᴀʀᴇ ᴜʀʟ</b>", parse_mode="HTML", reply_markup=markup)
    except Exception:
        pass
    await update.message.reply_text(f"✅ <b>ʙᴀᴛᴄʜ ʟɪɴᴋ ɢᴇɴᴇʀᴀᴛᴇᴅ</b>\n\nɪᴛᴇᴍꜱ: <b>{len(rows)}</b>\n\n{url}", parse_mode="HTML", disable_web_page_preview=True, reply_markup=markup)


async def send_download_page(message, short_url):
    image = start_image()
    caption = (
        "<i>📊 ʜᴇʏ ʙʀᴏ/ꜱɪꜱ,</i>\n\n"
        "➜ ʏᴏᴜʀ ʟɪɴᴋ ɪꜱ ʀᴇᴀᴅʏ, ᴋɪɴᴅʟʏ ᴄʟɪᴄᴋ ᴏɴ\n"
        "ᴅᴏᴡɴʟᴏᴀᴅ ʙᴜᴛᴛᴏɴ! 👇\n\n"
        "ᴛᴏ ʙᴜʏ ᴘʀᴇᴍɪᴜᴍ, ᴄᴏɴᴛᴀᴄᴛ: <a href=\"https://t.me/Its_Lozo\">@ɪᴛꜱ_ʟᴏᴢᴏ</a>"
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
            pass
    await message.reply_text(caption, parse_mode="HTML", reply_markup=keyboard)


async def deliver_target(update, target):
    delivered = []
    if target.startswith("message:"):
        _, chat_id, message_id = target.split(":", 2)
        m = await update.get_bot().copy_message(chat_id=update.effective_chat.id, from_chat_id=int(chat_id), message_id=int(message_id))
        delivered.append(m.message_id)
    elif target.startswith("batch:"):
        batch_id = target.split(":", 1)[1]
        rows = db.get_batch_items(batch_id)
        if not rows:
            await update.message.reply_text("❌ ʙᴀᴛᴄʜ ɴᴏᴛ ꜰᴏᴜɴᴅ.")
            return []
        for row in rows:
            try:
                m = await update.get_bot().copy_message(chat_id=update.effective_chat.id, from_chat_id=row["channel_id"], message_id=row["message_id"])
                delivered.append(m.message_id)
                await asyncio.sleep(0.08)
            except Exception:
                log.exception("Batch item delivery failed")
    else:
        await update.message.reply_text("❌ ɪɴᴠᴀʟɪᴅ ʟɪɴᴋ ᴛᴀʀɢᴇᴛ.")
    return delivered


async def schedule_auto_delete(context, chat_id, message_ids, minutes):
    if minutes <= 0 or not message_ids:
        return
    context.job_queue.run_once(delete_delivered, when=minutes * 60, data={"chat_id": chat_id, "message_ids": message_ids})


async def delete_delivered(context):
    data = context.job.data
    for mid in data["message_ids"]:
        try:
            await context.bot.delete_message(data["chat_id"], mid)
        except Exception:
            pass


def auto_delete_minutes():
    try:
        return max(0, int(db.get_setting("auto_delete_minutes", "10")))
    except Exception:
        return 10


async def deliver_and_notify(update, context, target):
    ids = await deliver_target(update, target)
    if not ids:
        return
    mins = auto_delete_minutes()
    if mins > 0:
        notice = await update.message.reply_text(
            f"⚠️ <b>ᴛʜɪꜱ ꜰɪʟᴇ ɪꜱ ᴅᴇʟᴇᴛɪɴɢ ᴀᴜᴛᴏᴍᴀᴛɪᴄᴀʟʟʏ ɪɴ {mins} ᴍɪɴᴜᴛᴇꜱ.</b>\n\nꜰᴏʀᴡᴀʀᴅ ɪᴛ ᴛᴏ ʏᴏᴜʀ ꜱᴀᴠᴇᴅ ᴍᴇꜱꜱᴀɢᴇꜱ..!"
        )
        ids.append(notice.message_id)
        await schedule_auto_delete(context, update.effective_chat.id, ids, mins)


async def verify(update, context, token):
    uid = update.effective_user.id
    missing = await is_fsub_member(context.bot, uid)
    if missing:
        return await update.message.reply_text("⚡ <b>ᴊᴏɪɴ ʀᴇǫᴜɪʀᴇᴅ</b>\n\nᴊᴏɪɴ ᴀʟʟ ʀᴇǫᴜɪʀᴇᴅ ᴄʜᴀɴɴᴇʟꜱ ᴛʜᴇɴ ᴛᴀᴘ ᴄʜᴇᴄᴋ ᴊᴏɪɴ.", parse_mode="HTML", reply_markup=fsub_keyboard(missing))
    row = db.get_token(token)
    if not row or int(row["user_id"]) != uid:
        return await update.message.reply_text("❌ ᴛʜɪꜱ ꜱʜᴏʀᴛᴇɴᴇʀ ʟɪɴᴋ ɪꜱ ɴᴏᴛ ᴍᴀᴅᴇ ꜰᴏʀ ʏᴏᴜ.")
    target = db.consume_token(token, uid)
    if not target:
        return await update.message.reply_text("❌ ᴛʜɪꜱ ꜱʜᴏʀᴛᴇɴᴇʀ ʟɪɴᴋ ɪꜱ ᴇxᴘɪʀᴇᴅ ᴏʀ ᴀʟʀᴇᴀᴅʏ ᴜꜱᴇᴅ.")
    await deliver_and_notify(update, context, target)


async def open_main(update, context, token):
    uid = update.effective_user.id
    row = db.get_main_link(token)
    if not row:
        return await update.message.reply_text("❌ ᴛʜɪꜱ ʟɪɴᴋ ɪꜱ ɴᴏᴛ ᴠᴀʟɪᴅ.")
    missing = await is_fsub_member(context.bot, uid)
    if missing:
        return await update.message.reply_text("⚡ <b>ᴊᴏɪɴ ʀᴇǫᴜɪʀᴇᴅ</b>\n\nᴊᴏɪɴ ᴀʟʟ ʀᴇǫᴜɪʀᴇᴅ ᴄʜᴀɴɴᴇʟꜱ ᴛʜᴇɴ ᴛᴀᴘ ᴄʜᴇᴄᴋ ᴊᴏɪɴ.", parse_mode="HTML", reply_markup=fsub_keyboard(missing))
    if db.is_premium(uid):
        await deliver_and_notify(update, context, row["target"])
        return
    verify_token = db.create_token(uid, row["target"], hours=2)
    short_url = shortener.create_from_token(verify_token, BOT_USERNAME)
    if not short_url:
        return await update.message.reply_text("⚠️ ꜱʜᴏʀᴛᴇɴᴇʀ ɪꜱ ɴᴏᴛ ᴄᴏɴꜰɪɢᴜʀᴇᴅ ᴄᴏʀʀᴇᴄᴛʟʏ.")
    await send_download_page(update.message, short_url)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    db.add_user(user.id, user.username or "", user.first_name or "")
    if db.is_banned(user.id):
        return await update.message.reply_text("🚫 ʏᴏᴜ ᴀʀᴇ ʙᴀɴɴᴇᴅ.")
    if context.args:
        arg = context.args[0]
        if arg.startswith("verify_"):
            return await verify(update, context, arg[7:])
        if arg.startswith("link_"):
            return await open_main(update, context, arg[5:])
    await render_start(update.message)


async def callback(update, context):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    if q.data == "close" or q.data == "settings_close":
        try: await q.message.delete()
        except Exception: pass
        return
    if q.data == "about": return await edit_about(q)
    if q.data == "back": return await edit_start(q)
    if q.data == "check_fsub":
        missing = await is_fsub_member(context.bot, uid)
        if missing: return await q.answer("❌ ᴊᴏɪɴ ᴀʟʟ ᴄʜᴀɴɴᴇʟꜱ ꜰɪʀꜱᴛ.", show_alert=True)
        try: await q.message.delete()
        except Exception: pass
        return await render_start(q.message)
    if not admin_ok(uid): return await q.answer("❌ ᴜɴᴀᴜᴛʜᴏʀɪᴢᴇᴅ", show_alert=True)
    if q.data == "set_image":
        _pending_image.add(uid); return await q.message.reply_text("🖼️ ʟᴇᴛᴍᴇ ʜᴀᴠᴇ ᴛʜᴇ ɪᴍᴀɢᴇ ʏᴏᴜ ᴡᴀɴᴛ ᴛᴏ ᴜꜱᴇ ᴀꜱ ꜱᴛᴀʀᴛ ɪᴍᴀɢᴇ.")
    if q.data == "auto_delete":
        cur = auto_delete_minutes(); _pending_autodelete.add(uid)
        return await q.message.reply_text(f"🗑️ ᴀᴜᴛᴏ ᴅᴇʟᴇᴛᴇ ᴛɪᴍᴇ: <b>{cur} ᴍɪɴᴜᴛᴇꜱ</b>\n\nꜱᴇɴᴅ ᴛʜᴇ ɴᴇᴡ ᴛɪᴍᴇ ɪɴ ᴍɪɴᴜᴛᴇꜱ.\nꜱᴇɴᴅ <code>0</code> ᴛᴏ ᴅɪꜱᴀʙʟᴇ.", parse_mode="HTML")
    if q.data == "admins":
        lines = ["<b>👮 ᴀᴅᴍɪɴꜱ</b>", "", f"• <a href=\"tg://user?id={OWNER_ID}\">ᴏᴡɴᴇʀ</a> — <code>{OWNER_ID}</code>"]
        for a in db.list_admins():
            name = a["first_name"] or ("@" + a["username"] if a["username"] else "ᴀᴅᴍɪɴ")
            lines.append(f"• <a href=\"tg://user?id={a['user_id']}\">{name}</a> — <code>{a['user_id']}</code>")
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("➕ ᴀᴅᴅ ᴀᴅᴍɪɴ", callback_data="add_admin"), InlineKeyboardButton("➖ ʀᴇᴍᴏᴠᴇ", callback_data="remove_admin")], [InlineKeyboardButton("↩️ ʙᴀᴄᴋ", callback_data="settings_back")]])
        return await q.message.edit_text("\n".join(lines), parse_mode="HTML", reply_markup=kb)
    if q.data == "add_admin": _pending_admin.add(uid); return await q.message.reply_text("➕ ꜱᴇɴᴅ ᴛʜᴇ ᴜꜱᴇʀ ɪᴅ ᴛᴏ ᴀᴅᴅ ᴀꜱ ᴀᴅᴍɪɴ.")
    if q.data == "remove_admin": _pending_admin.add(-uid); return await q.message.reply_text("➖ ꜱᴇɴᴅ ᴛʜᴇ ᴜꜱᴇʀ ɪᴅ ᴛᴏ ʀᴇᴍᴏᴠᴇ ᴀɴ ᴀᴅᴍɪɴ.")
    if q.data == "fsub":
        rows = db.list_fsub(); lines = ["<b>📢 ꜰꜱᴜʙ ᴄʜᴀɴɴᴇʟꜱ</b>", ""]
        for r in rows: lines.append(f"• {r.get('title','CHANNEL')} — <code>{r['channel_id']}</code>")
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("➕ ᴀᴅᴅ ꜰꜱᴜʙ", callback_data="add_fsub")], [InlineKeyboardButton("↩️ ʙᴀᴄᴋ", callback_data="settings_back")]])
        return await q.message.edit_text("\n".join(lines), parse_mode="HTML", reply_markup=kb)
    if q.data == "add_fsub": _pending_fsub.add(uid); return await q.message.reply_text("📢 ꜱᴇɴᴅ: <code>CHANNEL_ID INVITE_LINK TITLE</code>", parse_mode="HTML")
    if q.data == "settings_back": return await q.message.edit_text("<b>⚙️ ꜱᴇᴛᴛɪɴɢꜱ</b>\n\nᴄʜᴏᴏꜱᴇ ᴀɴ ᴏᴘᴛɪᴏɴ.", parse_mode="HTML", reply_markup=settings_keyboard())


async def settings(update, context):
    if not admin_ok(update.effective_user.id): return await update.message.reply_text("❌ ᴜɴᴀᴜᴛʜᴏʀɪᴢᴇᴅ.")
    await update.message.reply_text("<b>⚙️ ꜱᴇᴛᴛɪɴɢꜱ</b>\n\nᴄʜᴏᴏꜱᴇ ᴀɴ ᴏᴘᴛɪᴏɴ.", parse_mode="HTML", reply_markup=settings_keyboard())


async def settings_input(update, context):
    uid = update.effective_user.id
    if not admin_ok(uid): return
    if uid in _pending_image and update.message.photo:
        db.set_setting("start_image", update.message.photo[-1].file_id); _pending_image.discard(uid)
        return await update.message.reply_text("✅ ꜱᴛᴀʀᴛ ɪᴍᴀɢᴇ ᴜᴘᴅᴀᴛᴇᴅ ꜱᴜᴄᴄᴇꜱꜱꜰᴜʟʟʏ.")
    if uid in _pending_autodelete:
        try:
            minutes = int(update.message.text.strip()); db.set_setting("auto_delete_minutes", minutes); _pending_autodelete.discard(uid)
            return await update.message.reply_text(f"✅ ᴀᴜᴛᴏ ᴅᴇʟᴇᴛᴇ ꜱᴇᴛ ᴛᴏ {minutes} ᴍɪɴᴜᴛᴇꜱ.")
        except Exception: return await update.message.reply_text("❌ ꜱᴇɴᴅ ᴀ ᴠᴀʟɪᴅ ɴᴜᴍʙᴇʀ.")
    if uid in _pending_admin or -uid in _pending_admin:
        try: target = int(update.message.text.strip())
        except Exception: return await update.message.reply_text("❌ ᴇɴᴛᴇʀ ᴀ ᴠᴀʟɪᴅ ᴜꜱᴇʀ ɪᴅ.")
        remove = -uid in _pending_admin; _pending_admin.discard(uid); _pending_admin.discard(-uid)
        if remove: db.remove_admin(target); return await update.message.reply_text("✅ ᴀᴅᴍɪɴ ʀᴇᴍᴏᴠᴇᴅ.")
        db.add_admin(target); return await update.message.reply_text("✅ ᴀᴅᴍɪɴ ᴀᴅᴅᴇᴅ.")
    if uid in _pending_fsub:
        parts = update.message.text.split(maxsplit=2)
        if len(parts) < 2: return await update.message.reply_text("❌ ᴜꜱᴇ: CHANNEL_ID INVITE_LINK TITLE")
        title = parts[2] if len(parts) > 2 else parts[0]
        db.add_fsub(parts[0], parts[1], title); _pending_fsub.discard(uid)
        return await update.message.reply_text("✅ ꜰꜱᴜʙ ᴄʜᴀɴɴᴇʟ ᴀᴅᴅᴇᴅ.")


async def addsubs(update, context):
    if not admin_ok(update.effective_user.id): return await update.message.reply_text("❌ ᴜɴᴀᴜᴛʜᴏʀɪᴢᴇᴅ.")
    if len(context.args) != 2: return await update.message.reply_text("ᴜꜱᴀɢᴇ: /ᴀᴅᴅꜱᴜʙꜱ USER_ID DAYS")
    try:
        expiry = db.add_premium(int(context.args[0]), int(context.args[1]))
        await update.message.reply_text(f"✅ ᴘʀᴇᴍɪᴜᴍ ᴀᴅᴅᴇᴅ.\n\nᴜꜱᴇʀ: <code>{context.args[0]}</code>\nᴇxᴘɪʀʏ: <code>{expiry.isoformat()}</code>", parse_mode="HTML")
    except Exception as e: await update.message.reply_text(f"❌ {e}")


async def removesubs(update, context):
    if not admin_ok(update.effective_user.id): return await update.message.reply_text("❌ ᴜɴᴀᴜᴛʜᴏʀɪᴢᴇᴅ.")
    if len(context.args) != 1: return await update.message.reply_text("ᴜꜱᴀɢᴇ: /ʀᴇᴍᴏᴠᴇꜱᴜʙꜱ USER_ID")
    db.remove_premium(int(context.args[0])); await update.message.reply_text(f"✅ ᴘʀᴇᴍɪᴜᴍ ʀᴇᴍᴏᴠᴇᴅ.\n\nᴜꜱᴇʀ: <code>{context.args[0]}</code>", parse_mode="HTML")


async def channel_post_indexer(update, context):
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
