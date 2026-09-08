import os
import uuid
import requests


class Shortener:
    def __init__(self, db):
        self.db = db
        self.api_url = os.getenv(
            "SHORTENER_API_URL",
            "https://arolinks.com/api",
        )
        self.api_key = os.getenv("SHORTENER_API_KEY", "")

    def create(self, user_id, target, bot_username):
        if not self.api_key:
            return None

        # Inner token is the destination reached after AroLinks.
        token = self.db.create_token(user_id, target, hours=2)
        destination = f"https://t.me/{bot_username}?start=verify_{token}"
        alias = "f" + uuid.uuid4().hex[:10]

        try:
            response = requests.get(
                self.api_url,
                params={
                    "api": self.api_key,
                    "url": destination,
                    "alias": alias,
                },
                timeout=20,
            )
            response.raise_for_status()
            data = response.json()
            if data.get("status") == "success":
                return data.get("shortenedUrl")
        except Exception:
            return None

        return None
