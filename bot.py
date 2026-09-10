import os
import logging
import asyncio
import re
import threading
from urllib.parse import quote
from http.server import BaseHTTPRequestHandler, HTTPServer

from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto
from telegram.constants import ChatMemberStatus
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    ContextTypes, MessageHandler, filters
)

from database import Database
from shortener import Shortener

load_dotenv()
logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("file-store-bot")


# Screenshot-style Unicode small-cap font. This changes visible text and button
# labels without changing HTML tags or URLs.
_FONT = str.maketrans({
    **dict(zip("abcdefghijklmnopqrstuvwxyz", "ᴀʙᴄᴅᴇꜰɢʜɪᴊᴋʟᴍɴᴏᴘǫʀꜱᴛᴜᴠᴡxʏᴢ")),
    **dict(zip("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "ᴀʙᴄᴅᴇꜰɢʜɪᴊᴋʟᴍɴᴏᴘǫʀꜱᴛᴜᴠᴡxʏᴢ")),
})

def font(text):
    return text.translate(_FONT)

def font_html(text):
    parts = re.split(r"(<[^>]+>)", text)
    return "".join(p if p.startswith("<") else font(p) for p in parts)
# Never expose Telegram Bot API URLs (they contain the bot token).
# httpx/httpcore INFO logging can print the full request URL.
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


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"Bot is running!")

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
    return font_html(
        "<i>Hi There....! 💥</i>\n\n"
        "I am a file-store bot.\n"
        "I can generate links directly with no problems.\n\n"
        "<b>My Owner:</b> <a href=\"https://t.me/Its_Lozo\">@Its_Lozo</a>"
    )


def about_caption():
    return font_html(
        "<b><i>About Us..</i></b>\n\n"
        "➤ Made for : <a href=\"https://t.me/Anime_Hub_94\">Anime Hub</a>\n"
        "➤ Owner : <a href=\"https://t.me/Its_Lozo\">@Its_Lozo</a>\n"
        "➤ Developer : <a href=\"https://t.me/Its_Lozo\">@Its_Lozo</a>\n\n"
        "Adios !!"
    )


def start_keyboard():
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(font("ABOUT"), callback_data="about"),
        InlineKeyboardButton(font("CLOSE"), callback_data="close"),
    ]])


def about_keyboard():
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(font("BACK"), callback_data="back"),
        InlineKeyboardButton(font("CLOSE"), callback_data="close"),
    ]])


async def render_start(message):
    image = start_image()
    if image:
        try:
            await message.reply_photo(
                photo=image,
                caption=start_caption(),
                reply_markup=start_keyboard(),
            )
            return
        except Exception:
            log.exception("Could not send configured start image")
    await message.reply_text(start_caption(), reply_markup=start_keyboard())


async def edit_start(query):
    image = start_image()
    if image:
        try:
            await query.edit_message_media(
                media=InputMediaPhoto(media=image, caption=start_caption()),
                reply_markup=start_keyboard(),
            )
            return
        except Exception:
            log.exception("Could not restore start image")
    try:
        await query.edit_message_text(
            text=start_caption(), reply_markup=start_keyboard()
        )
    except Exception:
        pass


async def edit_about(query):
    # If the start message contains a photo, keep the same image while changing
    # only its caption, matching the screenshot flow.
    try:
        await query.edit_message_caption(
            caption=about_caption(),
            parse_mode="HTML",
            reply_markup=about_keyboard(),
        )
    except Exception:
        await query.edit_message_text(
            text=about_caption(),
            parse_mode="HTML",
            reply_markup=about_keyboard(),
        )


async def is_fsub_member(bot, user_id):
    missing = []
    for row in db.list_fsub():
        try:
            member = await bot.get_chat_member(int(row["channel_id"]), user_id)
            if member.status in (ChatMemberStatus.LEFT, ChatMemberStatus.BANNED):
                missing.append(row)
        except Exception:
            # A channel that cannot be checked is treated as required.
            missing.append(row)
    return missing


def fsub_keyboard(rows):
    buttons = []
    for row in rows:
        if row.get("invite_link"):
            buttons.append([InlineKeyboardButton(
                font(f"JOIN {row.get('title') or 'CHANNEL'}"),
                url=row["invite_link"]
            )])
    buttons.append([InlineKeyboardButton(font("✅ CHECK JOIN"), callback_data="check_fsub")])
    return InlineKeyboardMarkup(buttons)


def token_link(token):
    return f"https://t.me/{BOT_USERNAME}?start=verify_{token}"


async def make_share_button(main_url):
    share_url = "https://telegram.me/share/url?url=" + quote(main_url, safe="")
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(font("↗ SHARE URL"), url=share_url)
    ]])


async def genlink(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid != OWNER_ID and not db.is_admin(uid):
        return await update.message.reply_text(font("❌ You are not authorized."))

    replied = update.message.reply_to_message
    if not replied:
        return await update.message.reply_text(font("Reply to any message/file and use /genlink."))

    # Always copy into the DB channel. This removes the forward header/tag and
    # gives every generated link a stable DB-channel source message.
    try:
        copied = await context.bot.copy_message(
            chat_id=DB_CHANNEL_ID,
            from_chat_id=replied.chat_id,
            message_id=replied.message_id,
        )
    except Exception:
        log.exception("GENLINK DB save failed")
        return await update.message.reply_text(font("❌ Could not save the message to the DB channel."))

    file_id = db.add_file(DB_CHANNEL_ID, copied.message_id, replied.caption or replied.text or "")
    main_url = bot_link(f"file_{file_id}")
    try:
        await context.bot.edit_message_reply_markup(
            chat_id=DB_CHANNEL_ID,
            message_id=copied.message_id,
            reply_markup=await make_share_button(main_url),
        )
    except Exception:
        log.exception("Could not add Share URL button")

    await update.message.reply_text(
        font_html(f"<b>🔗 Link generated!</b>\\n\\n{main_url}"),
        disable_web_page_preview=True,
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
    if uid != OWNER_ID and not db.is_admin(uid):
        return await update.message.reply_text(font("❌ You are not authorized."))

    if len(context.args) != 2:
        return await update.message.reply_text(font("Usage: /batch FIRST_DB_POST_LINK LAST_DB_POST_LINK"))

    first = parse_message_link(context.args[0])
    last = parse_message_link(context.args[1])
    if not first or not last:
        return await update.message.reply_text(font("❌ Invalid Telegram post link."))

    try:
        first_chat = await context.bot.get_chat(first[0]) if isinstance(first[0], str) else None
        last_chat = await context.bot.get_chat(last[0]) if isinstance(last[0], str) else None
        first_channel = first_chat.id if first_chat else first[0]
        last_channel = last_chat.id if last_chat else last[0]
    except Exception:
        return await update.message.reply_text(font("❌ Could not resolve the DB channel link."))

    if first_channel != DB_CHANNEL_ID or last_channel != DB_CHANNEL_ID:
        return await update.message.reply_text(font("❌ Both links must be posts from the configured DB channel."))

    lo, hi = sorted((first[1], last[1]))
    rows = db.list_files_between(DB_CHANNEL_ID, lo, hi)
    if not rows:
        return await update.message.reply_text(font("❌ No DB posts found between those two posts."))

    batch_id = db.create_batch([r["file_id"] for r in rows])
    main_url = bot_link(f"batch_{batch_id}")

    # The batch's main link is attached to the first DB post in the range.
    try:
        await context.bot.edit_message_reply_markup(
            chat_id=DB_CHANNEL_ID,
            message_id=rows[0]["message_id"],
            reply_markup=await make_share_button(main_url),
        )
    except Exception:
        log.exception("Could not add batch Share URL button")

    await update.message.reply_text(
        font_html(f"<b>✅ Batch created!</b>\\n\\n📦 Posts: <b>{len(rows)}</b>\\n\\n{main_url}"),
        disable_web_page_preview=True,
    )


async def send_download_page(message, short_url):
    image = start_image()
    caption = font_html(
        "<b>📊 HEY BRO/SIS,</b>\\n\\n"
        "➜ <b>YOUR LINK IS READY, KINDLY CLICK ON\\n"
        "DOWNLOAD BUTTON! 👇</b>\\n\\n"
        "<b>TO BUY PREMIUM, CONTACT: </b>"
        "<a href=\"https://t.me/Its_Lozo\">@Its_Lozo</a>"
    )
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(font("• CLICK HERE TO DOWNLOAD •"), url=short_url)],
        [
            InlineKeyboardButton(font("PREMIUM"), url="https://t.me/PremiumHub094"),
            InlineKeyboardButton(font("TUTORIAL"), url="https://t.me/Tutorial_Hub_94/4"),
        ],
    ])

    if image:
        try:
            await message.reply_photo(photo=image, caption=caption, parse_mode="HTML", reply_markup=keyboard)
            return
        except Exception:
            log.exception("Could not send download-page image")
    await message.reply_text(caption, parse_mode="HTML", reply_markup=keyboard)


async def deliver_target(update, target):
    if target.startswith("message:"):
        _, chat_id, message_id = target.split(":", 2)
        try:
            await context_bot_copy(update, int(chat_id), int(message_id))
        except Exception:
            await update.message.reply_text(font("❌ I could not access the original message anymore."))
        return

    if target.startswith("file:"):
        row = db.get_file(target.split(":", 1)[1])
        if not row:
            return await update.message.reply_text(font("❌ File not found."))
        try:
            await context_bot_copy(update, row["channel_id"], row["message_id"])
        except Exception:
            await update.message.reply_text(font("❌ I could not access the saved file."))
        return

    if target.startswith("batch:"):
        batch_id = target.split(":", 1)[1]
        rows = db.get_batch_items(batch_id)
        if not rows:
            return await update.message.reply_text(font("❌ Batch not found."))
        for row in rows:
            try:
                await context_bot_copy(update, row["channel_id"], row["message_id"])
                await asyncio.sleep(0.15)
            except Exception:
                log.exception("Batch item delivery failed")
        return

    await update.message.reply_text(font("❌ Invalid link target."))


async def context_bot_copy(update, chat_id, message_id):
    await update.get_bot().copy_message(
        chat_id=update.effective_chat.id,
        from_chat_id=chat_id,
        message_id=message_id,
    )


async def open_main_target(update, context, target):
    uid = update.effective_user.id
    if db.is_premium(uid):
        await deliver_target(update, target)
        return

    short_url = shortener.create(uid, target, BOT_USERNAME)
    if not short_url:
        await update.message.reply_text(font("⚠️ Shortener is not configured correctly."))
        return
    await send_download_page(update.message, short_url)


async def verify(update: Update, context: ContextTypes.DEFAULT_TYPE, token):
    uid = update.effective_user.id

    # FSUB is mandatory for everyone, including premium users.
    missing = await is_fsub_member(context.bot, uid)
    if missing:
        await update.message.reply_text(
            font_html("⚡ <b>JOIN REQUIRED</b>\\n\\nJoin all required channels first, then tap <b>CHECK JOIN</b>."),
            parse_mode="HTML",
            reply_markup=fsub_keyboard(missing),
        )
        return

    consumed = db.consume_token_for_user(token, uid)
    if not consumed:
        return await update.message.reply_text(font("❌ This shortener link is expired, already used, or belongs to another user."))

    _, target = consumed
    await deliver_target(update, target)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    db.add_user(user.id, user.username or "", user.first_name or "")

    if db.is_banned(user.id):
        return await update.message.reply_text(font("🚫 You are banned from using this bot."))

    arg = context.args[0] if context.args else ""

    if arg.startswith("verify_"):
        return await verify(update, context, arg[7:])

    # MAIN LINKS ARE REUSABLE. Only the shortener token generated after each
    # click is time-limited/single-use.
    if arg.startswith("file_"):
        if not await check_and_show_fsub(update, context):
            return
        file_id = arg[5:]
        target = f"file:{file_id}"
        return await open_main_target(update, context, target)

    if arg.startswith("batch_"):
        if not await check_and_show_fsub(update, context):
            return
        batch_id = arg[6:]
        if not db.get_batch_items(batch_id):
            return await update.message.reply_text(font("❌ Batch not found or empty."))
        return await open_main_target(update, context, f"batch:{batch_id}")

    await render_start(update.message)


async def check_and_show_fsub(update, context):
    missing = await is_fsub_member(context.bot, update.effective_user.id)
    if not missing:
        return True
    await update.message.reply_text(
        font_html("⚡ <b>JOIN REQUIRED</b>\\n\\nJoin all required channels first, then tap <b>CHECK JOIN</b>."),
        parse_mode="HTML",
        reply_markup=fsub_keyboard(missing),
    )
    return False


async def callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "close":
        try:
            await query.message.delete()
        except Exception:
            pass
        return

    if query.data == "about":
        await edit_about(query)
        return

    if query.data == "back":
        await edit_start(query)
        return

    if query.data == "check_fsub":
        missing = await is_fsub_member(context.bot, query.from_user.id)
        if missing:
            await query.answer(
                font("❌ You still need to join all required channels."),
                show_alert=True,
            )
        else:
            await query.answer(font("✅ FSUB completed."))
            await query.message.reply_text(
                "✅ Subscription check passed. Open your file link again."
            )
        return


async def settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid != OWNER_ID and not db.is_admin(uid):
        return await update.message.reply_text("❌ You are not authorized.")

    await update.message.reply_text(
        font_html(
            "<b>⚙️ SETTINGS</b>\n\n"
            "🖼️ Start Image\n"
            "Reply to any photo with <code>/setimage</code> to set it as the bot's "
            "start/download-page image.\n\n"
            "The image is saved in Supabase settings; no image URL variable is required."
        ),
        parse_mode="HTML",
    )


async def setimage(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid != OWNER_ID and not db.is_admin(uid):
        return

    replied = update.message.reply_to_message
    if not replied or not replied.photo:
        return await update.message.reply_text(
            "Reply to a photo and use /setimage."
        )

    db.set_setting("start_image", replied.photo[-1].file_id)
    await update.message.reply_text(font("✅ Start image updated successfully."))


async def addfsub(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid != OWNER_ID and not db.is_admin(uid):
        return
    if len(context.args) < 2:
        return await update.message.reply_text(
            "Usage:\n/addfsub <channel_id> <invite_link> [title]"
        )
    channel_id, invite = context.args[0], context.args[1]
    title = " ".join(context.args[2:]) or channel_id
    db.add_fsub(channel_id, invite, title)
    await update.message.reply_text("✅ FSUB channel added.")


async def delfsub(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid != OWNER_ID and not db.is_admin(uid):
        return
    if not context.args:
        return await update.message.reply_text("Usage: /delfsub <channel_id>")
    db.del_fsub(context.args[0])
    await update.message.reply_text("✅ FSUB channel removed.")


async def premium(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid != OWNER_ID and not db.is_admin(uid):
        return
    if len(context.args) < 2:
        return await update.message.reply_text("Usage: /premium <user_id> <days>")
    try:
        expiry = db.add_premium(int(context.args[0]), int(context.args[1]))
        await update.message.reply_text(
            f"✅ Premium active until {expiry.isoformat()}"
        )
    except Exception as e:
        await update.message.reply_text(f"❌ {e}")


async def unpremium(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid != OWNER_ID and not db.is_admin(uid):
        return
    if not context.args:
        return await update.message.reply_text("Usage: /unpremium <user_id>")
    db.remove_premium(int(context.args[0]))
    await update.message.reply_text("✅ Premium removed.")


async def channel_post_indexer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    post = update.channel_post
    if not post or post.chat_id != DB_CHANNEL_ID:
        return
    try:
        caption = post.caption or post.text or ""
        db.add_file(DB_CHANNEL_ID, post.message_id, caption)
    except Exception:
        log.exception("Could not index DB channel post")


def main():
    threading.Thread(target=start_health_server, daemon=True).start()
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("genlink", genlink))
    app.add_handler(CommandHandler("batch", batch))
    app.add_handler(CommandHandler("settings", settings))
    app.add_handler(CommandHandler("setimage", setimage))
    app.add_handler(CommandHandler("addfsub", addfsub))
    app.add_handler(CommandHandler("delfsub", delfsub))
    app.add_handler(CommandHandler("premium", premium))
    app.add_handler(CommandHandler("unpremium", unpremium))
    app.add_handler(CallbackQueryHandler(callback))

    # Receives channel_post updates and indexes the configured DB channel.
    app.add_handler(
        MessageHandler(filters.ALL, channel_post_indexer),
        group=10,
    )

    log.info("Bot starting")
    # One long-polling instance only. Do NOT run the same bot token in UptimeRobot.
    app.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True,
        close_loop=False,
    )


if __name__ == "__main__":
    main()
