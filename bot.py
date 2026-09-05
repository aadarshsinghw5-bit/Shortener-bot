import os
import re
import logging
from datetime import datetime, timezone

import aiohttp
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.constants import ChatMemberStatus
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

from database import Database

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("file-store-bot")

# ============================================================
# ENV
# ============================================================

BOT_TOKEN = os.environ["BOT_TOKEN"]
BOT_USERNAME = os.environ.get("BOT_USERNAME", "").lstrip("@")
START_IMAGE = os.environ.get("START_IMAGE", "").strip()
SHORTENER_IMAGE = os.environ.get("SHORTENER_IMAGE", "").strip()

# Your AroLinks token. NEVER put the real token in GitHub.
AROLINKS_TOKEN = os.environ.get("AROLINKS_TOKEN", "").strip()

# AroLinks API endpoint. Keep configurable because API endpoints can change.
# Example:
#   https://arolinks.com/api
# or the exact endpoint shown in your AroLinks dashboard.
AROLINKS_API_URL = os.environ.get("AROLINKS_API_URL", "").strip()
AROLINKS_QUICK_LINK = os.environ.get("AROLINKS_QUICK_LINK", "").strip()

# If your AroLinks dashboard requires a fixed API parameter name,
# these can be changed without touching the rest of the bot.
AROLINKS_TOKEN_PARAM = os.environ.get("AROLINKS_TOKEN_PARAM", "api")
AROLINKS_URL_PARAM = os.environ.get("AROLINKS_URL_PARAM", "url")

# Public links used by the buttons.
TUTORIAL_URL = os.environ.get("TUTORIAL_URL", "https://t.me/").strip()
PREMIUM_URL = os.environ.get("PREMIUM_URL", "https://t.me/").strip()

# This is the text shown on the /start home screen.
# Change only these strings if you want different wording.
HOME_TEXT = """⚡ HEY, {name} ~

I AM FILE STORE BOT, I CAN STORE PRIVATE
FILES IN SPECIFIED CHANNEL AND OTHER USERS
CAN ACCESS IT FROM SPECIAL LINK."""

ABOUT_TEXT = """ℹ️ ABOUT

This bot creates special links for files/messages.

• Single message: reply to any message and use /genlink
• Batch: use /batch with the first and last DB file links
• Premium users still have to join all required channels
• Premium users get direct access without the shortener

Use the buttons below to continue."""

# ============================================================
# DATABASE
# ============================================================

db = Database()


# ============================================================
# HELPERS
# ============================================================

def home_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("↗ • ANIME HUB", url=os.environ.get("ANIME_HUB_URL", "https://t.me/")),
            InlineKeyboardButton("ABOUT •", callback_data="about"),
        ],
        [
            InlineKeyboardButton("• CLOSE •", callback_data="close"),
        ],
    ])


def download_keyboard(short_url: str):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Click to download your file", url=short_url)],
        [
            InlineKeyboardButton("How to Open", url=TUTORIAL_URL),
            InlineKeyboardButton("Premium", url=PREMIUM_URL),
        ],
    ])


def fsub_keyboard(channels):
    rows = []
    for ch in channels:
        title = ch.get("title") or ch.get("channel_id", "Channel")
        link = ch.get("invite_link") or ""
        if link:
            rows.append([InlineKeyboardButton(f"• {title} •", url=link)])
    rows.append([InlineKeyboardButton("♻️ Try Again", callback_data="check_fsub")])
    return InlineKeyboardMarkup(rows)


async def is_joined(bot, user_id: int, channel_id: str) -> bool:
    try:
        member = await bot.get_chat_member(chat_id=int(channel_id), user_id=user_id)
        return member.status in {
            ChatMemberStatus.MEMBER,
            ChatMemberStatus.ADMINISTRATOR,
            ChatMemberStatus.OWNER,
        }
    except Exception as exc:
        log.warning("FSUB check failed for %s / %s: %s", channel_id, user_id, exc)
        return False


async def check_all_fsub(bot, user_id: int):
    channels = db.list_fsub()
    missing = []
    for ch in channels:
        if not await is_joined(bot, user_id, str(ch["channel_id"])):
            missing.append(ch)
    return missing


async def require_fsub(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    user = update.effective_user
    missing = await check_all_fsub(context.bot, user.id)

    if not missing:
        return True

    text = "🔒 Please join all required channels first.\n\nThen tap **Try Again**."
    if update.callback_query:
        try:
            await update.callback_query.answer()
            await update.callback_query.edit_message_text(
                text,
                reply_markup=fsub_keyboard(missing),
                parse_mode="Markdown",
            )
        except Exception:
            await update.callback_query.message.reply_text(
                text,
                reply_markup=fsub_keyboard(missing),
                parse_mode="Markdown",
            )
    else:
        await update.effective_message.reply_text(
            text,
            reply_markup=fsub_keyboard(missing),
            parse_mode="Markdown",
        )
    return False


def make_bot_deeplink(token: str) -> str:
    if not BOT_USERNAME:
        raise RuntimeError("BOT_USERNAME is missing")
    return f"https://t.me/{BOT_USERNAME}?start={token}"


async def shorten_url(long_url: str) -> str:
    """
    Shorten using either:
      1) AroLinks Quick/Easy Link (recommended), or
      2) a configured AroLinks API endpoint.

    Quick link normally looks like:
      https://.../?api=TOKEN&url=

    Put the complete Quick Link template in AROLINKS_QUICK_LINK,
    including the final `url=`. The destination URL is URL-encoded.
    """
    if AROLINKS_QUICK_LINK:
        from urllib.parse import quote
        separator = "" if AROLINKS_QUICK_LINK.endswith(("=", "&", "?")) else "&url="
        if "url=" in AROLINKS_QUICK_LINK:
            return AROLINKS_QUICK_LINK + quote(long_url, safe="")
        return AROLINKS_QUICK_LINK + separator + quote(long_url, safe="")

    if not AROLINKS_TOKEN:
        raise RuntimeError("AROLINKS_TOKEN is missing")
    if not AROLINKS_API_URL:
        raise RuntimeError("AROLINKS_API_URL is missing")

    params = {
        AROLINKS_TOKEN_PARAM: AROLINKS_TOKEN,
        AROLINKS_URL_PARAM: long_url,
    }

    timeout = aiohttp.ClientTimeout(total=20)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(AROLINKS_API_URL, params=params) as resp:
            raw = await resp.text()

            if resp.status != 200:
                raise RuntimeError(f"AroLinks HTTP {resp.status}: {raw[:300]}")

            try:
                data = await resp.json(content_type=None)
            except Exception:
                data = None

            if isinstance(data, dict):
                for key in (
                    "shortenedUrl",
                    "shortened_url",
                    "short_url",
                    "short",
                    "url",
                    "link",
                ):
                    value = data.get(key)
                    if isinstance(value, str) and value.startswith(("http://", "https://")):
                        return value

                for parent in ("data", "result"):
                    child = data.get(parent)
                    if isinstance(child, dict):
                        for key in ("shortenedUrl", "shortened_url", "short_url", "short", "url", "link"):
                            value = child.get(key)
                            if isinstance(value, str) and value.startswith(("http://", "https://")):
                                return value

            text = raw.strip().strip('"')
            match = re.search(r"https?://\S+", text)
            if match:
                return match.group(0).rstrip('"\'')
    raise RuntimeError("Could not find a short URL in the AroLinks response.")


async def send_file_from_target(update: Update, context: ContextTypes.DEFAULT_TYPE, target: str):
    """
    target formats:
      single:<source_chat_id>:<source_message_id>
      file:<file_id>
      batch:<batch_id>
    """
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id

    if target.startswith("single:"):
        _, source_chat_id, source_message_id = target.split(":", 2)
        sent = await context.bot.copy_message(
            chat_id=chat_id,
            from_chat_id=int(source_chat_id),
            message_id=int(source_message_id),
        )
        await schedule_delete(context, chat_id, sent.message_id)
        return

    if target.startswith("file:"):
        file_id = target.split(":", 1)[1]
        row = db.get_file(file_id)
        if not row:
            await context.bot.send_message(chat_id, "❌ File link is invalid or expired.")
            return

        sent = await context.bot.copy_message(
            chat_id=chat_id,
            from_chat_id=int(row["channel_id"]),
            message_id=int(row["message_id"]),
        )
        await schedule_delete(context, chat_id, sent.message_id)
        return

    if target.startswith("batch:"):
        batch_id = target.split(":", 1)[1]
        items = db.get_batch_items(batch_id)
        if not items:
            await context.bot.send_message(chat_id, "❌ Batch is empty or invalid.")
            return

        for row in items:
            try:
                sent = await context.bot.copy_message(
                    chat_id=chat_id,
                    from_chat_id=int(row["channel_id"]),
                    message_id=int(row["message_id"]),
                )
                await schedule_delete(context, chat_id, sent.message_id)
            except Exception as exc:
                log.exception("Could not copy batch file %s: %s", row.get("file_id"), exc)
        return

    await context.bot.send_message(chat_id, "❌ Invalid download link.")


async def send_link_result(update: Update, text: str, url: str):
    markup = download_keyboard(url)
    if SHORTENER_IMAGE:
        try:
            return await update.effective_message.reply_photo(
                photo=SHORTENER_IMAGE,
                caption=text,
                reply_markup=markup,
            )
        except Exception as exc:
            log.warning("SHORTENER_IMAGE could not be sent: %s", exc)
    return await update.effective_message.reply_text(
        text,
        reply_markup=markup,
    )


async def schedule_delete(context: ContextTypes.DEFAULT_TYPE, chat_id: int, message_id: int):
    seconds = db.get_delete_seconds()
    if seconds <= 0:
        return

    from datetime import timedelta
    delete_at = datetime.now(timezone.utc) + timedelta(seconds=seconds)
    db.add_pending_delete(chat_id, message_id, delete_at)


async def process_pending_deletes(context: ContextTypes.DEFAULT_TYPE):
    now = datetime.now(timezone.utc)
    for row in db.get_pending_deletes():
        try:
            delete_at = datetime.fromisoformat(
                row["delete_at"].replace("Z", "+00:00")
            )
            if delete_at > now:
                continue

            try:
                await context.bot.delete_message(
                    chat_id=int(row["chat_id"]),
                    message_id=int(row["message_id"]),
                )
            except Exception as exc:
                log.debug("Delete failed for %s: %s", row["id"], exc)

            db.mark_delete_done(row["id"])
        except Exception as exc:
            log.warning("Pending delete error: %s", exc)


# ============================================================
# /START
# ============================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    db.add_user(
        user.id,
        user.username or "",
        user.first_name or "",
    )

    if db.is_banned(user.id):
        await update.effective_message.reply_text("🚫 You are banned from using this bot.")
        return

    # Deep-link token: /start TOKEN
    if context.args:
        token = context.args[0]
        consumed = db.consume_token(token)

        if not consumed:
            await update.effective_message.reply_text("❌ This link is invalid, expired or already used.")
            return

        _, target = consumed

        # IMPORTANT: premium does NOT bypass FSUB.
        if not await require_fsub(update, context):
            return

        try:
            await send_file_from_target(update, context, target)
        except Exception as exc:
            log.exception("Download failed: %s", exc)
            await update.effective_message.reply_text(
                "❌ Unable to send the file. The source message may no longer be accessible."
            )
        return

    text = HOME_TEXT.format(name=user.first_name or "User")

    if START_IMAGE:
        try:
            await update.effective_message.reply_photo(
                photo=START_IMAGE,
                caption=text,
                reply_markup=home_keyboard(),
            )
            return
        except Exception as exc:
            log.warning("START_IMAGE could not be sent: %s", exc)

    await update.effective_message.reply_text(
        text,
        reply_markup=home_keyboard(),
    )


# ============================================================
# ABOUT / CLOSE
# ============================================================

async def callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    if q.data == "about":
        await q.edit_message_text(
            ABOUT_TEXT,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("↩️ Back", callback_data="home")]
            ]),
        )
        return

    if q.data == "home":
        user = q.from_user
        await q.edit_message_text(
            HOME_TEXT.format(name=user.first_name or "User"),
            reply_markup=home_keyboard(),
        )
        return

    if q.data == "close":
        try:
            await q.message.delete()
        except Exception:
            pass
        return

    if q.data == "check_fsub":
        if await require_fsub(update, context):
            # Do not automatically reveal a file here; the user can use
            # the original download/deep link again.
            await q.edit_message_text(
                "✅ All required channels joined.\n\nOpen the download link again.",
            )
        return


# ============================================================
# /GENLINK
# ============================================================

async def genlink(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    db.add_user(user.id, user.username or "", user.first_name or "")

    if db.is_banned(user.id):
        await update.effective_message.reply_text("🚫 You are banned.")
        return

    if not db.is_admin(user.id):
        await update.effective_message.reply_text("❌ Admin only.")
        return

    reply = update.effective_message.reply_to_message
    if not reply:
        await update.effective_message.reply_text(
            "Reply to any message/file and use:\n/genlink"
        )
        return

    # Works for normal messages and forwarded messages.
    target = f"single:{reply.chat_id}:{reply.message_id}"

    # Token itself is enough. No file ID is requested.
    token = db.create_token(user.id, target, hours=2)
    deep_link = make_bot_deeplink(token)

    # Premium still has FSUB, but /genlink generation itself is admin-only.
    if await check_all_fsub(context.bot, user.id):
        pass

    # Premium admin gets a direct Telegram link; normal admin gets AroLinks.
    if db.is_premium(user.id):
        final_url = deep_link
    else:
        try:
            final_url = await shorten_url(deep_link)
        except Exception as exc:
            log.exception("Shortener failed: %s", exc)
            await update.effective_message.reply_text(
                "❌ Shortener error.\n\nCheck AROLINKS_API_URL / AROLINKS_TOKEN."
            )
            return

    await send_link_result(
        update,
        "Your Link is down here click on Short URL..",
        final_url,
    )


# ============================================================
# /BATCH
# ============================================================

PRIVATE_LINK_RE = re.compile(
    r"^https?://t\.me/c/(\d+)/(\d+)(?:\?.*)?$",
    re.IGNORECASE,
)
PUBLIC_LINK_RE = re.compile(
    r"^https?://t\.me/([A-Za-z0-9_]+)/(\d+)(?:\?.*)?$",
    re.IGNORECASE,
)


async def resolve_telegram_post_link(bot, link: str):
    link = link.strip()

    m = PRIVATE_LINK_RE.match(link)
    if m:
        internal_id = m.group(1)
        message_id = int(m.group(2))
        channel_id = int(f"-100{internal_id}")
        return channel_id, message_id

    m = PUBLIC_LINK_RE.match(link)
    if m:
        username = m.group(1)
        message_id = int(m.group(2))
        chat = await bot.get_chat(f"@{username}")
        return int(chat.id), message_id

    return None


async def batch(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    db.add_user(user.id, user.username or "", user.first_name or "")

    if db.is_banned(user.id):
        await update.effective_message.reply_text("🚫 You are banned.")
        return

    if not db.is_admin(user.id):
        await update.effective_message.reply_text("❌ Admin only.")
        return

    if len(context.args) != 2:
        await update.effective_message.reply_text(
            "Use exactly:\n\n"
            "/batch (first_db_file_link) (last_db_file_link)\n\n"
            "Example:\n"
            "/batch https://t.me/c/1234567890/100 https://t.me/c/1234567890/120"
        )
        return

    first = await resolve_telegram_post_link(context.bot, context.args[0])
    last = await resolve_telegram_post_link(context.bot, context.args[1])

    if not first or not last:
        await update.effective_message.reply_text("❌ Invalid Telegram post link.")
        return

    first_channel, first_message = first
    last_channel, last_message = last

    if first_channel != last_channel:
        await update.effective_message.reply_text(
            "❌ First and last DB file links must belong to the same channel."
        )
        return

    if first_message > last_message:
        await update.effective_message.reply_text(
            "❌ First link message ID must be smaller than the last link message ID."
        )
        return

    first_row = db.get_file_by_message(first_channel, first_message)
    last_row = db.get_file_by_message(last_channel, last_message)

    if not first_row or not last_row:
        await update.effective_message.reply_text(
            "❌ Both links must point to files that already exist in the DB."
        )
        return

    rows = db.get_files_between(
        first_channel,
        first_message,
        last_message,
    )

    if not rows:
        await update.effective_message.reply_text("❌ No DB files found between these posts.")
        return

    file_ids = [row["file_id"] for row in rows]
    batch_id = db.create_batch(file_ids)

    token = db.create_token(user.id, f"batch:{batch_id}", hours=2)
    deep_link = make_bot_deeplink(token)

    if db.is_premium(user.id):
        final_url = deep_link
    else:
        try:
            final_url = await shorten_url(deep_link)
        except Exception as exc:
            log.exception("Batch shortener failed: %s", exc)
            await update.effective_message.reply_text(
                "❌ Shortener error.\n\nCheck AROLINKS_API_URL / AROLINKS_TOKEN."
            )
            return

    await send_link_result(
        update,
        f"✅ Batch created\n\n"
        f"📦 Files: {len(rows)}\n"
        f"🆔 Batch: `{batch_id}`",
        final_url,
    )


# ============================================================
# OPTIONAL ADMIN COMMANDS
# ============================================================

async def addadmin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not db.is_admin(update.effective_user.id):
        await update.effective_message.reply_text("❌ Admin only.")
        return
    if not context.args:
        await update.effective_message.reply_text("Use /addadmin USER_ID")
        return
    try:
        db.add_admin(int(context.args[0]))
        await update.effective_message.reply_text("✅ Admin added.")
    except ValueError:
        await update.effective_message.reply_text("❌ Invalid user ID.")


async def deladmin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not db.is_admin(update.effective_user.id):
        await update.effective_message.reply_text("❌ Admin only.")
        return
    if not context.args:
        await update.effective_message.reply_text("Use /deladmin USER_ID")
        return
    try:
        db.remove_admin(int(context.args[0]))
        await update.effective_message.reply_text("✅ Admin removed.")
    except ValueError:
        await update.effective_message.reply_text("❌ Invalid user ID.")


async def premium(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not db.is_admin(update.effective_user.id):
        await update.effective_message.reply_text("❌ Admin only.")
        return
    if len(context.args) != 2:
        await update.effective_message.reply_text("Use /premium USER_ID DAYS")
        return
    try:
        uid = int(context.args[0])
        days = int(context.args[1])
        expires = db.add_premium(uid, days)
        await update.effective_message.reply_text(
            f"✅ Premium activated.\nUser: `{uid}`\nExpires: `{expires.isoformat()}`",
            parse_mode="Markdown",
        )
    except ValueError:
        await update.effective_message.reply_text("❌ Invalid user ID or days.")


async def unpremium(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not db.is_admin(update.effective_user.id):
        await update.effective_message.reply_text("❌ Admin only.")
        return
    if not context.args:
        await update.effective_message.reply_text("Use /unpremium USER_ID")
        return
    try:
        db.remove_premium(int(context.args[0]))
        await update.effective_message.reply_text("✅ Premium removed.")
    except ValueError:
        await update.effective_message.reply_text("❌ Invalid user ID.")


# ============================================================
# STARTUP
# ============================================================

async def post_init(application: Application):
    global BOT_USERNAME
    if not BOT_USERNAME:
        me = await application.bot.get_me()
        BOT_USERNAME = me.username or ""
    log.info("Bot started as @%s", BOT_USERNAME)


def main():
    app = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("genlink", genlink))
    app.add_handler(CommandHandler("batch", batch))
    app.add_handler(CommandHandler("addadmin", addadmin))
    app.add_handler(CommandHandler("deladmin", deladmin))
    app.add_handler(CommandHandler("premium", premium))
    app.add_handler(CommandHandler("unpremium", unpremium))
    app.add_handler(CallbackQueryHandler(callbacks))

    # Pending deletion worker.
    app.job_queue.run_repeating(process_pending_deletes, interval=15, first=15)

    log.info("Starting polling...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
