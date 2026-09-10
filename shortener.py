import os
import requests


class Shortener:
    def __init__(self, db):
        self.db = db
        self.api_url = os.getenv("SHORTENER_API_URL", "https://arolinks.com/api")
        self.api_key = os.getenv("SHORTENER_API_KEY", "")

    def create_from_token(self, token, bot_username):
        if not self.api_key:
            return None
        destination = f"https://t.me/{bot_username}?start=verify_{token}"
        try:
            response = requests.get(self.api_url, params={"api": self.api_key, "url": destination}, timeout=20)
            response.raise_for_status()
            data = response.json()
            if data.get("status") == "success":
                return data.get("shortenedUrl") or data.get("shortened_url")
        except Exception:
            return None
        return None
