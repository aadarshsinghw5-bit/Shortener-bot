import os
import requests
from urllib.parse import urlencode


class Shortener:
    def __init__(self, db):
        self.db = db

        self.api_url = os.getenv(
            "SHORTENER_API_URL",
            "https://arolinks.com/api"
        )

        self.api_key = os.getenv(
            "SHORTENER_API_KEY",
            ""
        )

        self.gateway_url = os.getenv(
            "GATEWAY_URL",
            ""
        ).rstrip("/")

    def create_from_token(self, token, bot_username):
        if not self.api_key:
            return None

        # AroLinks will redirect to our Vercel gateway,
        # instead of exposing the Telegram verification URL directly.
        if self.gateway_url:
            destination = (
                f"{self.gateway_url}/api/gateway?"
                + urlencode({"token": token})
            )
        else:
            # Fallback if GATEWAY_URL is not configured.
            destination = (
                f"https://t.me/{bot_username}?"
                + urlencode({"start": f"verify_{token}"})
            )

        try:
            response = requests.get(
                self.api_url,
                params={
                    "api": self.api_key,
                    "url": destination
                },
                timeout=20
            )

            response.raise_for_status()

            data = response.json()

            if data.get("status") == "success":
                return (
                    data.get("shortenedUrl")
                    or data.get("shortened_url")
                )

        except Exception:
            return None

        return None
