# File Store Bot

GitHub/Render-ready Telegram file-store bot using Supabase and AroLinks.

## Current flow

### Single `/genlink`
Reply to **any normal message or forwarded message** and send:

`/genlink`

No file ID is requested and the replied message does **not** need to exist in the DB. The bot creates a temporary Telegram deep-link token for that exact message. Free users get an AroLinks URL; premium users get the direct Telegram deep link.

### Batch `/batch`
Batch accepts exactly two Telegram post links:

`/batch (first_db_file_link) (last_db_file_link)`

Both links must point to files already present in the DB, in the same DB channel. Every DB file between those two message IDs (inclusive) is included.

### FSUB + Premium
Premium users **must still complete FSUB**. Premium only removes the AroLinks step; it never bypasses force-subscription.

### Shortener result UI
The shortener result uses the screenshot-style bold/uppercase layout:
- `HEY BRO/SIS`
- `YOUR LINK IS READY...`
- `TO BUY PREMIUM, CONTACT: @Its_Lozo`
- `• CLICK HERE TO DOWNLOAD •`
- `PREMIUM` → `https://t.me/PremiumHub094`
- `TUTORIAL` → `https://t.me/Tutorial_Hub_94/4`

Set `SHORTENER_IMAGE` to the Telegram `file_id` (or supported image URL) you want above this text.

## Render + UptimeRobot
The bot runs Telegram long polling and also starts a small HTTP health server on Render's `PORT`. UptimeRobot should **only monitor the Render health URL**; it must not run another copy of the bot.

A Telegram `Conflict: terminated by other getUpdates request` means another process/service is polling the same bot token. Keep exactly **one** bot polling instance running.

## Security
Never commit `.env`, `BOT_TOKEN`, `SUPABASE_KEY`, or the AroLinks token/Quick Link to GitHub. Put them in Render Environment Variables.
