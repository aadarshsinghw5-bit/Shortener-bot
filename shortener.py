import uuid
import requests


class Shortener:
    """
    AroLinks API adapter.
    """

    def __init__(self, api_url, api_key, bot_username, db):
        self.api_url = api_url or "https://arolinks.com/api"
        self.api_key = api_key
        self.bot_username = bot_username.lstrip("@")
        self.db = db

    def create(self, file_id, user_id):
        if not self.api_key:
            print("❌ SHORTENER_API_KEY is empty")
            return None

        token = self.db.create_token(user_id, file_id)
        destination = (
            f"https://t.me/{self.bot_username}?start=verify_{token}"
        )

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

            print("AroLinks HTTP:", response.status_code)
            print("AroLinks response:", response.text)

            response.raise_for_status()

            data = response.json()

            if data.get("status") != "success":
                print("❌ AroLinks API error:", data)
                return None

            shortened_url = data.get("shortenedUrl")

            if not shortened_url:
                print("❌ AroLinks returned no shortenedUrl")
                return None

            return shortened_url

        except Exception as e:
            print("❌ AroLinks error:", repr(e))
            return None

    def verify(self, token):
        return self.db.consume_token(token)
