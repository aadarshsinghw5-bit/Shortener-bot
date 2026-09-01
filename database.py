import sqlite3
import uuid
from datetime import datetime, timedelta, timezone


class Database:

    def __init__(self, path="bot.db"):
        self.path = path
        self.init()

    # =====================================================
    # CONNECTION
    # =====================================================

    def connect(self):
        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
        return db

    # =====================================================
    # DATABASE INIT
    # =====================================================

    def init(self):

        with self.connect() as db:

            # -------------------------------
            # ADMINS
            # -------------------------------

            db.execute("""
                CREATE TABLE IF NOT EXISTS admins(
                    user_id INTEGER PRIMARY KEY
                )
            """)

            # -------------------------------
            # USERS
            # -------------------------------

            db.execute("""
                CREATE TABLE IF NOT EXISTS users(
                    user_id INTEGER PRIMARY KEY,
                    username TEXT DEFAULT '',
                    first_name TEXT DEFAULT '',
                    joined_at TEXT NOT NULL
                )
            """)

            # -------------------------------
            # PREMIUM
            # -------------------------------

            db.execute("""
                CREATE TABLE IF NOT EXISTS premium(
                    user_id INTEGER PRIMARY KEY,
                    expires_at TEXT NOT NULL
                )
            """)

            # -------------------------------
            # OLD SUBSCRIPTIONS
            # -------------------------------
            # Kept for compatibility with
            # old commands.

            db.execute("""
                CREATE TABLE IF NOT EXISTS subscriptions(
                    user_id INTEGER PRIMARY KEY,
                    expires_at TEXT NOT NULL
                )
            """)

            # -------------------------------
            # FILES
            # -------------------------------

            db.execute("""
                CREATE TABLE IF NOT EXISTS files(
                    file_id TEXT PRIMARY KEY,
                    channel_id INTEGER NOT NULL,
                    message_id INTEGER NOT NULL,
                    caption TEXT
                )
            """)

            # -------------------------------
            # BATCHES
            # -------------------------------

            db.execute("""
                CREATE TABLE IF NOT EXISTS batches(
                    batch_id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL
                )
            """)

            # -------------------------------
            # BATCH ITEMS
            # -------------------------------

            db.execute("""
                CREATE TABLE IF NOT EXISTS batch_items(
                    batch_id TEXT NOT NULL,
                    file_id TEXT NOT NULL,
                    position INTEGER NOT NULL,
                    PRIMARY KEY(batch_id, file_id)
                )
            """)

            # -------------------------------
            # VERIFICATION TOKENS
            # -------------------------------

            db.execute("""
                CREATE TABLE IF NOT EXISTS tokens(
                    token TEXT PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    target TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    used INTEGER DEFAULT 0
                )
            """)

            # -------------------------------
            # BANNED USERS
            # -------------------------------

            db.execute("""
                CREATE TABLE IF NOT EXISTS banned_users(
                    user_id INTEGER PRIMARY KEY,
                    banned_by INTEGER,
                    banned_at TEXT NOT NULL
                )
            """)

            # -------------------------------
            # FORCE SUB CHANNELS
            # -------------------------------

            db.execute("""
                CREATE TABLE IF NOT EXISTS fsub_channels(
                    channel_id TEXT PRIMARY KEY,
                    invite_link TEXT,
                    title TEXT DEFAULT ''
                )
            """)

            # -------------------------------
            # REQUESTS
            # -------------------------------

            db.execute("""
                CREATE TABLE IF NOT EXISTS requests(
                    request_id TEXT PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    request_text TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
            """)

            # -------------------------------
            # SETTINGS
            # -------------------------------

            db.execute("""
                CREATE TABLE IF NOT EXISTS settings(
                    key TEXT PRIMARY KEY,
                    value TEXT
                )
            """)

            db.commit()

    # =====================================================
    # USERS
    # =====================================================

    def add_user(
        self,
        user_id,
        username="",
        first_name=""
    ):

        with self.connect() as db:

            db.execute("""
                INSERT INTO users(
                    user_id,
                    username,
                    first_name,
                    joined_at
                )
                VALUES(?,?,?,?)
                ON CONFLICT(user_id)
                DO UPDATE SET
                    username=excluded.username,
                    first_name=excluded.first_name
            """, (
                user_id,
                username or "",
                first_name or "",
                datetime.now(timezone.utc).isoformat()
            ))

    def list_users(self):

        with self.connect() as db:

            rows = db.execute("""
                SELECT user_id
                FROM users
                ORDER BY user_id
            """).fetchall()

            return [
                row["user_id"]
                for row in rows
            ]

    def user_count(self):

        with self.connect() as db:

            return db.execute(
                "SELECT COUNT(*) FROM users"
            ).fetchone()[0]

    # =====================================================
    # ADMINS
    # =====================================================

    def add_admin(self, user_id):

        with self.connect() as db:

            db.execute("""
                INSERT OR IGNORE INTO admins(user_id)
                VALUES(?)
            """, (user_id,))

    def remove_admin(self, user_id):

        with self.connect() as db:

            db.execute("""
                DELETE FROM admins
                WHERE user_id=?
            """, (user_id,))

    def is_admin(self, user_id):

        with self.connect() as db:

            row = db.execute("""
                SELECT 1
                FROM admins
                WHERE user_id=?
            """, (user_id,)).fetchone()

            return row is not None

    def list_admins(self):

        with self.connect() as db:

            rows = db.execute("""
                SELECT user_id
                FROM admins
                ORDER BY user_id
            """).fetchall()

            return [
                row["user_id"]
                for row in rows
            ]

    # =====================================================
    # PREMIUM
    # =====================================================

    def add_premium(self, user_id, days):

        now = datetime.now(timezone.utc)

        with self.connect() as db:

            old = db.execute("""
                SELECT expires_at
                FROM premium
                WHERE user_id=?
            """, (user_id,)).fetchone()

            if old:

                try:
                    old_expiry = datetime.fromisoformat(
                        old["expires_at"]
                    )

                    base = max(
                        now,
                        old_expiry
                    )

                except ValueError:

                    base = now

            else:

                base = now

            expires = (
                base +
                timedelta(days=days)
            )

            db.execute("""
                INSERT INTO premium(
                    user_id,
                    expires_at
                )
                VALUES(?,?)
                ON CONFLICT(user_id)
                DO UPDATE SET
                    expires_at=excluded.expires_at
            """, (
                user_id,
                expires.isoformat()
            ))

    def remove_premium(self, user_id):

        with self.connect() as db:

            db.execute("""
                DELETE FROM premium
                WHERE user_id=?
            """, (user_id,))

    def get_premium(self, user_id):

        with self.connect() as db:

            row = db.execute("""
                SELECT *
                FROM premium
                WHERE user_id=?
            """, (user_id,)).fetchone()

        if not row:
            return None

        try:

            expires = datetime.fromisoformat(
                row["expires_at"]
            )

            if expires <= datetime.now(timezone.utc):

                self.remove_premium(user_id)

                return None

        except ValueError:

            return None

        return row

    def is_premium(self, user_id):

        return self.get_premium(user_id) is not None

    def list_premium(self):

        now = datetime.now(timezone.utc).isoformat()

        with self.connect() as db:

            rows = db.execute("""
                SELECT *
                FROM premium
                WHERE expires_at > ?
                ORDER BY expires_at
            """, (now,)).fetchall()

            return rows

    # =====================================================
    # OLD SUBSCRIPTION COMPATIBILITY
    # =====================================================

    def add_subscription(
        self,
        user_id,
        days
    ):

        now = datetime.now(timezone.utc)

        with self.connect() as db:

            old = db.execute("""
                SELECT expires_at
                FROM subscriptions
                WHERE user_id=?
            """, (user_id,)).fetchone()

            if old:

                try:

                    base = max(
                        now,
                        datetime.fromisoformat(
                            old["expires_at"]
                        )
                    )

                except ValueError:

                    base = now

            else:

                base = now

            expires = (
                base +
                timedelta(days=days)
            )

            db.execute("""
                INSERT INTO subscriptions(
                    user_id,
                    expires_at
                )
                VALUES(?,?)
                ON CONFLICT(user_id)
                DO UPDATE SET
                    expires_at=excluded.expires_at
            """, (
                user_id,
                expires.isoformat()
            ))

    def remove_subscription(
        self,
        user_id
    ):

        with self.connect() as db:

            db.execute("""
                DELETE FROM subscriptions
                WHERE user_id=?
            """, (user_id,))

    def is_subscribed(
        self,
        user_id
    ):

        with self.connect() as db:

            row = db.execute("""
                SELECT expires_at
                FROM subscriptions
                WHERE user_id=?
            """, (user_id,)).fetchone()

        if not row:
            return False

        try:

            return (
                datetime.fromisoformat(
                    row["expires_at"]
                )
                >
                datetime.now(timezone.utc)
            )

        except ValueError:

            return False

    # =====================================================
    # FILES
    # =====================================================

    def add_file(
        self,
        channel_id,
        message_id,
        caption=""
    ):

        file_id = uuid.uuid4().hex[:12]

        with self.connect() as db:

            db.execute("""
                INSERT INTO files(
                    file_id,
                    channel_id,
                    message_id,
                    caption
                )
                VALUES(?,?,?,?)
            """, (
                file_id,
                channel_id,
                message_id,
                caption or ""
            ))

        return file_id

    def get_file(self, file_id):

        with self.connect() as db:

            return db.execute("""
                SELECT *
                FROM files
                WHERE file_id=?
            """, (file_id,)).fetchone()

    # =====================================================
    # BATCH
    # =====================================================

    def create_batch(self, file_ids):

        batch_id = uuid.uuid4().hex[:12]

        with self.connect() as db:

            db.execute("""
                INSERT INTO batches(
                    batch_id,
                    created_at
                )
                VALUES(?,?)
            """, (
                batch_id,
                datetime.now(timezone.utc).isoformat()
            ))

            for position, file_id in enumerate(file_ids):

                db.execute("""
                    INSERT INTO batch_items(
                        batch_id,
                        file_id,
                        position
                    )
                    VALUES(?,?,?)
                """, (
                    batch_id,
                    file_id,
                    position
                ))

        return batch_id

    def get_batch_items(self, batch_id):

        with self.connect() as db:

            return db.execute("""
                SELECT f.*
                FROM batch_items b
                JOIN files f
                    ON f.file_id=b.file_id
                WHERE b.batch_id=?
                ORDER BY b.position
            """, (batch_id,)).fetchall()

    # =====================================================
    # TOKENS
    # =====================================================

    def create_token(
        self,
        user_id,
        target,
        hours=2
    ):

        token = uuid.uuid4().hex

        expires = (
            datetime.now(timezone.utc)
            +
            timedelta(hours=hours)
        )

        with self.connect() as db:

            db.execute("""
                INSERT INTO tokens(
                    token,
                    user_id,
                    target,
                    expires_at,
                    used
                )
                VALUES(?,?,?,?,0)
            """, (
                token,
                user_id,
                target,
                expires.isoformat()
            ))

        return token

    def consume_token(self, token):

        with self.connect() as db:

            row = db.execute("""
                SELECT *
                FROM tokens
                WHERE token=?
            """, (token,)).fetchone()

            if not row:
                return None

            if row["used"]:
                return None

            try:

                expires = datetime.fromisoformat(
                    row["expires_at"]
                )

            except ValueError:

                return None

            if expires <= datetime.now(timezone.utc):

                return None

            db.execute("""
                UPDATE tokens
                SET used=1
                WHERE token=?
            """, (token,))

            return (
                row["user_id"],
                row["target"]
            )

    # =====================================================
    # BANNED USERS
    # =====================================================

    def ban_user(
        self,
        user_id,
        banned_by
    ):

        with self.connect() as db:

            db.execute("""
                INSERT OR REPLACE INTO banned_users(
                    user_id,
                    banned_by,
                    banned_at
                )
                VALUES(?,?,?)
            """, (
                user_id,
                banned_by,
                datetime.now(timezone.utc).isoformat()
            ))

    def unban_user(self, user_id):

        with self.connect() as db:

            db.execute("""
                DELETE FROM banned_users
                WHERE user_id=?
            """, (user_id,))

    def is_banned(self, user_id):

        with self.connect() as db:

            row = db.execute("""
                SELECT 1
                FROM banned_users
                WHERE user_id=?
            """, (user_id,)).fetchone()

            return row is not None

    def list_banned(self):

        with self.connect() as db:

            return db.execute("""
                SELECT *
                FROM banned_users
                ORDER BY banned_at DESC
            """).fetchall()

    # =====================================================
    # FORCE SUB
    # =====================================================

    def add_fsub(
        self,
        channel_id,
        invite_link=None,
        title=""
    ):

        with self.connect() as db:

            db.execute("""
                INSERT INTO fsub_channels(
                    channel_id,
                    invite_link,
                    title
                )
                VALUES(?,?,?)
                ON CONFLICT(channel_id)
                DO UPDATE SET
                    invite_link=excluded.invite_link,
                    title=excluded.title
            """, (
                str(channel_id),
                invite_link,
                title or ""
            ))

    def del_fsub(self, channel_id):

        with self.connect() as db:

            db.execute("""
                DELETE FROM fsub_channels
                WHERE channel_id=?
            """, (str(channel_id),))

    def list_fsub(self):

        with self.connect() as db:

            return db.execute("""
                SELECT *
                FROM fsub_channels
                ORDER BY rowid
            """).fetchall()

    # =====================================================
    # REQUESTS
    # =====================================================

    def add_request(
        self,
        user_id,
        request_text
    ):

        request_id = uuid.uuid4().hex[:10]

        with self.connect() as db:

            db.execute("""
                INSERT INTO requests(
                    request_id,
                    user_id,
                    request_text,
                    created_at
                )
                VALUES(?,?,?,?)
            """, (
                request_id,
                user_id,
                request_text,
                datetime.now(timezone.utc).isoformat()
            ))

        return request_id

    # =====================================================
    # SETTINGS
    # =====================================================

    def set_setting(
        self,
        key,
        value
    ):

        with self.connect() as db:

            db.execute("""
                INSERT INTO settings(
                    key,
                    value
                )
                VALUES(?,?)
                ON CONFLICT(key)
                DO UPDATE SET
                    value=excluded.value
            """, (
                key,
                value
            ))

    def get_setting(
        self,
        key,
        default=None
    ):

        with self.connect() as db:

            row = db.execute("""
                SELECT value
                FROM settings
                WHERE key=?
            """, (key,)).fetchone()

            if not row:
                return default

            return row["value"]

    # =====================================================
    # STATS
    # =====================================================

    def stats(self):

        now = datetime.now(
            timezone.utc
        ).isoformat()

        with self.connect() as db:

            return {

                "files": db.execute(
                    "SELECT COUNT(*) FROM files"
                ).fetchone()[0],

                "batches": db.execute(
                    "SELECT COUNT(*) FROM batches"
                ).fetchone()[0],

                "admins": db.execute(
                    "SELECT COUNT(*) FROM admins"
                ).fetchone()[0],

                "premium": db.execute(
                    "SELECT COUNT(*) FROM premium WHERE expires_at > ?",
                    (now,)
                ).fetchone()[0],

                "users": db.execute(
                    "SELECT COUNT(*) FROM users"
                ).fetchone()[0],

                "banned": db.execute(
                    "SELECT COUNT(*) FROM banned_users"
                ).fetchone()[0],
        }
