# File Store Bot - final flow

## Commands
- `/genlink` — reply to ANY message and run this command. No file ID is requested.
- `/batch FIRST_DB_LINK LAST_DB_LINK` — first and last Telegram post must already exist in `files` table. All DB files between their message IDs are included.
- `/premium USER_ID DAYS`
- `/unpremium USER_ID`
- `/addadmin USER_ID`
- `/deladmin USER_ID`

## Single-message flow
Admin replies to any normal/forwarded message:
`/genlink`

Normal user:
`/genlink -> AroLinks -> Telegram deep link -> FSUB check -> file`

Premium user:
`/genlink -> direct Telegram deep link -> FSUB check -> file`

Premium NEVER bypasses FSUB.

## Batch flow
Only DB files are used for batches:
`/batch (first_db_file_link) (last_db_file_link)`

The first and last links must point to DB rows in the same channel. Every DB file whose
message_id lies between those two posts (inclusive) is added in message order.

## Important
Do not commit `.env` or real tokens to GitHub.
For AroLinks, the preferred setup is `AROLINKS_QUICK_LINK`: copy the Quick/Easy Link
template from AroLinks Tools and paste it exactly as provided. The template normally
contains `api=...&url=`. Do not commit the real token/template to GitHub.

If you use API mode instead, configure `AROLINKS_API_URL` and parameter names from your
AroLinks dashboard/documentation.
