import sqlite3
import uuid
from datetime import datetime, timedelta, timezone


class Database:
    def __init__(self, path="bot.db"):
        self.path = path
        self.init()

    def connect(self):
        c = sqlite3.connect(self.path, timeout=30)
        c.row_factory = sqlite3.Row
        return c

    def init(self):
        with self.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS admins(user_id INTEGER PRIMARY KEY)")
            db.execute("CREATE TABLE IF NOT EXISTS subscriptions(user_id INTEGER PRIMARY KEY, expires_at TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS premium(user_id INTEGER PRIMARY KEY, expires_at TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS banned_users(user_id INTEGER PRIMARY KEY, banned_by INTEGER, created_at TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS users(user_id INTEGER PRIMARY KEY, username TEXT, first_name TEXT, created_at TEXT NOT NULL, last_seen TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS fsub_channels(channel_id TEXT PRIMARY KEY, invite_link TEXT, title TEXT)")
            db.execute("CREATE TABLE IF NOT EXISTS requests(request_id TEXT PRIMARY KEY, user_id INTEGER, text TEXT, created_at TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT)")
            db.execute("""CREATE TABLE IF NOT EXISTS files(
                file_id TEXT PRIMARY KEY, channel_id INTEGER NOT NULL,
                message_id INTEGER NOT NULL, caption TEXT)""")
            db.execute("""CREATE TABLE IF NOT EXISTS batches(
                batch_id TEXT PRIMARY KEY, created_at TEXT NOT NULL)""")
            db.execute("""CREATE TABLE IF NOT EXISTS batch_items(
                batch_id TEXT NOT NULL, file_id TEXT NOT NULL,
                position INTEGER NOT NULL, PRIMARY KEY(batch_id,file_id))""")
            db.execute("""CREATE TABLE IF NOT EXISTS tokens(
                token TEXT PRIMARY KEY, user_id INTEGER NOT NULL,
                target TEXT NOT NULL, expires_at TEXT NOT NULL,
                used INTEGER DEFAULT 0)""")

    def add_user(self, user_id, username="", first_name=""):
        now = datetime.now(timezone.utc).isoformat()
        with self.connect() as db:
            db.execute("""INSERT INTO users(user_id,username,first_name,created_at,last_seen)
                         VALUES(?,?,?,?,?)
                         ON CONFLICT(user_id) DO UPDATE SET
                         username=excluded.username, first_name=excluded.first_name,
                         last_seen=excluded.last_seen""",
                       (user_id, username, first_name, now, now))

    def list_users(self):
        with self.connect() as db:
            return [r["user_id"] for r in db.execute("SELECT user_id FROM users")]

    def user_count(self):
        with self.connect() as db:
            return db.execute("SELECT COUNT(*) FROM users").fetchone()[0]

    def add_admin(self, user_id):
        with self.connect() as db:
            db.execute("INSERT OR IGNORE INTO admins VALUES(?)", (user_id,))

    def remove_admin(self, user_id):
        with self.connect() as db:
            db.execute("DELETE FROM admins WHERE user_id=?", (user_id,))

    def is_admin(self, user_id):
        with self.connect() as db:
            return db.execute("SELECT 1 FROM admins WHERE user_id=?", (user_id,)).fetchone() is not None

    def list_admins(self):
        with self.connect() as db:
            return [r["user_id"] for r in db.execute("SELECT user_id FROM admins ORDER BY user_id")]

    def _add_time(self, table, user_id, days):
        now = datetime.now(timezone.utc)
        with self.connect() as db:
            r = db.execute(f"SELECT expires_at FROM {table} WHERE user_id=?", (user_id,)).fetchone()
            base = now
            if r:
                try:
                    base = max(now, datetime.fromisoformat(r["expires_at"]))
                except ValueError:
                    pass
            expires = base + timedelta(days=days)
            db.execute(f"""INSERT INTO {table}(user_id,expires_at) VALUES(?,?)
                          ON CONFLICT(user_id) DO UPDATE SET expires_at=excluded.expires_at""",
                       (user_id, expires.isoformat()))

    def add_subscription(self, user_id, days):
        self._add_time("subscriptions", user_id, days)

    def remove_subscription(self, user_id):
        with self.connect() as db:
            db.execute("DELETE FROM subscriptions WHERE user_id=?", (user_id,))

    def is_subscribed(self, user_id):
        with self.connect() as db:
            r = db.execute("SELECT expires_at FROM subscriptions WHERE user_id=?", (user_id,)).fetchone()
        return bool(r and datetime.fromisoformat(r["expires_at"]) > datetime.now(timezone.utc))

    def add_premium(self, user_id, days):
        self._add_time("premium", user_id, days)

    def remove_premium(self, user_id):
        with self.connect() as db:
            db.execute("DELETE FROM premium WHERE user_id=?", (user_id,))

    def is_premium(self, user_id):
        if self.is_subscribed(user_id):
            return True
        with self.connect() as db:
            r = db.execute("SELECT expires_at FROM premium WHERE user_id=?", (user_id,)).fetchone()
        return bool(r and datetime.fromisoformat(r["expires_at"]) > datetime.now(timezone.utc))

    def get_premium(self, user_id):
        with self.connect() as db:
            return db.execute("SELECT * FROM premium WHERE user_id=?", (user_id,)).fetchone()

    def list_premium(self):
        now = datetime.now(timezone.utc).isoformat()
        with self.connect() as db:
            return db.execute("SELECT * FROM premium WHERE expires_at > ? ORDER BY expires_at", (now,)).fetchall()

    def ban_user(self, user_id, banned_by):
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO banned_users VALUES(?,?,?)",
                       (user_id, banned_by, datetime.now(timezone.utc).isoformat()))

    def unban_user(self, user_id):
        with self.connect() as db:
            db.execute("DELETE FROM banned_users WHERE user_id=?", (user_id,))

    def is_banned(self, user_id):
        with self.connect() as db:
            return db.execute("SELECT 1 FROM banned_users WHERE user_id=?", (user_id,)).fetchone() is not None

    def list_banned(self):
        with self.connect() as db:
            return db.execute("SELECT * FROM banned_users ORDER BY created_at DESC").fetchall()

    def add_fsub(self, channel_id, invite_link, title):
        with self.connect() as db:
            db.execute("""INSERT INTO fsub_channels VALUES(?,?,?)
                         ON CONFLICT(channel_id) DO UPDATE SET
                         invite_link=excluded.invite_link,title=excluded.title""",
                       (str(channel_id), invite_link, title))

    def del_fsub(self, channel_id):
        with self.connect() as db:
            db.execute("DELETE FROM fsub_channels WHERE channel_id=?", (str(channel_id),))

    def list_fsub(self):
        with self.connect() as db:
            return db.execute("SELECT * FROM fsub_channels ORDER BY title").fetchall()

    def add_request(self, user_id, text):
        rid = uuid.uuid4().hex[:10]
        with self.connect() as db:
            db.execute("INSERT INTO requests VALUES(?,?,?,?)",
                       (rid, user_id, text, datetime.now(timezone.utc).isoformat()))
        return rid

    def set_setting(self, key, value):
        with self.connect() as db:
            db.execute("""INSERT INTO settings VALUES(?,?)
                         ON CONFLICT(key) DO UPDATE SET value=excluded.value""",
                       (key, value))

    def get_setting(self, key, default=None):
        with self.connect() as db:
            r = db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return r["value"] if r else default

    def add_file(self, channel_id, message_id, caption=""):
        fid = uuid.uuid4().hex[:12]
        with self.connect() as db:
            db.execute("INSERT INTO files VALUES(?,?,?,?)",
                       (fid, channel_id, message_id, caption))
        return fid

    def get_file(self, file_id):
        with self.connect() as db:
            return db.execute("SELECT * FROM files WHERE file_id=?", (file_id,)).fetchone()

    def create_batch(self, file_ids):
        bid = uuid.uuid4().hex[:12]
        with self.connect() as db:
            db.execute("INSERT INTO batches VALUES(?,?)",
                       (bid, datetime.now(timezone.utc).isoformat()))
            for i, fid in enumerate(file_ids):
                db.execute("INSERT INTO batch_items VALUES(?,?,?)", (bid, fid, i))
        return bid

    def get_batch_items(self, batch_id):
        with self.connect() as db:
            return db.execute("""SELECT f.* FROM batch_items b
                JOIN files f ON f.file_id=b.file_id
                WHERE b.batch_id=? ORDER BY b.position""", (batch_id,)).fetchall()

    def create_token(self, user_id, target, hours=2):
        token = uuid.uuid4().hex
        expires = datetime.now(timezone.utc) + timedelta(hours=hours)
        with self.connect() as db:
            db.execute("INSERT INTO tokens VALUES(?,?,?,?,0)",
                       (token, user_id, target, expires.isoformat()))
        return token

    def consume_token(self, token):
        with self.connect() as db:
            r = db.execute("SELECT * FROM tokens WHERE token=?", (token,)).fetchone()
            if not r or r["used"]:
                return None
            if datetime.fromisoformat(r["expires_at"]) <= datetime.now(timezone.utc):
                return None
            db.execute("UPDATE tokens SET used=1 WHERE token=?", (token,))
            return r["user_id"], r["target"]

    def stats(self):
        with self.connect() as db:
            return {
                "files": db.execute("SELECT COUNT(*) FROM files").fetchone()[0],
                "batches": db.execute("SELECT COUNT(*) FROM batches").fetchone()[0],
                "admins": db.execute("SELECT COUNT(*) FROM admins").fetchone()[0],
                "premium": db.execute("SELECT COUNT(*) FROM premium WHERE expires_at > ?", (datetime.now(timezone.utc).isoformat(),)).fetchone()[0],
                "users": db.execute("SELECT COUNT(*) FROM users").fetchone()[0],
                "banned": db.execute("SELECT COUNT(*) FROM banned_users").fetchone()[0],
            }

