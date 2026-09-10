# File Store Bot — Final Web Service Build

This build matches the latest agreed flow.

## Main links

### `/genlink`
Reply to **any message/file** and use `/genlink`.

- The bot copies the replied message into `DB_CHANNEL_ID` automatically.
- Forward headers are not preserved by the copy.
- A permanent/reusable main link is created from the saved DB post.
- A `↗ SHARE URL` button is placed under the DB-channel post.
- The main link itself does **not** expire after 2 hours and is **not** single-use.

### `/batch`
Use:

`/batch FIRST_DB_POST_LINK LAST_DB_POST_LINK`

All indexed DB-channel posts between those message IDs are included.
A reusable batch main link is created, and its Share URL button is attached to the first post in the batch range.

## Shortener flow

For a **non-premium** user:

1. User opens the reusable main Genlink/Batch link.
2. FSUB is checked first.
3. A **new AroLinks shortener link is created for that user and that click**.
4. That shortener session is valid for **2 hours from creation**.
5. The shortener session is bound to the Telegram user ID.
6. Another user cannot use that shortener session.
7. When AroLinks is completed and the user returns to the bot, the session is consumed and the file/batch is delivered.
8. The consumed session cannot be reused.
9. If the user opens the same main link again, a **fresh shortener session** is created.

The reusable main link is never converted into a 2-hour token.

Premium users still have to pass FSUB, but skip AroLinks and receive the file/batch directly.

## Download page UI

Uses the configured start image and screenshot-style Unicode font.

Buttons:

- `• CLICK HERE TO DOWNLOAD •` → AroLinks
- `PREMIUM` → `https://t.me/PremiumHub094`
- `TUTORIAL` → `https://t.me/Tutorial_Hub_94/4`

Contact: `@Its_Lozo`

## Start / About

The start image is set from `/settings`/`/setimage` and is reused on the download page.

- `/start` → configured image + requested start caption + ABOUT/CLOSE
- ABOUT → same image + About caption + BACK/CLOSE
- CLOSE → deletes the current bot message

User-facing main UI text and buttons use the requested Unicode small-cap style.

## Start image

Reply to a photo with:

`/setimage`

The Telegram file ID is stored in Supabase settings. No image URL environment variable is required.

## Supabase

Run `schema.sql` once in Supabase SQL Editor.

Required environment variables:

- `BOT_TOKEN`
- `BOT_USERNAME`
- `OWNER_ID`
- `DB_CHANNEL_ID`
- `SUPABASE_URL`
- `SUPABASE_KEY`
- `SHORTENER_API_URL` (optional; defaults to `https://arolinks.com/api`)
- `SHORTENER_API_KEY`

Do **not** add a manual `PORT` environment variable. Render supplies `PORT` automatically.

## Render Web Service

Build command:

`pip install -r requirements.txt`

Start command:

`python -u bot.py`

The bot starts an HTTP health server on `0.0.0.0:$PORT` and Telegram polling in the same process.

## UptimeRobot

UptimeRobot must only monitor the Render Web Service URL, for example:

`https://YOUR-SERVICE.onrender.com/`

It must **not** run Telegram polling or use the bot token.

Only one process may poll Telegram `getUpdates` for this bot token.
