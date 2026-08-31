import os
import logging
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters
from telegram.constants import ParseMode

from database import Database
from shortener import Shortener

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)
BOT_TOKEN = os.environ["BOT_TOKEN"]
OWNER_ID = int(os.environ["OWNER_ID"])
DB_CHANNEL_ID = int(os.environ["DB_CHANNEL_ID"])
BOT_USERNAME = os.environ["BOT_USERNAME"].lstrip("@")

db = Database(os.getenv("DATABASE_PATH", "bot.db"))
shortener = Shortener(
    os.getenv("SHORTENER_API_URL", ""),
    os.getenv("SHORTENER_API_KEY", ""),
    BOT_USERNAME,
    db,
)

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

def is_admin(user_id: int) -> bool:
    return user_id == OWNER_ID or db.is_admin(user_id)
def is_owner(user_id: int) -> bool:
    return user_id == OWNER_ID

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    arg = context.args[0] if context.args else ""
    if arg.startswith("file_"):
        await deliver_file(update, context, arg[5:])
        return
    if arg.startswith("batch_"):
        await deliver_batch(update, context, arg[6:])
        return
    await update.message.reply_text(
        "👋 Welcome!\n\n"
        "Use a file/batch link to receive files.\n"
        "Use /help to see commands."
    )
async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📚 Commands\n\n"
        "/get <file_id> — get a file\n"
        "/batch <file_id> ... — create a batch (admin)\n"
        "/files — list recent files (admin)\n"
        "/batches — list batches (admin)\n"
        "/addsub <user_id> <days> — add subscription\n"
        "/remsub <user_id> — remove subscription\n"
        "/addadmin <user_id> — owner only\n"
        "/removeadmin <user_id> — owner only\n"
        "/admins — list admins\n"
        "/stats — statistics (admin)\n\n"
        "To store files: send/reply with a document, video, audio or photo "
        "in this chat and use /save on the message."
    )
async def save_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    msg = update.message.reply_to_message
    if not msg:
        await update.message.reply_text("Reply to a file message with /save.")
        return
    try:
        copied = await context.bot.copy_message(
            chat_id=DB_CHANNEL_ID,
            from_chat_id=msg.chat_id,
            message_id=msg.message_id,
        )
        file_id = db.add_file(DB_CHANNEL_ID, copied.message_id, msg.caption or "")
        link = f"https://t.me/{BOT_USERNAME}?start=file_{file_id}"
        sent = await update.message.reply_text(
    f"✅ File saved.\n\n🆔 {file_id}\n🔗 {link}"
)

await asyncio.sleep(600)
try:
    await sent.delete()
except Exception:
    pass
    except Exception as e:
        log.exception("save failed")
        await update.message.reply_text(f"❌ Could not save file: {e}")
async def get_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) != 1:
        await update.message.reply_text("Usage: /get <file_id>")
        return
    await deliver_file(update, context, context.args[0])

async def deliver_file(update: Update, context: ContextTypes.DEFAULT_TYPE, file_id: str):
    row = db.get_file(file_id)
    if not row:
        await update.effective_message.reply_text("❌ File not found.")
        return
    uid = update.effective_user.id
    if db.is_subscribed(uid):
        await copy_stored_message(context, update.effective_chat.id, row)
        return

    short_url = shortener.create(file_id=file_id, user_id=uid)
    if not short_url:
        await update.effective_message.reply_text(
            "⚠️ Shortener is not configured correctly."
        )
        return
    await update.effective_message.reply_text(
        "🔐 You are not subscribed.\n"
        "Complete the shortener first, then open the verification link to receive the file.",
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("🔗 Continue", url=short_url)]]
        ),
    )
async def deliver_batch(update: Update, context: ContextTypes.DEFAULT_TYPE, batch_id: str):
    items = db.get_batch_items(batch_id)
    if not items:
        await update.effective_message.reply_text("❌ Batch not found or empty.")
        return

    uid = update.effective_user.id
    if db.is_subscribed(uid):
        await send_batch(context, update.effective_chat.id, items)
        return
    short_url = shortener.create(file_id=f"batch:{batch_id}", user_id=uid)
    if not short_url:
        await update.effective_message.reply_text("⚠️ Shortener is not configured correctly.")
        return

    await update.effective_message.reply_text(
        f"📦 This batch contains {len(items)} files.\n"
        "Complete the shortener to unlock the batch.",
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("🔗 Unlock Batch", url=short_url)]]
        ),
    )
async def verify_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) != 1:
        await update.message.reply_text("Usage: /verify <token>")
        return

    payload = shortener.verify(context.args[0])
    if not payload:
        await update.message.reply_text("❌ Invalid or expired token.")
        return

    uid, target = payload
    if uid != update.effective_user.id:
        await update.message.reply_text("❌ This token belongs to another user.")
        return
    if target.startswith("batch:"):
        items = db.get_batch_items(target[6:])
        await send_batch(context, update.effective_chat.id, items)
    else:
        row = db.get_file(target)
        if row:
            await copy_stored_message(context, update.effective_chat.id, row)
        else:
            await update.message.reply_text("❌ File not found.")
async def send_batch(context, chat_id, items):
    for row in items:
        try:
            await copy_stored_message(context, chat_id, row)
        except Exception:
            log.exception("batch delivery failed")

async def copy_stored_message(context, chat_id, row):
    await context.bot.copy_message(
        chat_id=chat_id,
        from_chat_id=row["channel_id"],
        message_id=row["message_id"],
    )
async def batch_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    if not context.args:
        await update.message.reply_text("Usage: /batch <file_id> <file_id> ...")
        return

    valid = [x for x in context.args if db.get_file(x)]
    if not valid:
        await update.message.reply_text("❌ No valid file IDs.")
        return
    batch_id = db.create_batch(valid)
    link = f"https://t.me/{BOT_USERNAME}?start=batch_{batch_id}"
    await update.message.reply_text(
        f"📦 Batch created: {len(valid)} files\n\n"
        f"🆔 `{batch_id}`\n🔗 {link}",
        parse_mode=ParseMode.MARKDOWN,
    )
async def addsub_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    if len(context.args) != 2:
        await update.message.reply_text("Usage: /addsub <user_id> <days>")
        return
    try:
        uid, days = int(context.args[0]), int(context.args[1])
        db.add_subscription(uid, days)
        await update.message.reply_text(f"✅ Subscription added for {uid} for {days} days.")
    except ValueError:
        await update.message.reply_text("❌ User ID and days must be numbers.")
async def remsub_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    if len(context.args) != 1:
        await update.message.reply_text("Usage: /remsub <user_id>")
        return
    db.remove_subscription(int(context.args[0]))
    await update.message.reply_text("✅ Subscription removed.")
async def addadmin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        return
    if len(context.args) != 1:
        await update.message.reply_text("Usage: /addadmin <user_id>")
        return
    db.add_admin(int(context.args[0]))
    await update.message.reply_text("✅ Admin added.")
async def removeadmin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        return
    if len(context.args) != 1:
        await update.message.reply_text("Usage: /removeadmin <user_id>")
        return
    db.remove_admin(int(context.args[0]))
    await update.message.reply_text("✅ Admin removed.")
async def admins_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    admins = db.list_admins()
    text = f"👑 Owner: `{OWNER_ID}`\n"
    text += "\n".join(f"• `{x}`" for x in admins) if admins else "• No additional admins"
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)
async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    s = db.stats()
    await update.message.reply_text(
        f"📊 Stats\n\n"
        f"Files: {s['files']}\n"
        f"Batches: {s['batches']}\n"
        f"Admins: {s['admins']}\n"
        f"Active subscriptions: {s['subscriptions']}"
    )

def main():
    health_thread = threading.Thread(target=start_health_server, daemon=True)
    health_thread.start()

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("save", save_cmd))
    app.add_handler(CommandHandler("get", get_cmd))
    app.add_handler(CommandHandler("verify", verify_cmd))
    app.add_handler(CommandHandler("batch", batch_cmd))
    app.add_handler(CommandHandler("addsub", addsub_cmd))
    app.add_handler(CommandHandler("remsub", remsub_cmd))
    app.add_handler(CommandHandler("addadmin", addadmin_cmd))
    app.add_handler(CommandHandler("removeadmin", removeadmin_cmd))
    app.add_handler(CommandHandler("admins", admins_cmd))
    app.add_handler(CommandHandler("stats", stats_cmd))
    log.info("Bot starting")
    app.run_polling()

if __name__ == "__main__":
    main()
