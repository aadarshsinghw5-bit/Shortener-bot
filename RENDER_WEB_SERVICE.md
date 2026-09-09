# Render Web Service

Start command:
`python -u bot.py`

The health server should bind to Render's `PORT` environment variable.
Do not put BOT_TOKEN or any other secret in GitHub.

If Telegram reports `Conflict: terminated by other getUpdates request`,
make sure no other process is polling with the same bot token.
