import uuid
import requests
import logging

log = logging.getLogger(__name__)


class Shortener:
    def __init__(self, api_url, api_key, bot_username, db):
        self.api_url = api_url or "https://arolinks.com/api"
        self.api_key = api_key
        self.bot_username = bot_username.lstrip("@")
        self.db = db

    def create(self, file_id, user_id):
        if not self.api_key:
            log.error("SHORTENER_API_KEY is empty")
            return None

        try:
            token = self.db.create_token(user_id, file_id)
            destination = f"https://t.me/{self.bot_username}?start=verify_{token}"
            alias = "f" + uuid.uuid4().hex[:10]

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
                log.error("AroLinks error: %s", data)
                return None

            return data.get("shortenedUrl")

        except (requests.RequestException, ValueError) as e:
            log.exception("AroLinks request failed: %s", e)
            return None
        except Exception:
            log.exception("Shortener error")
            return None

    def verify(self, token):
        return self.db.consume_token(token)
