import os
from urllib.parse import urlencode


class Shortener:
    def __init__(self, db):
        self.db = db

        self.gateway_url = os.getenv(
            "GATEWAY_URL",
            ""
        ).strip().rstrip("/")

    def create_from_token(self, token, bot_username):
        if not self.gateway_url:
            return None

        return (
            f"{self.gateway_url}/api/gateway?"
            + urlencode({
                "token": token,
                "bot": bot_username.lstrip("@"),
            })
        )
