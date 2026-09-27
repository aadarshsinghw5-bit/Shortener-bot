import os
import secrets
from datetime import datetime, timezone
from urllib.parse import urlencode


class Shortener:
    def __init__(self, db):
        self.db = db
        self.gateway_url = (
            os.getenv("GATEWAY_URL", "")
            .strip()
            .rstrip("/")
        )

    def _gateway_collection(self):
        """
        Get the MongoDB collection used for one-time
        gateway entries.

        Database class is expected to expose the actual
        Mongo database as self.db.db.
        """
        return self.db.db["gateway_entries"]

    def create_from_token(self, token, bot_username):
        """
        Create a fresh one-time gateway entry.

        Important:
        - Original Telegram token is NOT consumed.
        - Every call creates a new entry_id.
        - The gateway will consume only this entry_id.
        """

        if not self.gateway_url:
            return None

        token = str(token or "").strip()

        if not token:
            return None

        bot_username = str(
            bot_username or ""
        ).replace("@", "").strip()

        if not bot_username:
            return None

        entry_id = secrets.token_urlsafe(32)

        now = datetime.now(timezone.utc)

        entry = {
            "entry_id": entry_id,
            "token": token,
            "bot_username": bot_username,
            "created_at": now,
            "used": False,
        }

        try:
            self._gateway_collection().insert_one(entry)

        except Exception as e:
            print(
                f"[Shortener] Failed to create gateway entry: {e}"
            )
            return None

        return (
            f"{self.gateway_url}/api/gateway?"
            + urlencode(
                {
                    "entry": entry_id
                }
            )
        )
