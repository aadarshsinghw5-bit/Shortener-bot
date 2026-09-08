import os
import uuid
from datetime import datetime, timedelta, timezone
from supabase import create_client


class Database:
    def __init__(self):
        self.db = create_client(
            os.environ["SUPABASE_URL"],
            os.environ["SUPABASE_KEY"],
        )

    # USERS
    def add_user(self, user_id, username="", first_name=""):
        self.db.table("users").upsert({
            "user_id": int(user_id),
            "username": username or "",
            "first_name": first_name or "",
        }).execute()

    def is_banned(self, user_id):
        r = self.db.table("banned_users").select("user_id").eq(
            "user_id", int(user_id)
        ).limit(1).execute()
        return bool(r.data)

    # ADMINS
    def add_admin(self, user_id):
        self.db.table("admins").upsert({"user_id": int(user_id)}).execute()

    def remove_admin(self, user_id):
        self.db.table("admins").delete().eq("user_id", int(user_id)).execute()

    def is_admin(self, user_id):
        r = self.db.table("admins").select("user_id").eq(
            "user_id", int(user_id)
        ).limit(1).execute()
        return bool(r.data)

    # PREMIUM
    def add_premium(self, user_id, days):
        user_id = int(user_id)
        now = datetime.now(timezone.utc)
        old = self.get_premium(user_id)
        base = now

        if old:
            try:
                old_expiry = datetime.fromisoformat(
                    old["expires_at"].replace("Z", "+00:00")
                )
                if old_expiry.tzinfo is None:
                    old_expiry = old_expiry.replace(tzinfo=timezone.utc)
                base = max(now, old_expiry)
            except Exception:
                pass

        expiry = base + timedelta(days=int(days))
        self.db.table("premium").upsert({
            "user_id": user_id,
            "expires_at": expiry.isoformat(),
        }).execute()
        return expiry

    def remove_premium(self, user_id):
        old = self.get_premium(user_id)
        self.db.table("premium").delete().eq(
            "user_id", int(user_id)
        ).execute()
        return old

    def get_premium(self, user_id):
        r = self.db.table("premium").select("*").eq(
            "user_id", int(user_id)
        ).limit(1).execute()
        if not r.data:
            return None

        row = r.data[0]
        try:
            expiry = datetime.fromisoformat(
                row["expires_at"].replace("Z", "+00:00")
            )
            if expiry.tzinfo is None:
                expiry = expiry.replace(tzinfo=timezone.utc)
            if expiry <= datetime.now(timezone.utc):
                self.db.table("premium").delete().eq(
                    "user_id", int(user_id)
                ).execute()
                return None
        except Exception:
            return None
        return row

    def is_premium(self, user_id):
        return self.get_premium(user_id) is not None

    # SETTINGS
    def set_setting(self, key, value):
        self.db.table("settings").upsert({
            "key": key,
            "value": str(value),
        }).execute()

    def get_setting(self, key, default=None):
        r = self.db.table("settings").select("value").eq(
            "key", key
        ).limit(1).execute()
        return r.data[0]["value"] if r.data else default

    # FILES / DB CHANNEL
    def add_file(self, channel_id, message_id, caption=""):
        existing = self.db.table("files").select("file_id").eq(
            "channel_id", int(channel_id)
        ).eq("message_id", int(message_id)).limit(1).execute()

        if existing.data:
            return existing.data[0]["file_id"]

        file_id = uuid.uuid4().hex[:12]
        self.db.table("files").insert({
            "file_id": file_id,
            "channel_id": int(channel_id),
            "message_id": int(message_id),
            "caption": caption or "",
        }).execute()
        return file_id

    def get_file(self, file_id):
        r = self.db.table("files").select("*").eq(
            "file_id", file_id
        ).limit(1).execute()
        return r.data[0] if r.data else None

    def list_files_between(self, channel_id, first_message_id, last_message_id):
        r = self.db.table("files").select("*").eq(
            "channel_id", int(channel_id)
        ).gte("message_id", int(first_message_id)).lte(
            "message_id", int(last_message_id)
        ).order("message_id").execute()
        return r.data or []

    # BATCH
    def create_batch(self, file_ids):
        batch_id = uuid.uuid4().hex[:12]
        self.db.table("batches").insert({
            "batch_id": batch_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }).execute()

        rows = [
            {"batch_id": batch_id, "file_id": file_id, "position": pos}
            for pos, file_id in enumerate(file_ids)
        ]
        if rows:
            self.db.table("batch_items").insert(rows).execute()
        return batch_id

    def get_batch_items(self, batch_id):
        r = self.db.table("batch_items").select(
            "file_id, position"
        ).eq("batch_id", batch_id).order("position").execute()

        result = []
        for item in r.data or []:
            row = self.get_file(item["file_id"])
            if row:
                result.append(row)
        return result

    # TOKENS
    def create_token(self, user_id, target, hours=2):
        token = uuid.uuid4().hex
        expires = datetime.now(timezone.utc) + timedelta(hours=hours)
        self.db.table("tokens").insert({
            "token": token,
            "user_id": int(user_id),
            "target": target,
            "expires_at": expires.isoformat(),
            "used": False,
        }).execute()
        return token

    def consume_token(self, token):
        r = self.db.table("tokens").select("*").eq(
            "token", token
        ).limit(1).execute()
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

        # Conditional update makes the token single-use.
        updated = self.db.table("tokens").update({
            "used": True
        }).eq("token", token).eq("used", False).execute()

        if not updated.data:
            return None
        return row["user_id"], row["target"]

    # FSUB
    def add_fsub(self, channel_id, invite_link="", title=""):
        self.db.table("fsub_channels").upsert({
            "channel_id": str(channel_id),
            "invite_link": invite_link or "",
            "title": title or str(channel_id),
        }).execute()

    def del_fsub(self, channel_id):
        self.db.table("fsub_channels").delete().eq(
            "channel_id", str(channel_id)
        ).execute()

    def list_fsub(self):
        r = self.db.table("fsub_channels").select("*").order("title").execute()
        return r.data or []
