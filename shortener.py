import uuid
import logging
import requests


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

            token = self.db.create_token(
                user_id,
                file_id
            )

            destination = (
                f"https://t.me/"
                f"{self.bot_username}"
                f"?start=verify_{token}"
            )

            alias = "f" + uuid.uuid4().hex[:10]

            log.info(
                "Creating AroLinks URL"
            )

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

            log.info(
                "AroLinks response: %s",
                data
            )

            if data.get("status") != "success":

                log.error(
                    "AroLinks error: %s",
                    data
                )

                return None

            return data.get("shortenedUrl")

        except requests.RequestException:

            log.exception(
                "AroLinks request failed"
            )

            return None

        except ValueError:

            log.exception(
                "Invalid AroLinks JSON"
            )

            return None

        except Exception:

            log.exception(
                "Shortener error"
            )

            return None

    def verify(self, token):

        return self.db.consume_token(token)
