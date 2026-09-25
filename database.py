import os
import uuid
from datetime import datetime, timedelta, timezone

from pymongo import MongoClient, ASCENDING
from pymongo.collection import ReturnDocument


class Database:
    def __init__(self):
        uri = os.environ.get("MONGO_URI", "").strip()
        if not uri:
            raise RuntimeError("MONGO_URI is not configured")

        self.client = MongoClient(uri, serverSelectionTimeoutMS=10000)
        db_name = os.environ.get("MONGO_DB", "file_store_bot").strip() or "file_store_bot"
        self.db = self.client[db_name]

        self.users = self.db["users"]
        self.banned_users = self.db["banned_users"]
        self.admins = self.db["admins"]
        self.premium = self.db["premium"]
        self.settings = self.db["settings"]
        self.files = self.db["files"]
        self.batches = self.db["batches"]
        self.batch_items = self.db["batch_items"]
        self.main_links = self.db["main_links"]
        self.tokens = self.db["tokens"]
        self.fsub_channels = self.db["fsub_channels"]
        self.broadcasts = self.db["broadcasts"]

        self.users.create_index([("user_id", ASCENDING)], unique=True)
        self.admins.create_index([("user_id", ASCENDING)], unique=True)
        self.banned_users.create_index([("user_id", ASCENDING)], unique=True)
        self.premium.create_index([("user_id", ASCENDING)], unique=True)
        self.settings.create_index([("key", ASCENDING)], unique=True)
        self.files.create_index([("file_id", ASCENDING)], unique=True)
        self.files.create_index([("channel_id", ASCENDING), ("message_id", ASCENDING)], unique=True)
        self.batches.create_index([("batch_id", ASCENDING)], unique=True)
        self.batch_items.create_index([("batch_id", ASCENDING), ("position", ASCENDING)], unique=True)
        self.main_links.create_index([("token", ASCENDING)], unique=True)
        self.tokens.create_index([("token", ASCENDING)], unique=True)
        self.fsub_channels.create_index([("channel_id", ASCENDING)], unique=True)
        self.broadcasts.create_index([("broadcast_id", ASCENDING)], unique=True)

    @staticmethod
    def _dt(value):
        if isinstance(value, datetime):
            dt = value
        else:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)

    def add_user(self, user_id, username="", first_name=""):
        self.users.update_one(
            {"user_id": int(user_id)},
            {
                "$set": {"username": username or "", "first_name": first_name or ""},
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
            {"_id": 0, "timezone": 1},
        )
        return (row or {}).get("timezone") or "Asia/Kolkata"

    def set_user_timezone(self, user_id, timezone_name):
        self.users.update_one(
            {"user_id": int(user_id)},
            {"$set": {"timezone": timezone_name}},
            upsert=True,
        )

    def list_users(self):
        return list(self.users.find({}, {"_id": 0}).sort("created_at", ASCENDING))

    # =========================
    # BAN SYSTEM
    # =========================
    def is_banned(self, user_id):
        return self.banned_users.find_one({"user_id": int(user_id)}, {"_id": 1}) is not None

    def ban_user(self, user_id):
        self.banned_users.update_one(
            {"user_id": int(user_id)},
            {"$set": {"user_id": int(user_id)}},
            upsert=True,
        )

    def unban_user(self, user_id):
        self.banned_users.delete_one({"user_id": int(user_id)})

    def list_banned_users(self):
        return list(
            self.banned_users.find({}, {"_id": 0, "user_id": 1}).sort("user_id", ASCENDING)
        )

    # =========================
    # ADMIN SYSTEM
    # =========================
    def add_admin(self, user_id):
        user_id = int(user_id)
        self.add_user(user_id)
        self.admins.update_one(
            {"user_id": user_id},
            {"$set": {"user_id": user_id}},
            upsert=True,
        )

    def delete_user(self, user_id):
        self.users.delete_one({"user_id": int(user_id)})

    def remove_admin(self, user_id):
        self.admins.delete_one({"user_id": int(user_id)})

    def is_admin(self, user_id):
        return self.admins.find_one({"user_id": int(user_id)}, {"_id": 1}) is not None

    def list_admins(self):
        out = []
        for row in self.admins.find({}, {"_id": 0}).sort("user_id", ASCENDING):
            uid = int(row["user_id"])
            info = self.users.find_one(
                {"user_id": uid},
                {"_id": 0, "username": 1, "first_name": 1},
            ) or {}
            out.append(
                {
                    "user_id": uid,
                    "username": info.get("username", ""),
                    "first_name": info.get("first_name", ""),
                }
            )
        return out

    # =========================
    # PREMIUM SYSTEM
    # =========================
    def add_premium(self, user_id, days):
        user_id = int(user_id)
        now = datetime.now(timezone.utc)
        old = self.get_premium(user_id)
        base = now
        if old:
            try:
                base = max(now, self._dt(old["expires_at"]))
            except Exception:
                pass
        start = now
        expiry = base + timedelta(days=int(days))
        self.premium.update_one(
            {"user_id": user_id},
            {"$set": {"starts_at": start, "expires_at": expiry}},
            upsert=True,
        )
        return start, expiry

    def remove_premium(self, user_id):
        old = self.get_premium(user_id)
        self.premium.delete_one({"user_id": int(user_id)})
        return old

    def get_premium(self, user_id):
        row = self.premium.find_one({"user_id": int(user_id)}, {"_id": 0})
        if not row:
            return None
        try:
            expiry = self._dt(row["expires_at"])
            if expiry <= datetime.now(timezone.utc):
                self.premium.delete_one({"user_id": int(user_id)})
                return None
        except Exception:
            return None
        return row

    def list_premium(self):
        return list(self.premium.find({}, {"_id": 0}).sort("expires_at", ASCENDING))

    def is_premium(self, user_id):
        return self.get_premium(user_id) is not None

    # =========================
    # SETTINGS
    # =========================
    def set_setting(self, key, value):
        self.settings.update_one(
            {"key": key},
            {"$set": {"value": str(value)}},
            upsert=True,
        )

    def get_setting(self, key, default=None):
        row = self.settings.find_one({"key": key}, {"_id": 0, "value": 1})
        return row.get("value", default) if row else default

    # =========================
    # FILE SYSTEM
    # =========================
    def add_file(self, channel_id, message_id, caption=""):
        existing = self.files.find_one(
            {"channel_id": int(channel_id), "message_id": int(message_id)},
            {"_id": 0, "file_id": 1},
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
                {"channel_id": int(channel_id), "message_id": int(message_id)},
                {"_id": 0, "file_id": 1},
            )
            if existing:
                return existing["file_id"]
            raise
        return file_id

    def get_file(self, file_id):
        return self.files.find_one({"file_id": file_id}, {"_id": 0})

    def list_files_between(self, channel_id, first_message_id, last_message_id):
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
            ).sort("message_id", ASCENDING)
        )

    # =========================
    # BATCH SYSTEM
    # =========================
    def create_batch(self, file_ids):
        batch_id = uuid.uuid4().hex[:12]
        self.batches.insert_one(
            {"batch_id": batch_id, "created_at": datetime.now(timezone.utc)}
        )
        rows = [
            {"batch_id": batch_id, "file_id": fid, "position": pos}
            for pos, fid in enumerate(file_ids)
        ]
        if rows:
            self.batch_items.insert_many(rows)
        return batch_id

    def get_batch_items(self, batch_id):
        result = []
        for item in self.batch_items.find(
            {"batch_id": batch_id}, {"_id": 0}
        ).sort("position", ASCENDING):
            row = self.get_file(item["file_id"])
            if row:
                result.append(row)
        return result

    # =========================
    # MAIN LINKS
    # =========================
    def create_main_link(self, target):
        token = uuid.uuid4().hex
        self.main_links.insert_one({"token": token, "target": target})
        return token

    def get_main_link(self, token):
        return self.main_links.find_one({"token": token}, {"_id": 0})

    # =========================
    # TOKEN SYSTEM
    # =========================
    def create_token(self, user_id, target, hours=2):
        token = uuid.uuid4().hex
        expires = datetime.now(timezone.utc) + timedelta(hours=hours)
        self.tokens.insert_one(
            {
                "token": token,
                "user_id": int(user_id),
                "target": target,
                "expires_at": expires,
                "used": False,
            }
        )
        return token

    def get_token(self, token):
        return self.tokens.find_one({"token": token}, {"_id": 0})

    def consume_token(self, token, user_id):
        row = self.get_token(token)
        if (
            not row
            or row.get("used")
            or int(row.get("user_id", -1)) != int(user_id)
        ):
            return None
        try:
            if self._dt(row["expires_at"]) <= datetime.now(timezone.utc):
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

    # =========================
    # FORCE SUBSCRIPTION
    # =========================
    def add_fsub(self, channel_id, invite_link="", title=""):
        self.fsub_channels.update_one(
            {"channel_id": str(channel_id)},
            {
                "$set": {
                    "invite_link": invite_link or "",
                    "title": title or str(channel_id),
                }
            },
            upsert=True,
        )

    def del_fsub(self, channel_id):
        self.fsub_channels.delete_one({"channel_id": str(channel_id)})

    def list_fsub(self):
        return list(self.fsub_channels.find({}, {"_id": 0}).sort("title", ASCENDING))

    # =========================
    # BROADCAST
    # =========================
    def create_broadcast(self, message_id, delete_at=None):
        bid = uuid.uuid4().hex[:12]
        self.broadcasts.insert_one(
            {
                "broadcast_id": bid,
                "message_id": int(message_id),
                "delete_at": delete_at,
            }
        )
        return bid
