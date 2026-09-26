import os
import uuid
from datetime import datetime, timedelta, timezone

from pymongo import MongoClient, ASCENDING
from pymongo.collection import ReturnDocument


class Database:
    """
    Dual-bot compatible MongoDB layer.

    Shared between both bots:
        - files
        - batches
        - batch_items
        - banned_users
        - premium
        - main_links

    Separate per bot:
        - users
        - admins
        - settings
        - fsub_channels
        - broadcasts
        - tokens

    Set BOT_ID differently on each Render service, for example:
        Bot 1: BOT_ID=bot1
        Bot 2: BOT_ID=bot2

    Existing legacy Bot-1 collections are migrated into the bot1 namespace
    automatically when BOT_ID is "bot1" (or when the old collections are
    detected and no scoped collection exists yet).
    """

    def __init__(self):
        uri = os.environ.get("MONGO_URI", "").strip()

        if not uri:
            raise RuntimeError("MONGO_URI is not configured")

        self.client = MongoClient(
            uri,
            serverSelectionTimeoutMS=10000,
        )

        db_name = (
            os.environ.get("MONGO_DB", "file_store_bot").strip()
            or "file_store_bot"
        )

        self.db = self.client[db_name]

        # -------------------------------------------------
        # BOT ID / NAMESPACE
        # -------------------------------------------------
        self.bot_id = (
            os.environ.get("BOT_ID", "").strip().lower()
            or "bot1"
        )

        # Keep the namespace Mongo-safe and predictable.
        self.bot_id = "".join(
            ch if (ch.isalnum() or ch in "_-") else "_"
            for ch in self.bot_id
        )[:64] or "bot1"

        self.namespace = f"bot_{self.bot_id}"

        # -------------------------------------------------
        # SHARED COLLECTIONS
        # -------------------------------------------------
        self.banned_users = self.db["banned_users"]
        self.premium = self.db["premium"]

        self.files = self.db["files"]
        self.batches = self.db["batches"]
        self.batch_items = self.db["batch_items"]

        # Main links point to shared files/batches, so they are shared.
        self.main_links = self.db["main_links"]

        # -------------------------------------------------
        # BOT-SPECIFIC COLLECTIONS
        # -------------------------------------------------
        self.users = self.db[f"{self.namespace}_users"]
        self.admins = self.db[f"{self.namespace}_admins"]
        self.settings = self.db[f"{self.namespace}_settings"]
        self.fsub_channels = self.db[f"{self.namespace}_fsub_channels"]
        self.broadcasts = self.db[f"{self.namespace}_broadcasts"]
        self.tokens = self.db[f"{self.namespace}_tokens"]

        self._ensure_indexes()
        self._migrate_legacy_bot1_data()

    # =====================================================
    # INDEXES
    # =====================================================

    def _ensure_indexes(self):
        # Shared
        self.banned_users.create_index(
            [("user_id", ASCENDING)],
            unique=True,
        )

        self.premium.create_index(
            [("user_id", ASCENDING)],
            unique=True,
        )

        self.files.create_index(
            [("file_id", ASCENDING)],
            unique=True,
        )

        self.files.create_index(
            [
                ("channel_id", ASCENDING),
                ("message_id", ASCENDING),
            ],
            unique=True,
        )

        self.batches.create_index(
            [("batch_id", ASCENDING)],
            unique=True,
        )

        self.batch_items.create_index(
            [
                ("batch_id", ASCENDING),
                ("position", ASCENDING),
            ],
            unique=True,
        )

        self.main_links.create_index(
            [("token", ASCENDING)],
            unique=True,
        )

        # Bot-specific
        self.users.create_index(
            [("user_id", ASCENDING)],
            unique=True,
        )

        self.admins.create_index(
            [("user_id", ASCENDING)],
            unique=True,
        )

        self.settings.create_index(
            [("key", ASCENDING)],
            unique=True,
        )

        self.fsub_channels.create_index(
            [("channel_id", ASCENDING)],
            unique=True,
        )

        self.broadcasts.create_index(
            [("broadcast_id", ASCENDING)],
            unique=True,
        )

        self.tokens.create_index(
            [("token", ASCENDING)],
            unique=True,
        )

        self.tokens.create_index(
            [
                ("user_id", ASCENDING),
                ("used", ASCENDING),
            ],
        )

    # =====================================================
    # LEGACY MIGRATION
    # =====================================================

    def _collection_exists(self, name):
        try:
            return name in self.db.list_collection_names()
        except Exception:
            return False

    def _copy_legacy_collection(self, legacy_name, target_collection):
        """
        Copy legacy documents into the current bot namespace.

        This is intentionally copy-only: legacy collections are NOT deleted.
        If a document with the same natural key already exists, it is skipped.
        """
        if not self._collection_exists(legacy_name):
            return

        try:
            source = self.db[legacy_name]

            if target_collection.count_documents({}) > 0:
                return

            docs = list(source.find({}))
            if not docs:
                return

            for doc in docs:
                doc.pop("_id", None)

                try:
                    if legacy_name == "users":
                        uid = int(doc["user_id"])
                        target_collection.update_one(
                            {"user_id": uid},
                            {"$setOnInsert": doc},
                            upsert=True,
                        )

                    elif legacy_name == "admins":
                        uid = int(doc["user_id"])
                        target_collection.update_one(
                            {"user_id": uid},
                            {"$setOnInsert": doc},
                            upsert=True,
                        )

                    elif legacy_name == "settings":
                        key = str(doc["key"])
                        target_collection.update_one(
                            {"key": key},
                            {"$setOnInsert": doc},
                            upsert=True,
                        )

                    elif legacy_name == "fsub_channels":
                        cid = str(doc["channel_id"])
                        target_collection.update_one(
                            {"channel_id": cid},
                            {"$setOnInsert": doc},
                            upsert=True,
                        )

                    elif legacy_name == "broadcasts":
                        bid = str(doc["broadcast_id"])
                        target_collection.update_one(
                            {"broadcast_id": bid},
                            {"$setOnInsert": doc},
                            upsert=True,
                        )

                except Exception:
                    # One malformed legacy row must not stop startup.
                    continue

        except Exception:
            # Migration is best-effort and never prevents the bot from booting.
            pass

    def _migrate_legacy_bot1_data(self):
        """
        The old project used unscoped collection names.

        Bot 1 should continue seeing that data after the migration. Bot 2
        starts with clean bot-specific collections.

        Migration is enabled for the default bot1 namespace. Legacy collections
        remain untouched so rollback is possible.
        """
        if self.bot_id != "bot1":
            return

        self._copy_legacy_collection("users", self.users)
        self._copy_legacy_collection("admins", self.admins)
        self._copy_legacy_collection("settings", self.settings)
        self._copy_legacy_collection(
            "fsub_channels",
            self.fsub_channels,
        )
        self._copy_legacy_collection(
            "broadcasts",
            self.broadcasts,
        )

    # =====================================================
    # DATETIME
    # =====================================================

    @staticmethod
    def _dt(value):
        if isinstance(value, datetime):
            dt = value
        else:
            dt = datetime.fromisoformat(
                str(value).replace("Z", "+00:00")
            )

        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)

        return dt.astimezone(timezone.utc)

    # =====================================================
    # USERS
    # =====================================================

    def add_user(
        self,
        user_id,
        username="",
        first_name="",
    ):
        self.users.update_one(
            {"user_id": int(user_id)},
            {
                "$set": {
                    "username": username or "",
                    "first_name": first_name or "",
                },
                "$setOnInsert": {
                    "created_at": datetime.now(timezone.utc),
                    "timezone": "Asia/Kolkata",
                },
            },
            upsert=True,
        )

    def get_user_timezone(self, user_id):
        row = self.users.find_one(
            {"user_id": int(user_id)},
            {
                "_id": 0,
                "timezone": 1,
            },
        )

        return (row or {}).get("timezone") or "Asia/Kolkata"

    def set_user_timezone(
        self,
        user_id,
        timezone_name,
    ):
        self.users.update_one(
            {"user_id": int(user_id)},
            {
                "$set": {
                    "timezone": timezone_name,
                }
            },
            upsert=True,
        )

    def list_users(self):
        return list(
            self.users.find(
                {},
                {"_id": 0},
            ).sort(
                "created_at",
                ASCENDING,
            )
        )

    # =====================================================
    # BAN SYSTEM - SHARED
    # =====================================================

    def is_banned(self, user_id):
        return (
            self.banned_users.find_one(
                {"user_id": int(user_id)},
                {"_id": 1},
            )
            is not None
        )

    def ban_user(
        self,
        user_id,
        reason="Manual Ban",
    ):
        self.banned_users.update_one(
            {"user_id": int(user_id)},
            {
                "$set": {
                    "user_id": int(user_id),
                    "reason": reason or "Manual Ban",
                    "banned_at": datetime.now(timezone.utc),
                }
            },
            upsert=True,
        )

    def unban_user(self, user_id):
        self.banned_users.delete_one(
            {"user_id": int(user_id)}
        )

    def list_banned_users(self):
        return list(
            self.banned_users.find(
                {},
                {
                    "_id": 0,
                    "user_id": 1,
                    "reason": 1,
                    "banned_at": 1,
                },
            ).sort(
                "banned_at",
                ASCENDING,
            )
        )

    # =====================================================
    # ADMIN SYSTEM - BOT SPECIFIC
    # =====================================================

    def add_admin(self, user_id):
        user_id = int(user_id)

        self.add_user(user_id)

        self.admins.update_one(
            {"user_id": user_id},
            {
                "$set": {
                    "user_id": user_id,
                }
            },
            upsert=True,
        )

    def delete_user(self, user_id):
        self.users.delete_one(
            {"user_id": int(user_id)}
        )

    def remove_admin(self, user_id):
        self.admins.delete_one(
            {"user_id": int(user_id)}
        )

    def is_admin(self, user_id):
        return (
            self.admins.find_one(
                {"user_id": int(user_id)},
                {"_id": 1},
            )
            is not None
        )

    def list_admins(self):
        out = []

        for row in self.admins.find(
            {},
            {"_id": 0},
        ).sort(
            "user_id",
            ASCENDING,
        ):
            uid = int(row["user_id"])

            info = self.users.find_one(
                {"user_id": uid},
                {
                    "_id": 0,
                    "username": 1,
                    "first_name": 1,
                },
            ) or {}

            out.append(
                {
                    "user_id": uid,
                    "username": info.get("username", ""),
                    "first_name": info.get("first_name", ""),
                }
            )

        return out

    # =====================================================
    # PREMIUM SYSTEM - SHARED
    # =====================================================

    def add_premium(
        self,
        user_id,
        days,
    ):
        user_id = int(user_id)
        now = datetime.now(timezone.utc)

        old = self.get_premium(user_id)
        base = now

        if old:
            try:
                base = max(
                    now,
                    self._dt(old["expires_at"]),
                )
            except Exception:
                pass

        start = now
        expiry = base + timedelta(days=int(days))

        self.premium.update_one(
            {"user_id": user_id},
            {
                "$set": {
                    "starts_at": start,
                    "expires_at": expiry,
                }
            },
            upsert=True,
        )

        return start, expiry

    def remove_premium(self, user_id):
        old = self.get_premium(user_id)

        self.premium.delete_one(
            {"user_id": int(user_id)}
        )

        return old

    def get_premium(self, user_id):
        row = self.premium.find_one(
            {"user_id": int(user_id)},
            {"_id": 0},
        )

        if not row:
            return None

        try:
            expiry = self._dt(row["expires_at"])

            if expiry <= datetime.now(timezone.utc):
                self.premium.delete_one(
                    {"user_id": int(user_id)}
                )
                return None

        except Exception:
            return None

        return row

    def list_premium(self):
        return list(
            self.premium.find(
                {},
                {"_id": 0},
            ).sort(
                "expires_at",
                ASCENDING,
            )
        )

    def is_premium(self, user_id):
        return self.get_premium(user_id) is not None

    # =====================================================
    # SETTINGS - BOT SPECIFIC
    # =====================================================

    def set_setting(
        self,
        key,
        value,
    ):
        self.settings.update_one(
            {"key": key},
            {
                "$set": {
                    "value": str(value),
                }
            },
            upsert=True,
        )

    def get_setting(
        self,
        key,
        default=None,
    ):
        row = self.settings.find_one(
            {"key": key},
            {
                "_id": 0,
                "value": 1,
            },
        )

        return (
            row.get("value", default)
            if row
            else default
        )

    # =====================================================
    # FILE SYSTEM - SHARED
    # =====================================================

    def add_file(
        self,
        channel_id,
        message_id,
        caption="",
    ):
        existing = self.files.find_one(
            {
                "channel_id": int(channel_id),
                "message_id": int(message_id),
            },
            {
                "_id": 0,
                "file_id": 1,
            },
        )

        if existing:
            return existing["file_id"]

        file_id = uuid.uuid4().hex[:12]

        try:
            self.files.insert_one(
                {
                    "file_id": file_id,
                    "channel_id": int(channel_id),
                    "message_id": int(message_id),
                    "caption": caption or "",
                }
            )

        except Exception:
            existing = self.files.find_one(
                {
                    "channel_id": int(channel_id),
                    "message_id": int(message_id),
                },
                {
                    "_id": 0,
                    "file_id": 1,
                },
            )

            if existing:
                return existing["file_id"]

            raise

        return file_id

    def get_file(self, file_id):
        return self.files.find_one(
            {"file_id": file_id},
            {"_id": 0},
        )

    def list_files_between(
        self,
        channel_id,
        first_message_id,
        last_message_id,
    ):
        return list(
            self.files.find(
                {
                    "channel_id": int(channel_id),
                    "message_id": {
                        "$gte": int(first_message_id),
                        "$lte": int(last_message_id),
                    },
                },
                {"_id": 0},
            ).sort(
                "message_id",
                ASCENDING,
            )
        )

    # =====================================================
    # BATCH SYSTEM - SHARED
    # =====================================================

    def create_batch(self, file_ids):
        batch_id = uuid.uuid4().hex[:12]

        self.batches.insert_one(
            {
                "batch_id": batch_id,
                "created_at": datetime.now(timezone.utc),
            }
        )

        rows = [
            {
                "batch_id": batch_id,
                "file_id": fid,
                "position": pos,
            }
            for pos, fid in enumerate(file_ids)
        ]

        if rows:
            self.batch_items.insert_many(rows)

        return batch_id

    def get_batch_items(self, batch_id):
        result = []

        for item in self.batch_items.find(
            {"batch_id": batch_id},
            {"_id": 0},
        ).sort(
            "position",
            ASCENDING,
        ):
            row = self.get_file(item["file_id"])

            if row:
                result.append(row)

        return result

    # =====================================================
    # MAIN LINKS - SHARED
    # =====================================================

    def create_main_link(self, target):
        token = uuid.uuid4().hex

        self.main_links.insert_one(
            {
                "token": token,
                "target": target,
            }
        )

        return token

    def get_main_link(self, token):
        return self.main_links.find_one(
            {"token": token},
            {"_id": 0},
        )

    # =====================================================
    # TOKEN SYSTEM - BOT SPECIFIC
    # =====================================================

    def create_token(
        self,
        user_id,
        target,
        hours=2,
    ):
        token = uuid.uuid4().hex

        now = datetime.now(timezone.utc)
        expires = now + timedelta(hours=hours)

        self.tokens.insert_one(
            {
                "token": token,
                "user_id": int(user_id),
                "target": target,
                "created_at": now,
                "expires_at": expires,
                "used": False,
                "bot_id": self.bot_id,
            }
        )

        return token

    def get_token(self, token):
        return self.tokens.find_one(
            {"token": token},
            {"_id": 0},
        )

    def get_token_age_seconds(self, token):
        row = self.tokens.find_one(
            {"token": token},
            {
                "_id": 0,
                "created_at": 1,
            },
        )

        if not row:
            return None

        created_at = row.get("created_at")

        if not created_at:
            return None

        try:
            created_at = self._dt(created_at)

            age = (
                datetime.now(timezone.utc)
                - created_at
            ).total_seconds()

            return max(0.0, age)

        except Exception:
            return None

    def consume_token(
        self,
        token,
        user_id,
    ):
        row = self.get_token(token)

        if (
            not row
            or row.get("used")
            or int(row.get("user_id", -1)) != int(user_id)
        ):
            return None

        try:
            if self._dt(row["expires_at"]) <= datetime.now(
                timezone.utc
            ):
                return None

        except Exception:
            return None

        result = self.tokens.find_one_and_update(
            {
                "token": token,
                "user_id": int(user_id),
                "used": False,
            },
            {
                "$set": {
                    "used": True,
                    "used_at": datetime.now(timezone.utc),
                }
            },
            return_document=ReturnDocument.AFTER,
        )

        return row["target"] if result else None

    # =====================================================
    # FORCE SUBSCRIPTION - BOT SPECIFIC
    # =====================================================

    def add_fsub(
        self,
        channel_id,
        invite_link="",
        title="",
    ):
        self.fsub_channels.update_one(
            {
                "channel_id": str(channel_id)
            },
            {
                "$set": {
                    "invite_link": invite_link or "",
                    "title": title or str(channel_id),
                }
            },
            upsert=True,
        )

    def del_fsub(self, channel_id):
        self.fsub_channels.delete_one(
            {"channel_id": str(channel_id)}
        )

    def list_fsub(self):
        return list(
            self.fsub_channels.find(
                {},
                {"_id": 0},
            ).sort(
                "title",
                ASCENDING,
            )
        )

    # =====================================================
    # BROADCAST - BOT SPECIFIC
    # =====================================================

    def create_broadcast(
        self,
        message_id,
        delete_at=None,
    ):
        bid = uuid.uuid4().hex[:12]

        self.broadcasts.insert_one(
            {
                "broadcast_id": bid,
                "message_id": int(message_id),
                "delete_at": delete_at,
                "bot_id": self.bot_id,
            }
        )

        return bid
