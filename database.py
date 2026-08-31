import sqlite3
import uuid
from datetime import datetime, timedelta, timezone

class Database:
    def __init__(self, path="bot.db"):
        self.path = path
        self.init()

    def connect(self):
        c = sqlite3.connect(self.path)
        c.row_factory = sqlite3.Row
        return c

    def init(self):
        with self.connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS admins(
                user_id INTEGER PRIMARY KEY
            )""")
            db.execute("""CREATE TABLE IF NOT EXISTS subscriptions(
                user_id INTEGER PRIMARY KEY,
                expires_at TEXT NOT NULL
            )""")
            db.execute("""CREATE TABLE IF NOT EXISTS files(
                file_id TEXT PRIMARY KEY,
                channel_id INTEGER NOT NULL,
                message_id INTEGER NOT NULL,
                caption TEXT
            )""")
            db.execute("""CREATE TABLE IF NOT EXISTS batches(
                batch_id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL
            )""")
            db.execute("""CREATE TABLE IF NOT EXISTS batch_items(
                batch_id TEXT NOT NULL,
                file_id TEXT NOT NULL,
                position INTEGER NOT NULL,
                PRIMARY KEY(batch_id, file_id)
            )""")
            db.execute("""CREATE TABLE IF NOT EXISTS tokens(
                token TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                target TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                used INTEGER DEFAULT 0
            )""")

    def add_admin(self, user_id):
        with self.connect() as db:
            db.execute("INSERT OR IGNORE INTO admins(user_id) VALUES(?)", (user_id,))

    def remove_admin(self, user_id):
        with self.connect() as db:
            db.execute("DELETE FROM admins WHERE user_id=?", (user_id,))

    def is_admin(self, user_id):
        with self.connect() as db:
            return db.execute("SELECT 1 FROM admins WHERE user_id=?", (user_id,)).fetchone() is not None

    def list_admins(self):
        with self.connect() as db:
            return [r["user_id"] for r in db.execute("SELECT user_id FROM admins ORDER BY user_id")]

    def add_subscription(self, user_id, days):
        now = datetime.now(timezone.utc)
        with self.connect() as db:
            old = db.execute("SELECT expires_at FROM subscriptions WHERE user_id=?", (user_id,)).fetchone()
            if old:
                try:
                    base = max(now, datetime.fromisoformat(old["expires_at"]))
                except ValueError:
                    base = now
            else:
                base = now
            expires = base + timedelta(days=days)
            db.execute(
                "INSERT INTO subscriptions VALUES(?,?) "
                "ON CONFLICT(user_id) DO UPDATE SET expires_at=excluded.expires_at",
                (user_id, expires.isoformat())
            )

    def remove_subscription(self, user_id):
        with self.connect() as db:
            db.execute("DELETE FROM subscriptions WHERE user_id=?", (user_id,))

    def is_subscribed(self, user_id):
        with self.connect() as db:
            r = db.execute("SELECT expires_at FROM subscriptions WHERE user_id=?", (user_id,)).fetchone()
        return bool(r and datetime.fromisoformat(r["expires_at"]) > datetime.now(timezone.utc))

    def add_file(self, channel_id, message_id, caption=""):
        fid = uuid.uuid4().hex[:12]
        with self.connect() as db:
            db.execute("INSERT INTO files VALUES(?,?,?,?)", (fid, channel_id, message_id, caption))
        return fid

    def get_file(self, file_id):
        with self.connect() as db:
            return db.execute("SELECT * FROM files WHERE file_id=?", (file_id,)).fetchone()

    def create_batch(self, file_ids):
        bid = uuid.uuid4().hex[:12]
        with self.connect() as db:
            db.execute("INSERT INTO batches VALUES(?,?)", (bid, datetime.now(timezone.utc).isoformat()))
            for i, fid in enumerate(file_ids):
                db.execute("INSERT INTO batch_items VALUES(?,?,?)", (bid, fid, i))
        return bid

    def get_batch_items(self, batch_id):
        with self.connect() as db:
            return db.execute("""
                SELECT f.* FROM batch_items b
                JOIN files f ON f.file_id=b.file_id
                WHERE b.batch_id=? ORDER BY b.position
            """, (batch_id,)).fetchall()

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
                "subscriptions": db.execute(
                    "SELECT COUNT(*) FROM subscriptions WHERE expires_at > ?",
                    (datetime.now(timezone.utc).isoformat(),)
                ).fetchone()[0],
            }
