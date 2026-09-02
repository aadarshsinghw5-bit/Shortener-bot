import os
import uuid
from datetime import datetime, timedelta, timezone

from supabase import create_client


class Database:

    def __init__(self):
        url = os.environ["SUPABASE_URL"]
        key = os.environ["SUPABASE_KEY"]
        self.db = create_client(url, key)

    # =========================
    # USERS
    # =========================

    def add_user(self, user_id, username="", first_name=""):
        self.db.table("users").upsert({
            "user_id": user_id,
            "username": username or "",
            "first_name": first_name or "",
        }).execute()

    def list_users(self):
        r = self.db.table("users").select("user_id").execute()
        return [x["user_id"] for x in (r.data or [])]

    def user_count(self):
        r = self.db.table("users").select(
            "user_id", count="exact"
        ).execute()
        return r.count or 0

    # =========================
    # ADMINS
    # =========================

    def add_admin(self, user_id):
        self.db.table("admins").upsert({
            "user_id": user_id
        }).execute()

    def remove_admin(self, user_id):
        self.db.table("admins").delete().eq(
            "user_id", user_id
        ).execute()

    def is_admin(self, user_id):
        r = self.db.table("admins").select(
            "user_id"
        ).eq("user_id", user_id).limit(1).execute()

        return bool(r.data)

    def list_admins(self):
        r = self.db.table("admins").select(
            "user_id"
        ).order("user_id").execute()

        return [x["user_id"] for x in (r.data or [])]

    # =========================
    # PREMIUM
    # =========================

    def add_premium(self, user_id, days):
        now = datetime.now(timezone.utc)

        old = self.get_premium(user_id)

        if old:
            try:
                old_expiry = datetime.fromisoformat(
                    old["expires_at"].replace("Z", "+00:00")
                )
                base = max(now, old_expiry)
            except Exception:
                base = now
        else:
            base = now

        expires = base + timedelta(days=days)

        self.db.table("premium").upsert({
            "user_id": user_id,
            "expires_at": expires.isoformat()
        }).execute()

    def remove_premium(self, user_id):
        self.db.table("premium").delete().eq(
            "user_id", user_id
        ).execute()

    def get_premium(self, user_id):
        r = self.db.table("premium").select(
            "*"
        ).eq("user_id", user_id).limit(1).execute()

        if not r.data:
            return None

        row = r.data[0]

        try:
            expiry = datetime.fromisoformat(
                row["expires_at"].replace("Z", "+00:00")
            )

            if expiry <= datetime.now(timezone.utc):
                self.remove_premium(user_id)
                return None

        except Exception:
            return None

        return row

    def is_premium(self, user_id):
        return self.get_premium(user_id) is not None

    def list_premium(self):
        now = datetime.now(timezone.utc).isoformat()

        r = self.db.table("premium").select(
            "*"
        ).gt("expires_at", now).order("expires_at").execute()

        return r.data or []

    # =========================
    # OLD SUBSCRIPTION ALIASES
    # =========================

    def add_subscription(self, user_id, days):
        self.add_premium(user_id, days)

    def remove_subscription(self, user_id):
        self.remove_premium(user_id)

    def is_subscribed(self, user_id):
        return self.is_premium(user_id)

    # =========================
    # BANNED USERS
    # =========================

    def ban_user(self, user_id, banned_by):
        self.db.table("banned_users").upsert({
            "user_id": user_id,
            "banned_by": banned_by
        }).execute()

    def unban_user(self, user_id):
        self.db.table("banned_users").delete().eq(
            "user_id", user_id
        ).execute()

    def is_banned(self, user_id):
        r = self.db.table("banned_users").select(
            "user_id"
        ).eq("user_id", user_id).limit(1).execute()

        return bool(r.data)

    def list_banned(self):
        r = self.db.table("banned_users").select(
            "*"
        ).order("created_at").execute()

        return r.data or []

    # =========================
    # FILES
    # =========================

    def add_file(self, channel_id, message_id, caption=""):
        file_id = uuid.uuid4().hex[:12]

        self.db.table("files").insert({
            "file_id": file_id,
            "channel_id": int(channel_id),
            "message_id": int(message_id),
            "caption": caption or ""
        }).execute()

        return file_id

    def get_file(self, file_id):
        r = self.db.table("files").select(
            "*"
        ).eq("file_id", file_id).limit(1).execute()

        if not r.data:
            return None

        return r.data[0]

    # =========================
    # BATCHES
    # =========================

    def create_batch(self, file_ids):
        batch_id = uuid.uuid4().hex[:12]

        self.db.table("batches").insert({
            "batch_id": batch_id
        }).execute()

        rows = []

        for position, file_id in enumerate(file_ids):
            rows.append({
                "batch_id": batch_id,
                "file_id": file_id,
                "position": position
            })

        if rows:
            self.db.table("batch_items").insert(rows).execute()

        return batch_id

    def get_batch_items(self, batch_id):
        r = self.db.table("batch_items").select(
            "file_id, position"
        ).eq(
            "batch_id", batch_id
        ).order("position").execute()

        result = []

        for item in (r.data or []):
            file_row = self.get_file(item["file_id"])

            if file_row:
                result.append(file_row)

        return result

    # =========================
    # SHORTENER TOKENS
    # =========================

    def create_token(self, user_id, target, hours=2):
        token = uuid.uuid4().hex

        expires = (
            datetime.now(timezone.utc)
            + timedelta(hours=hours)
        )

        self.db.table("tokens").insert({
            "token": token,
            "user_id": int(user_id),
            "target": target,
            "expires_at": expires.isoformat(),
            "used": False
        }).execute()

        return token

    def consume_token(self, token):
        r = self.db.table("tokens").select(
            "*"
        ).eq("token", token).limit(1).execute()

        if not r.data:
            return None

        row = r.data[0]

        if row.get("used"):
            return None

        try:
            expiry = datetime.fromisoformat(
                row["expires_at"].replace("Z", "+00:00")
            )

            if expiry <= datetime.now(timezone.utc):
                return None

        except Exception:
            return None

        self.db.table("tokens").update({
            "used": True
        }).eq("token", token).execute()

        return row["user_id"], row["target"]

    # =========================
    # FSUB
    # =========================

    def add_fsub(
        self,
        channel_id,
        invite_link="",
        title=""
    ):
        self.db.table("fsub_channels").upsert({
            "channel_id": str(channel_id),
            "invite_link": invite_link or "",
            "title": title or str(channel_id)
        }).execute()

    def del_fsub(self, channel_id):
        self.db.table("fsub_channels").delete().eq(
            "channel_id", str(channel_id)
        ).execute()

    def list_fsub(self):
        r = self.db.table("fsub_channels").select(
            "*"
        ).order("title").execute()

        return r.data or []

    # =========================
    # REQUESTS
    # =========================

    def add_request(self, user_id, request):
        r = self.db.table("requests").insert({
            "user_id": int(user_id),
            "request": request
        }).execute()

        if r.data:
            return r.data[0]["id"]

        return None

    # =========================
    # SETTINGS
    # =========================

    def set_setting(self, key, value):
        self.db.table("settings").upsert({
            "key": key,
            "value": str(value)
        }).execute()

    def get_setting(self, key, default=None):
        r = self.db.table("settings").select(
            "value"
        ).eq("key", key).limit(1).execute()

        if not r.data:
            return default

        return r.data[0]["value"]

    def get_delete_seconds(self):
        value = self.get_setting(
            "delete_seconds",
            "600"
        )

        try:
            return max(0, int(value))
        except Exception:
            return 600

    # =========================
    # PENDING AUTO DELETE
    # =========================

    def add_pending_delete(
        self,
        chat_id,
        message_id,
        delete_at
    ):
        r = self.db.table("pending_deletes").insert({
            "chat_id": int(chat_id),
            "message_id": int(message_id),
            "delete_at": delete_at.isoformat(),
            "deleted": False
        }).execute()

        if r.data:
            return r.data[0]["id"]

        return None

    def get_pending_deletes(self):
        r = self.db.table("pending_deletes").select(
            "*"
        ).eq(
            "deleted", False
        ).execute()

        return r.data or []

    def mark_delete_done(self, delete_id):
        self.db.table("pending_deletes").update({
            "deleted": True
        }).eq("id", delete_id).execute()

    # =========================
    # STATISTICS
    # =========================

    def _count(self, table):
        r = self.db.table(table).select(
            "*",
            count="exact"
        ).limit(1).execute()

        return r.count or 0

    def stats(self):
        return {
            "files": self._count("files"),
            "batches": self._count("batches"),
            "admins": self._count("admins"),
            "premium": len(self.list_premium()),
            "users": self._count("users"),
            "banned": self._count("banned_users"),
        }
