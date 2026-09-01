import os
import asyncio
import logging
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import quote

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, ContextTypes
from telegram.error import TelegramError

from database import Database
from shortener import Shortener

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
log = logging.getLogger(__name__)

BOT_TOKEN = os.environ["BOT_TOKEN"]
OWNER_ID = int(os.environ["OWNER_ID"])
DB_CHANNEL_ID = int(os.environ["DB_CHANNEL_ID"])
BOT_USERNAME = os.environ["BOT_USERNAME"].lstrip("@")

db = Database(os.getenv("DATABASE_PATH", "bot.db"))
shortener = Shortener(
    os.getenv("SHORTENER_API_URL", "https://arolinks.com/api"),
    os.getenv("SHORTENER_API_KEY", ""),
    BOT_USERNAME,
    db,
)

DEFAULT_DELETE_SECONDS = int(os.getenv("DELETE_AFTER_SECONDS", "600"))


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"Bot is running!")

    def log_message(self, fmt, *args):
        return


def start_health_server():
    port = int(os.environ.get("PORT", "10000"))
    server = HTTPServer(("0.0.0.0", port), HealthHandler)
    log.info("Health server running on port %s", port)
    server.serve_forever()


def is_owner(uid):
    return uid == OWNER_ID


def is_admin(uid):
    return is_owner(uid) or db.is_admin(uid)


def admin_only(update):
    return is_admin(update.effective_user.id)


def owner_only(update):
    return is_owner(update.effective_user.id)


def bot_link(target):
    return f"https://t.me/{BOT_USERNAME}?start={target}"


async def send_text(update, text, **kwargs):
    msg = update.effective_message
    if msg:
        return await msg.reply_text(text, **kwargs)


async def delete_later(context, chat_id, message_ids, seconds):
    await asyncio.sleep(seconds)
    if not isinstance(message_ids, list):
        message_ids = [message_ids]
    for mid in message_ids:
        try:
            await context.bot.delete_message(chat_id, mid)
        except TelegramError:
            pass


async def copy_stored_message(context, chat_id, row):
    return await context.bot.copy_message(
        chat_id=chat_id,
        from_chat_id=row["channel_id"],
        message_id=row["message_id"],
    )


async def send_file(context, chat_id, row):
    file_msg = await copy_stored_message(context, chat_id, row)
    warning = await context.bot.send_message(
        chat_id=chat_id,
        text=f"⏳ This file will be automatically deleted in {DEFAULT_DELETE_SECONDS // 60} minutes.",
    )
    asyncio.create_task(
        delete_later(
            context,
            chat_id,
            [file_msg.message_id, warning.message_id],
            DEFAULT_DELETE_SECONDS,
        )
    )


async def send_batch(context, chat_id, items):
    ids = []
    for row in items:
        try:
            msg = await copy_stored_message(context, chat_id, row)
            ids.append(msg.message_id)
        except TelegramError:
            log.exception("Batch delivery failed")
    if not ids:
        await context.bot.send_message(chat_id, "❌ Could not deliver the batch.")
        return
    warning = await context.bot.send_message(
        chat_id,
        f"⏳ These {len(ids)} files will be automatically deleted in {DEFAULT_DELETE_SECONDS // 60} minutes.",
    )
    asyncio.create_task(
        delete_later(
            context,
            chat_id,
            ids + [warning.message_id],
            DEFAULT_DELETE_SECONDS,
        )
    )


async def check_fsub(update, context):
    channels = db.list_fsub()
    if not channels:
        return True

    uid = update.effective_user.id
    missing = []

    for ch in channels:
        try:
            member = await context.bot.get_chat_member(ch["channel_id"], uid)
            if member.status in ("left", "kicked"):
                missing.append(ch)
        except TelegramError as e:
            log.warning("FSUB check failed for %s: %s", ch["channel_id"], e)

    if not missing:
        return True

    rows = []
    for ch in missing:
        url = ch["invite_link"] or ch["username"]
        if url:
            rows.append([InlineKeyboardButton(f"📢 {ch['title']}", url=url)])

    rows.append([InlineKeyboardButton("✅ Check Subscription", callback_data="check_fsub")])

    await send_text(
        update,
        "🔒 Please join the required channel(s) first, then press Check Subscription.",
        reply_markup=InlineKeyboardMarkup(rows),
    )
    return False


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    db.add_user(uid, update.effective_user.username or "", update.effective_user.first_name or "")

    if db.is_banned(uid):
        await send_text(update, "🚫 You are blocked from using this bot.")
        return

    arg = context.args[0] if context.args else ""

    if arg.startswith("verify_"):
        await verify_token(update, context, arg[7:])
        return

    if not await check_fsub(update, context):
        return

    if arg.startswith("file_"):
        await deliver_file(update, context, arg[5:])
        return

    if arg.startswith("batch_"):
        await deliver_batch(update, context, arg[6:])
        return

    await send_text(
        update,
        "👋 Welcome!\n\n"
        "Send a valid file link to receive a file.\n"
        "Use /help to see available commands."
    )


async def help_cmd(update, context):
    await send_text(
        update,
        "📚 Commands\n\n"
        "/start - Wake up the bot and load active file links\n"
        "/my_plan - Check premium membership\n"
        "/request <text> - Submit a request\n"
        "/auto_del - Adjust auto-delete (Admins)\n"
        "/fsub_chnl - View force-sub channels (Admins)\n"
        "/add_banuser <id> - Ban user (Admins)\n"
        "/del_banuser <id> - Unban user (Admins)\n"
        "/banuser_list - List banned users (Admins)\n"
        "/add_premium <id> <days> - Add premium (Admins)\n"
        "/remove_premium <id> - Remove premium (Admins)\n"
        "/list_premium - List premium users (Admins)\n"
        "/add_fsub <channel_id> <invite_link> [title] - Add FSUB (Admins)\n"
        "/del_fsub <channel_id> - Remove FSUB (Admins)\n"
        "/add_admins <id> - Add admin (Owner)\n"
        "/del_admins <id> - Remove admin (Owner)\n"
        "/admin_list - List admins (Owner)\n"
        "/broadcast <text> - Broadcast (Admins)\n"
        "/pbroadcast <text> - Broadcast and pin in PM (Admins)\n"
        "/batch <file_id> ... - Create batch (Admins)\n"
        "/genlink <file_id> - Create share link (Admins)\n"
        "/cancel - Cancel setup\n"
        "/users - Total users (Owner)\n\n"
        "Legacy aliases also work: /save /get /verify /addsub /remsub /addadmin /removeadmin /admins /stats"
    )


async def save_cmd(update, context):
    if not admin_only(update):
        return
    msg = update.message.reply_to_message
    if not msg:
        await send_text(update, "Reply to a file/post with /save.")
        return

    try:
        copied = await context.bot.copy_message(
            chat_id=DB_CHANNEL_ID,
            from_chat_id=msg.chat_id,
            message_id=msg.message_id,
        )
        file_id = db.add_file(DB_CHANNEL_ID, copied.message_id, msg.caption or "")
        link = bot_link(f"file_{file_id}")
        share_url = "https://telegram.me/share/url?url=" + quote(link, safe="")
        keyboard = InlineKeyboardMarkup(
            [[InlineKeyboardButton("📤 Share Link", url=share_url)]]
        )

        try:
            await context.bot.edit_message_reply_markup(
                chat_id=DB_CHANNEL_ID,
                message_id=copied.message_id,
                reply_markup=keyboard,
            )
        except TelegramError as e:
            log.warning("Could not add share button: %s", e)

        await send_text(
            update,
            f"✅ File saved successfully!\n\n"
            f"🆔 File ID: {file_id}\n"
            f"🤖 Bot Link:\n{link}\n\n"
            f"📤 Share Link button added to DB channel."
        )
    except Exception as e:
        log.exception("SAVE ERROR")
        await send_text(update, f"❌ Could not save file: {e}")


async def genlink_cmd(update, context):
    if not admin_only(update):
        return
    if len(context.args) != 1:
        await send_text(update, "Usage: /genlink <file_id>")
        return
    fid = context.args[0]
    if not db.get_file(fid):
        await send_text(update, "❌ File not found.")
        return
    await send_text(update, f"🔗 Bot Link:\n{bot_link('file_' + fid)}")


async def get_cmd(update, context):
    if len(context.args) != 1:
        await send_text(update, "Usage: /get <file_id>")
        return
    await deliver_file(update, context, context.args[0])


async def deliver_file(update, context, file_id):
    if not await check_fsub(update, context):
        return

    row = db.get_file(file_id)
    if not row:
        await send_text(update, "❌ File not found.")
        return

    uid = update.effective_user.id
    if db.is_premium(uid):
        await send_file(context, update.effective_chat.id, row)
        return

    short_url = shortener.create(file_id, uid)
    if not short_url:
        await send_text(update, "⚠️ Shortener is not configured correctly.")
        return

    await send_text(
        update,
        "🔐 Continue to unlock this file.\n\n"
        "Complete the shortener, then Telegram will bring you back to the bot.",
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("🔗 Continue", url=short_url)]]
        ),
    )


async def deliver_batch(update, context, batch_id):
    if not await check_fsub(update, context):
        return

    items = db.get_batch_items(batch_id)
    if not items:
        await send_text(update, "❌ Batch not found or empty.")
        return

    uid = update.effective_user.id
    if db.is_premium(uid):
        await send_batch(context, update.effective_chat.id, items)
        return

    short_url = shortener.create(f"batch:{batch_id}", uid)
    if not short_url:
        await send_text(update, "⚠️ Shortener is not configured correctly.")
        return

    await send_text(
        update,
        f"📦 This batch contains {len(items)} files.\n\n"
        "Complete the shortener to unlock the batch.",
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("🔗 Continue", url=short_url)]]
        ),
    )


async def verify_token(update, context, token):
    payload = shortener.verify(token)
    if not payload:
        await send_text(update, "❌ Invalid, expired, or already-used token.")
        return

    token_uid, target = payload
    current_uid = update.effective_user.id

    # Shareable admin links use user_id=0; normal user links are bound to the user.
    if token_uid != 0 and token_uid != current_uid:
        await send_text(update, "❌ This verification link belongs to another user.")
        return

    if target.startswith("batch:"):
        items = db.get_batch_items(target[6:])
        if not items:
            await send_text(update, "❌ Batch not found or empty.")
            return
        await send_batch(context, update.effective_chat.id, items)
        return

    row = db.get_file(target)
    if not row:
        await send_text(update, "❌ File not found.")
        return
    await send_file(context, update.effective_chat.id, row)


async def verify_cmd(update, context):
    if len(context.args) != 1:
        await send_text(update, "Usage: /verify <token>")
        return
    await verify_token(update, context, context.args[0])


async def batch_cmd(update, context):
    if not admin_only(update):
        return
    if not context.args:
        await send_text(update, "Usage: /batch <file_id> <file_id> ...")
        return

    valid = [fid for fid in context.args if db.get_file(fid)]
    if not valid:
        await send_text(update, "❌ No valid file IDs.")
        return

    bid = db.create_batch(valid)
    await send_text(
        update,
        f"📦 Batch created: {len(valid)} files\n\n"
        f"🆔 {bid}\n"
        f"🤖 Bot Link:\n{bot_link('batch_' + bid)}"
    )


async def my_plan_cmd(update, context):
    uid = update.effective_user.id
    plan = db.get_premium(uid)
    if not plan:
        await send_text(update, "📋 Plan: Free\n\nNo active premium membership.")
        return
    await send_text(
        update,
        f"💎 Plan: Premium\n\n"
        f"👤 User ID: {uid}\n"
        f"⏳ Expires: {plan['expires_at']}"
    )


async def request_cmd(update, context):
    text = " ".join(context.args).strip()
    if not text:
        await send_text(update, "Usage: /request <movie or series name>")
        return
    rid = db.add_request(update.effective_user.id, text)
    await send_text(update, f"✅ Request submitted.\n🆔 Request ID: {rid}")
    try:
        await context.bot.send_message(
            OWNER_ID,
            f"📩 New request #{rid}\n"
            f"User: {update.effective_user.id}\n"
            f"Request: {text}"
        )
    except TelegramError:
        pass


async def auto_del_cmd(update, context):
    if not admin_only(update):
        return
    if not context.args:
        await send_text(update, f"⏳ Current auto-delete: {DEFAULT_DELETE_SECONDS} seconds\nUsage: /auto_del <seconds>")
        return
    try:
        seconds = max(30, int(context.args[0]))
    except ValueError:
        await send_text(update, "❌ Seconds must be a number.")
        return
    db.set_setting("delete_seconds", str(seconds))
    await send_text(update, f"✅ Auto-delete setting saved: {seconds} seconds.\nRestart the bot to apply it.")


async def fsub_chnl_cmd(update, context):
    if not admin_only(update):
        return
    channels = db.list_fsub()
    if not channels:
        await send_text(update, "📢 No force-sub channels configured.")
        return
    text = "📢 Force-sub channels:\n\n"
    for c in channels:
        text += f"• {c['channel_id']} — {c['title']}\n"
    await send_text(update, text)


async def add_banuser_cmd(update, context):
    if not admin_only(update):
        return
    if len(context.args) != 1:
        await send_text(update, "Usage: /add_banuser <user_id>")
        return
    try:
        uid = int(context.args[0])
        db.ban_user(uid, update.effective_user.id)
        await send_text(update, f"🚫 User {uid} banned.")
    except ValueError:
        await send_text(update, "❌ User ID must be a number.")


async def del_banuser_cmd(update, context):
    if not admin_only(update):
        return
    if len(context.args) != 1:
        await send_text(update, "Usage: /del_banuser <user_id>")
        return
    try:
        db.unban_user(int(context.args[0]))
        await send_text(update, "✅ User unbanned.")
    except ValueError:
        await send_text(update, "❌ User ID must be a number.")


async def banuser_list_cmd(update, context):
    if not admin_only(update):
        return
    rows = db.list_banned()
    if not rows:
        await send_text(update, "🚫 No banned users.")
        return
    await send_text(update, "🚫 Banned users:\n\n" + "\n".join(f"• {r['user_id']}" for r in rows))


async def add_premium_cmd(update, context):
    if not admin_only(update):
        return
    if len(context.args) != 2:
        await send_text(update, "Usage: /add_premium <user_id> <days>")
        return
    try:
        uid, days = int(context.args[0]), int(context.args[1])
        db.add_premium(uid, days)
        await send_text(update, f"💎 Premium added for {uid} for {days} days.")
    except ValueError:
        await send_text(update, "❌ User ID and days must be numbers.")


async def remove_premium_cmd(update, context):
    if not admin_only(update):
        return
    if len(context.args) != 1:
        await send_text(update, "Usage: /remove_premium <user_id>")
        return
    try:
        db.remove_premium(int(context.args[0]))
        await send_text(update, "✅ Premium removed.")
    except ValueError:
        await send_text(update, "❌ User ID must be a number.")


async def list_premium_cmd(update, context):
    if not admin_only(update):
        return
    rows = db.list_premium()
    if not rows:
        await send_text(update, "💎 No active premium accounts.")
        return
    await send_text(update, "💎 Active premium:\n\n" + "\n".join(
        f"• {r['user_id']} — {r['expires_at']}" for r in rows
    ))


async def add_fsub_cmd(update, context):
    if not admin_only(update):
        return
    if len(context.args) < 2:
        await send_text(update, "Usage: /add_fsub <channel_id> <invite_link> [title]")
        return
    channel_id = context.args[0]
    link = context.args[1]
    title = " ".join(context.args[2:]) or channel_id
    db.add_fsub(channel_id, link, title)
    await send_text(update, "✅ Force-sub channel added.\nMake sure the bot is an admin in that channel.")


async def del_fsub_cmd(update, context):
    if not admin_only(update):
        return
    if len(context.args) != 1:
        await send_text(update, "Usage: /del_fsub <channel_id>")
        return
    db.del_fsub(context.args[0])
    await send_text(update, "✅ Force-sub channel removed.")


async def add_admins_cmd(update, context):
    if not owner_only(update):
        return
    if len(context.args) != 1:
        await send_text(update, "Usage: /add_admins <user_id>")
        return
    db.add_admin(int(context.args[0]))
    await send_text(update, "✅ Admin added.")


async def del_admins_cmd(update, context):
    if not owner_only(update):
        return
    if len(context.args) != 1:
        await send_text(update, "Usage: /del_admins <user_id>")
        return
    db.remove_admin(int(context.args[0]))
    await send_text(update, "✅ Admin removed.")


async def admin_list_cmd(update, context):
    if not owner_only(update):
        return
    admins = db.list_admins()
    await send_text(update, "👑 Owner: " + str(OWNER_ID) + "\n\n" +
                    ("\n".join(f"• {x}" for x in admins) if admins else "• No secondary admins"))


async def broadcast_cmd(update, context):
    if not admin_only(update):
        return
    text = " ".join(context.args).strip()
    if not text:
        await send_text(update, "Usage: /broadcast <message>")
        return
    users = db.list_users()
    ok = fail = 0
    for uid in users:
        try:
            await context.bot.send_message(uid, text)
            ok += 1
        except TelegramError:
            fail += 1
        await asyncio.sleep(0.05)
    await send_text(update, f"📣 Broadcast complete.\n✅ Sent: {ok}\n❌ Failed: {fail}")


async def pbroadcast_cmd(update, context):
    if not admin_only(update):
        return
    text = " ".join(context.args).strip()
    if not text:
        await send_text(update, "Usage: /pbroadcast <message>")
        return
    users = db.list_users()
    ok = fail = 0
    for uid in users:
        try:
            msg = await context.bot.send_message(uid, text)
            try:
                await context.bot.pin_chat_message(uid, msg.message_id, disable_notification=True)
            except TelegramError:
                pass
            ok += 1
        except TelegramError:
            fail += 1
        await asyncio.sleep(0.05)
    await send_text(update, f"📌 Broadcast complete.\n✅ Sent: {ok}\n❌ Failed: {fail}")


async def cancel_cmd(update, context):
    context.user_data.clear()
    await send_text(update, "✅ All active setup/configuration state has been reset.")


async def users_cmd(update, context):
    if not owner_only(update):
        return
    await send_text(update, f"👥 Total users: {db.user_count()}")


async def addsub_cmd(update, context):
    if not admin_only(update):
        return
    if len(context.args) != 2:
        await send_text(update, "Usage: /addsub <user_id> <days>")
        return
    try:
        db.add_subscription(int(context.args[0]), int(context.args[1]))
        await send_text(update, "✅ Subscription added.")
    except ValueError:
        await send_text(update, "❌ Invalid numbers.")


async def remsub_cmd(update, context):
    if not admin_only(update):
        return
    if len(context.args) != 1:
        await send_text(update, "Usage: /remsub <user_id>")
        return
    try:
        db.remove_subscription(int(context.args[0]))
        await send_text(update, "✅ Subscription removed.")
    except ValueError:
        await send_text(update, "❌ Invalid user ID.")


async def addadmin_cmd(update, context):
    await add_admins_cmd(update, context)


async def removeadmin_cmd(update, context):
    await del_admins_cmd(update, context)


async def admins_cmd(update, context):
    await admin_list_cmd(update, context)


async def stats_cmd(update, context):
    if not admin_only(update):
        return
    s = db.stats()
    await send_text(
        update,
        f"📊 Statistics\n\n"
        f"📁 Files: {s['files']}\n"
        f"📦 Batches: {s['batches']}\n"
        f"👑 Admins: {s['admins']}\n"
        f"💎 Premium: {s['premium']}\n"
        f"👥 Users: {s['users']}\n"
        f"🚫 Banned: {s['banned']}"
    )


async def fsub_callback(update, context):
    query = update.callback_query
    await query.answer()
    try:
        await query.message.delete()
    except TelegramError:
        pass
    if not await check_fsub(update, context):
        return
    await query.message.chat.send_message("✅ Subscription verified. Now open your file link again.")


def main():
    threading.Thread(target=start_health_server, daemon=True).start()

    app = Application.builder().token(BOT_TOKEN).build()

    handlers = {
        "start": start,
        "help": help_cmd,
        "save": save_cmd,
        "get": get_cmd,
        "verify": verify_cmd,
        "batch": batch_cmd,
        "genlink": genlink_cmd,
        "my_plan": my_plan_cmd,
        "request": request_cmd,
        "auto_del": auto_del_cmd,
        "fsub_chnl": fsub_chnl_cmd,
        "add_banuser": add_banuser_cmd,
        "del_banuser": del_banuser_cmd,
        "banuser_list": banuser_list_cmd,
        "add_premium": add_premium_cmd,
        "remove_premium": remove_premium_cmd,
        "list_premium": list_premium_cmd,
        "add_fsub": add_fsub_cmd,
        "del_fsub": del_fsub_cmd,
        "add_admins": add_admins_cmd,
        "del_admins": del_admins_cmd,
        "admin_list": admin_list_cmd,
        "broadcast": broadcast_cmd,
        "pbroadcast": pbroadcast_cmd,
        "cancel": cancel_cmd,
        "users": users_cmd,
        "addsub": addsub_cmd,
        "remsub": remsub_cmd,
        "addadmin": addadmin_cmd,
        "removeadmin": removeadmin_cmd,
        "admins": admins_cmd,
        "stats": stats_cmd,
    }

    for name, fn in handlers.items():
        app.add_handler(CommandHandler(name, fn))

    app.add_handler(
        __import__("telegram.ext", fromlist=["CallbackQueryHandler"])
        .CallbackQueryHandler(fsub_callback, pattern="^check_fsub$")
    )

    log.info("🔥 Bot starting on Render...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
