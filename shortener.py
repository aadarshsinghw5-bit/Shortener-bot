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

        self.gateway_entries = (
            self.db.db["gateway_entries"]
        )

        self.gateway_entries.create_index(
            "entry_id",
            unique=True,
        )

        self.gateway_entries.create_index(
            "expires_at",
        )

    def create_from_token(
        self,
        token,
        bot_username,
    ):
        if not self.gateway_url:
            return None

        entry_id = secrets.token_urlsafe(32)

        now = datetime.now(timezone.utc)

        # Original Telegram token remains untouched.
        # This entry is the one-time gateway URL.
        self.gateway_entries.insert_one(
            {
                "entry_id": entry_id,
                "token": token,
                "bot_username": (
                    bot_username or ""
                ).lstrip("@"),
                "created_at": now,
                "expires_at": now,
                "used": False,
            }
        )

        return (
            f"{self.gateway_url}/api/gateway?"
            + urlencode(
                {
                    "entry": entry_id,
                }
            )
        )
