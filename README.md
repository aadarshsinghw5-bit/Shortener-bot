# Telegram File Store Bot

A Telegram file-store bot with:

- 📁 Telegram channel as file database/storage
- 🔗 Permanent single-file links
- 📦 Multiple files in one batch link
- 🔐 Shortener flow for non-subscribed users
- ⚡ Direct delivery for subscribed users
- 👑 Owner + multiple admins
- ⏳ Time-based subscriptions
- 🗃️ SQLite metadata database
- ☁️ Server does not download the actual files

## Architecture

Files are copied into a private Telegram DB channel. The SQLite database stores the channel message ID.

A single file link looks like:

`https://t.me/YourBot?start=file_xxxxxxxxxxxx`

A batch link looks like:

`https://t.me/YourBot?start=batch_xxxxxxxxxxxx`

Users never need access to the DB channel.

## Setup

1. Create your Telegram bot with BotFather.
2. Create a private Telegram channel for file storage.
3. Add the bot to that channel as an administrator with permission to post messages.
4. Find the channel ID. It normally looks like `-100xxxxxxxxxx`.
5. Copy `.env.example` to `.env`.
6. Fill in the bot token, owner Telegram ID, DB channel ID, bot username and shortener credentials.
7. Install dependencies:

```bash
pip install -r requirements.txt
```

8. Start:

```bash
python bot.py
```

## Save files

Send a file to the bot and reply to that message with:

```text
/save
```

The bot copies the message to the DB channel and returns a unique link.

Supported Telegram media in this starter:

- Documents
- Videos
- Audio
- Photos

Because Telegram message IDs are stored, the bot does not need to download the file to your server.

## Create a batch

After saving multiple files, use:

```text
/batch FILE_ID_1 FILE_ID_2 FILE_ID_3
```

The bot returns one batch link. The files are delivered in the same order.

## Subscriptions

Admin:

```text
/addsub USER_ID 30
```

This adds 30 days to the user's current subscription. If the user already has time remaining, the new days are added after the current expiry.

Remove:

```text
/remsub USER_ID
```

When subscribed, the user bypasses the shortener.

## Admins

Owner only:

```text
/addadmin USER_ID
/removeadmin USER_ID
```

List:

```text
/admins
```

The owner is configured through `OWNER_ID` and cannot be removed by an admin.

## AroLinks integration

This repository is configured for the AroLinks Developers API.

The bot sends:

```text
GET https://arolinks.com/api?api=YOUR_API_TOKEN&url=DESTINATION&alias=UNIQUE_ALIAS
```

and expects the JSON response:

```json
{"status":"success","shortenedUrl":"https://arolinks.com/xxxxx"}
```

Set:

```env
SHORTENER_API_URL=https://arolinks.com/api
SHORTENER_API_KEY=YOUR_AROLINKS_API_TOKEN
```

The destination is a Telegram deep link:

```text
https://t.me/YOUR_BOT?start=verify_TOKEN
```

When the user completes the AroLinks redirect, Telegram opens that deep link. The bot validates the single-use token, checks that it belongs to the same Telegram user, and sends the file or complete batch.

**Never put your AroLinks API token in GitHub.** Keep it in `.env` or your hosting provider's secret/environment-variable settings.

## Security

Never commit `.env` or your bot token.

Keep the DB channel private and give the bot only the permissions it needs.

Tokens are single-use and expire after 2 hours by default.

## Limitations of this starter

- SQLite is suitable for small/medium deployments. PostgreSQL is recommended for high traffic.
- Batch delivery sends files sequentially. Large batches may take time because Telegram rate limits apply.
- The generic shortener adapter must be customized for your provider's exact API.
- This repo uses polling. A webhook can be added for production hosting.

## License

MIT
