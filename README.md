# File Store Bot — Final Web Service Build

## Important flow
- `/genlink`: reply to any message/file. The bot copies it to the DB channel, creates ONE reusable main link, and adds a Share URL button to the copied DB post.
- `/batch FIRST_LINK LAST_LINK`: creates ONE reusable batch main link from indexed DB-channel posts in that range and adds the same batch Share URL button to each selected DB post.
- Main Genlink/Batch links do **not** expire and are not single-use.
- Every time a non-premium user opens a main link, a **new personal AroLinks shortener session** is created for that Telegram user.
- Each shortener session is valid for 2 hours. It is locked to that user ID. When the shortener redirects back and the file/batch is delivered, that session is immediately consumed.
- Premium users still pass FSUB but bypass the shortener.

## Settings
`/settings` opens buttons for:
- Set Image
- Admins
- Add FSUB
- FSUB List
- Remove FSUB

There is intentionally **no `/setimage` command**. The image is set from the Settings button.

## Premium commands
- `/addsubs USER_ID DAYS`
- `/removesubs USER_ID`

## Render / UptimeRobot
This is a Render Web Service. The bot starts a small HTTP health server on Render's automatic `$PORT` and binds to `0.0.0.0`.
Monitor the Render service URL (for example `/`) with UptimeRobot. UptimeRobot must not run Telegram polling.
