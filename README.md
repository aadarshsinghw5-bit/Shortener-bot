# File Store Bot — Clean Final Build

This is a clean implementation of the latest agreed flow.

## UI

### /start
Uses the image saved through `/setimage` and the requested screenshot-style start caption:

Hi There....! 💥

I am a file-store bot.
I can generate links directly with no problems.

My Owner: @Its_Lozo

Buttons: `ABOUT` and `CLOSE`.

### ABOUT
Keeps the start image and shows:

About Us..

➤ Made for : Anime Hub
➤ Owner : @Its_Lozo
➤ Developer : @Its_Lozo

Adios !!

Buttons: `BACK` and `CLOSE`.

### CLOSE
Deletes the bot's current message.

## Links

### Single
Reply to ANY message/file and use:

`/genlink`

No file ID is requested. `/save` is not implemented.

The message reference is stored in the token, so a single link does not need the message to be in the DB channel.

### Batch
Only:

`/batch FIRST_DB_FILE_LINK LAST_DB_FILE_LINK`

Both links must point to posts in the configured DB channel. All indexed DB posts between those message IDs are included.

## Shortener

Non-premium users:
1. Open generated link.
2. FSUB is checked.
3. Download page is shown.
4. User clicks the download button.
5. AroLinks is completed.
6. The bot sends the requested file(s).

Shortener tokens are valid for 2 hours and are single-use.

Premium users:
- Still must pass FSUB.
- Skip AroLinks.
- Receive the file directly.

Download page buttons:
- `• CLICK HERE TO DOWNLOAD •`
- `PREMIUM` → https://t.me/PremiumHub094
- `TUTORIAL` → https://t.me/Tutorial_Hub_94/4

Contact: `@Its_Lozo`

## Start image

Send a photo to the bot, reply to it with:

`/setimage`

The Telegram file ID is saved in Supabase. You do NOT need a START_IMAGE URL environment variable.

## DB channel

Add the bot as an administrator to the configured DB channel.

New channel posts are automatically indexed in `files`.

Set:

`DB_CHANNEL_ID=-100xxxxxxxxxx`

## FSUB

Add:

`/addfsub -1001234567890 https://t.me/+invite Channel Name`

Remove:

`/delfsub -1001234567890`

The bot must be able to call `getChatMember` for the FSUB channel.

## Premium

Add:

`/premium USER_ID DAYS`

Remove:

`/unpremium USER_ID`

## Render

Use a **Background Worker**:

Build:
`pip install -r requirements.txt`

Start:
`python -u bot.py`

Important: Telegram polling allows only one active `getUpdates` consumer for a bot token. Do NOT run this same bot token in a second Render service, local process, VPS, or UptimeRobot process.

UptimeRobot should only monitor an HTTP health endpoint if one is added; it must not run the bot itself.
