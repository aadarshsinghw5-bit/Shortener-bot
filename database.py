import os
import uuid
from datetime import datetime, timedelta, timezone

from supabase import create_client


class Database:
    def __init__(self):
        self.supabase_url = os.environ["SUPABASE_URL"]
        self.supabase_key = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
        self.client = create_client(self.supabase_url, self.supabase_key)
        self.bot_key = os.environ["BOT_USERNAME"].lstrip("@").strip().lower()
        self.bot_username = os.environ["BOT_USERNAME"].lstrip("@").strip()

    @staticmethod
    def now():
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def parse_dt(value):
        if not value:
            return None
        if isinstance(value, datetime):
            return value
        try:
            return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except Exception:
            return None

    # ==================== USERS (PER BOT) ====================
    def add_user(self, user_id, username="", first_name=""):
        user_id = int(user_id)
        existing = (self.client.table("users").select("user_id")
                    .eq("bot_key", self.bot_key).eq("user_id", user_id)
                    .limit(1).execute())
        data = {"user_id": user_id, "username": username or "",
                "first_name": first_name or "", "bot_key": self.bot_key}
        if existing.data:
            (self.client.table("users").update({"username": username or "",
                "first_name": first_name or ""}).eq("bot_key", self.bot_key)
                .eq("user_id", user_id).execute())
        else:
            self.client.table("users").insert(data).execute()

    def list_users(self):
        result = (self.client.table("users").select("*")
                  .eq("bot_key", self.bot_key).order("user_id").execute())
        return result.data or []

    # ==================== BANNED (GLOBAL) ====================
    def is_banned(self, user_id):
        result = (self.client.table("banned_users").select("user_id")
                  .eq("user_id", int(user_id)).limit(1).execute())
        return bool(result.data)

    def ban_user(self, user_id):
        user_id = int(user_id)
        if self.is_banned(user_id):
            return
        self.client.table("banned_users").insert({
            "user_id": user_id, "banned_at": self.now()
        }).execute()

    def unban_user(self, user_id):
        self.client.table("banned_users").delete().eq("user_id", int(user_id)).execute()

    def list_banned_users(self):
        result = (self.client.table("banned_users").select("*")
                  .order("user_id").execute())
        return result.data or []

    # ==================== ADMINS (PER BOT) ====================
    def add_admin(self, user_id):
        user_id = int(user_id)
        existing = (self.client.table("admins").select("user_id")
                    .eq("bot_key", self.bot_key).eq("user_id", user_id)
                    .limit(1).execute())
        if not existing.data:
            self.client.table("admins").insert({
                "user_id": user_id, "bot_key": self.bot_key
            }).execute()

    def delete_user(self, user_id):
        (self.client.table("users").delete().eq("bot_key", self.bot_key)
         .eq("user_id", int(user_id)).execute())

    def remove_admin(self, user_id):
        (self.client.table("admins").delete().eq("bot_key", self.bot_key)
         .eq("user_id", int(user_id)).execute())

    def is_admin(self, user_id):
        result = (self.client.table("admins").select("user_id")
                  .eq("bot_key", self.bot_key).eq("user_id", int(user_id))
                  .limit(1).execute())
        return bool(result.data)

    def list_admins(self):
        result = (self.client.table("admins").select("*")
                  .eq("bot_key", self.bot_key).order("user_id").execute())
        return result.data or []

    # ==================== PREMIUM (GLOBAL) ====================
    def add_premium(self, user_id, days):
        user_id, days = int(user_id), int(days)
        now = datetime.now(timezone.utc)
        existing = (self.client.table("premium").select("*")
                    .eq("user_id", user_id).limit(1).execute())
        if existing.data:
            row = existing.data[0]
            old_expiry = self.parse_dt(row.get("expires_at"))
            if old_expiry and old_expiry > now:
                started_at = row.get("started_at") or now.isoformat()
                expiry = old_expiry + timedelta(days=days)
            else:
                started_at = now.isoformat()
                expiry = now + timedelta(days=days)
            (self.client.table("premium").update({
                "started_at": started_at, "expires_at": expiry.isoformat()
            }).eq("user_id", user_id).execute())
        else:
            expiry = now + timedelta(days=days)
            self.client.table("premium").insert({
                "user_id": user_id, "started_at": now.isoformat(),
                "expires_at": expiry.isoformat()
            }).execute()

    def remove_premium(self, user_id):
        self.client.table("premium").delete().eq("user_id", int(user_id)).execute()

    def get_premium(self, user_id):
        result = (self.client.table("premium").select("*")
                  .eq("user_id", int(user_id)).limit(1).execute())
        if not result.data:
            return None
        row = result.data[0]
        expiry = self.parse_dt(row.get("expires_at"))
        if expiry and expiry <= datetime.now(timezone.utc):
            self.remove_premium(user_id)
            return None
        return row

    def list_premium(self):
        result = self.client.table("premium").select("*").order("expires_at").execute()
        now = datetime.now(timezone.utc)
        return [r for r in (result.data or [])
                if (self.parse_dt(r.get("expires_at")) or now) > now]

    def is_premium(self, user_id):
        return self.get_premium(user_id) is not None

    # ==================== SETTINGS (PER BOT) ====================
    def set_setting(self, key, value):
        key = str(key)
        existing = (self.client.table("settings").select("key")
                    .eq("bot_key", self.bot_key).eq("key", key)
                    .limit(1).execute())
        if existing.data:
            (self.client.table("settings").update({"value": str(value)})
             .eq("bot_key", self.bot_key).eq("key", key).execute())
        else:
            self.client.table("settings").insert({
                "key": key, "value": str(value), "bot_key": self.bot_key
            }).execute()

    def get_setting(self, key, default=None):
        result = (self.client.table("settings").select("value")
                  .eq("bot_key", self.bot_key).eq("key", str(key))
                  .limit(1).execute())
        return result.data[0].get("value", default) if result.data else default

    # ==================== FILES (GLOBAL / SHARED) ====================
    def add_file(self, channel_id, message_id, caption=""):
        file_id = uuid.uuid4().hex
        self.client.table("files").insert({
            "file_id": file_id, "channel_id": int(channel_id),
            "message_id": int(message_id), "caption": caption or ""
        }).execute()
        return file_id

    def get_file(self, file_id):
        result = (self.client.table("files").select("*")
                  .eq("file_id", str(file_id)).limit(1).execute())
        return result.data[0] if result.data else None

    def list_files_between(self, channel_id, first_message_id, last_message_id):
        first_message_id, last_message_id = int(first_message_id), int(last_message_id)
        if first_message_id > last_message_id:
            first_message_id, last_message_id = last_message_id, first_message_id
        result = (self.client.table("files").select("*")
                  .eq("channel_id", int(channel_id))
                  .gte("message_id", first_message_id)
                  .lte("message_id", last_message_id)
                  .order("message_id").execute())
        return result.data or []

    # ==================== BATCHES (GLOBAL / SHARED) ====================
    def create_batch(self, file_ids):
        batch_id = uuid.uuid4().hex
        self.client.table("batches").insert({"batch_id": batch_id}).execute()
        rows = [{"batch_id": batch_id, "file_id": str(fid), "position": i}
                for i, fid in enumerate(file_ids)]
        if rows:
            self.client.table("batch_items").insert(rows).execute()
        return batch_id

    def get_batch_items(self, batch_id):
        result = (self.client.table("batch_items").select("*")
                  .eq("batch_id", str(batch_id)).order("position").execute())
        return result.data or []

    # ==================== MAIN LINKS (GLOBAL / SHARED) ====================
    def create_main_link(self, target):
        token = uuid.uuid4().hex
        self.client.table("main_links").insert({
            "token": token, "target": str(target), "created_at": self.now()
        }).execute()
        return token

    def get_main_link(self, token):
        result = (self.client.table("main_links").select("*")
                  .eq("token", str(token)).limit(1).execute())
        return result.data[0] if result.data else None

    # ==================== TOKENS (GLOBAL / SHARED) ====================
    def create_token(self, user_id, target, hours=2):
        token = uuid.uuid4().hex
        expires_at = datetime.now(timezone.utc) + timedelta(hours=int(hours))
        self.client.table("tokens").insert({
            "token": token, "user_id": int(user_id), "target": str(target),
            "expires_at": expires_at.isoformat(), "used": False,
            "bot_username": self.bot_username
        }).execute()
        return token

    def get_token(self, token):
        result = (self.client.table("tokens").select("*")
                  .eq("token", str(token)).limit(1).execute())
        if not result.data:
            return None
        row = result.data[0]
        expiry = self.parse_dt(row.get("expires_at"))
        if (expiry and expiry <= datetime.now(timezone.utc)) or row.get("used"):
            return None
        return row

    def consume_token(self, token, user_id):
        row = self.get_token(token)
        if not row or int(row.get("user_id")) != int(user_id):
            return None
        (self.client.table("tokens").update({"used": True})
         .eq("token", str(token)).eq("user_id", int(user_id)).execute())
        return row

    # ==================== FSUB (PER BOT) ====================
    def add_fsub(self, channel_id, invite_link="", title=""):
        channel_id = int(channel_id)
        existing = (self.client.table("fsub_channels").select("channel_id")
                    .eq("bot_key", self.bot_key).eq("channel_id", channel_id)
                    .limit(1).execute())
        data = {"channel_id": channel_id, "invite_link": invite_link or "",
                "title": title or "", "bot_key": self.bot_key}
        if existing.data:
            (self.client.table("fsub_channels").update({
                "invite_link": invite_link or "", "title": title or ""
            }).eq("bot_key", self.bot_key).eq("channel_id", channel_id).execute())
        else:
            self.client.table("fsub_channels").insert(data).execute()

    def del_fsub(self, channel_id):
        (self.client.table("fsub_channels").delete()
         .eq("bot_key", self.bot_key).eq("channel_id", int(channel_id)).execute())

    def list_fsub(self):
        result = (self.client.table("fsub_channels").select("*")
                  .eq("bot_key", self.bot_key).order("channel_id").execute())
        return result.data or []

    # ==================== BROADCASTS (PER BOT) ====================
    def create_broadcast(self, message_id, delete_at=None):
        broadcast_id = uuid.uuid4().hex
        data = {"broadcast_id": broadcast_id, "message_id": int(message_id),
                "bot_key": self.bot_key}
        if delete_at is not None:
            data["delete_at"] = delete_at.isoformat() if isinstance(delete_at, datetime) else str(delete_at)
        self.client.table("broadcasts").insert(data).execute()
        return broadcast_id
