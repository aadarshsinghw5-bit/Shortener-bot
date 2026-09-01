import uuid
import requests
import logging

log = logging.getLogger(__name__)


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
            log.error("SHORTENER_API_KEY is empty")
            return None

        try:
            token = self.db.create_token(user_id, file_id)

            destination = (
                f"https://t.me/{self.bot_username}?start=verify_{token}"
            )

            alias = "f" + uuid.uuid4().hex[:10]

            log.info("AroLinks request starting")
            log.info("AroLinks API URL: %s", self.api_url)
            log.info("Destination: %s", destination)

            response = requests.get(
                self.api_url,
                params={
                    "api": self.api_key,
                    "url": destination,
                    "alias": alias,
                },
                timeout=20,
            )

            log.info(
                "AroLinks HTTP status: %s",
                response.status_code,
            )

            log.info(
                "AroLinks response: %s",
                response.text[:2000],
            )

            response.raise_for_status()

            data = response.json()

            log.info("AroLinks JSON: %s", data)

            if data.get("status") != "success":
                log.error(
                    "AroLinks returned non-success status: %s",
                    data.get("status"),
                )
                return None

            shortened_url = data.get("shortenedUrl")

            if not shortened_url:
                log.error(
                    "AroLinks response has no shortenedUrl: %s",
                    data,
                )
                return None

            log.info("AroLinks short URL created successfully")

            return shortened_url

        except requests.RequestException as e:
            log.exception("AroLinks HTTP request failed: %s", e)
            return None

        except ValueError as e:
            log.exception("AroLinks returned invalid JSON: %s", e)
            return None

        except Exception as e:
            log.exception("AroLinks create error: %s", e)
            return None

    def verify(self, token):
        return self.db.consume_token(token)
