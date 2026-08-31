import uuid
import requests

class Shortener:
    """
    AroLinks API adapter.

    AroLinks accepts:
      GET https://arolinks.com/api?api=API_TOKEN&url=DESTINATION&alias=CustomAlias

    JSON response:
      {"status":"success","shortenedUrl":"https://arolinks.com/xxxxx"}
    """

    def __init__(self, api_url, api_key, bot_username, db):
        self.api_url = api_url or "https://arolinks.com/api"
        self.api_key = api_key
        self.bot_username = bot_username.lstrip("@")
        self.db = db

    def create(self, file_id, user_id):
        if not self.api_key:
            return None

        token = self.db.create_token(user_id, file_id)
        destination = f"https://t.me/{self.bot_username}?start=verify_{token}"

        # Use a unique alias only when supported by the account.
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

            if data.get("status") != "success":
                return None

            return data.get("shortenedUrl")
        except (requests.RequestException, ValueError):
            return None

    def verify(self, token):
        return self.db.consume_token(token)
