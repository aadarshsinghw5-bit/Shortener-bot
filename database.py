import os
import uuid
from datetime import datetime, timedelta, timezone

import psycopg2
from psycopg2.extras import RealDictCursor


class Database:

    def __init__(self, path=None):
        self.url = os.getenv("DATABASE_URL")

        if not self.url:
            raise RuntimeError("DATABASE_URL is not set")

        self.init()

    def connect(self):
        return psycopg2.connect(self.url)

    def init(self):

        with self.connect() as db:
            with db.cursor() as c:

                c.execute("""
                    CREATE TABLE IF NOT EXISTS users (
                        user_id BIGINT PRIMARY KEY,
                        username TEXT DEFAULT '',
                        first_name TEXT DEFAULT '',
                        created_at TIMESTAMPTZ DEFAULT NOW()
                    )
                """)

                c.execute("""
                    CREATE TABLE IF NOT EXISTS admins (
                        user_id BIGINT PRIMARY KEY
                    )
                """)

                c.execute("""
                    CREATE TABLE IF NOT EXISTS premium (
                        user_id BIGINT PRIMARY KEY,
                        expires_at TIMESTAMPTZ NOT NULL
                    )
                """)

                c.execute("""
                    CREATE TABLE IF NOT EXISTS banned_users (
                        user_id BIGINT PRIMARY KEY,
                        banned_by BIGINT,
                        banned_at TIMESTAMPTZ DEFAULT NOW()
                    )
                """)

                c.execute("""
                    CREATE TABLE IF NOT EXISTS files (
                        file_id TEXT PRIMARY KEY,
                        channel_id BIGINT NOT NULL,
                        message_id BIGINT NOT NULL,
                        caption TEXT DEFAULT ''
                    )
                """)

                c.execute("""
                    CREATE TABLE IF NOT EXISTS batches (
                        batch_id TEXT PRIMARY KEY,
                        created_at TIMESTAMPTZ DEFAULT NOW()
                    )
                """)

                c.execute("""
                    CREATE TABLE IF NOT EXISTS batch_items (
                        batch_id TEXT NOT NULL,
                        file_id TEXT NOT NULL,
                        position INTEGER NOT NULL,
                        PRIMARY KEY(batch_id, file_id)
                    )
                """)

                c.execute("""
                    CREATE TABLE IF NOT EXISTS tokens (
                        token TEXT PRIMARY KEY,
                        user_id BIGINT NOT NULL,
                        target TEXT NOT NULL,
                        expires_at TIMESTAMPTZ NOT NULL,
                        used BOOLEAN DEFAULT FALSE
                    )
                """)

                c.execute("""
                    CREATE TABLE IF NOT EXISTS fsub_channels (
                        channel_id TEXT PRIMARY KEY,
                        invite_link TEXT DEFAULT '',
                        title TEXT DEFAULT ''
                    )
                """)

                c.execute("""
                    CREATE TABLE IF NOT EXISTS requests (
                        request_id TEXT PRIMARY KEY,
                        user_id BIGINT NOT NULL,
                        request_text TEXT NOT NULL,
                        created_at TIMESTAMPTZ DEFAULT NOW()
                    )
                """)

                c.execute("""
                    CREATE TABLE IF NOT EXISTS settings (
                        key TEXT PRIMARY KEY,
                        value TEXT
                    )
                """)

            db.commit()

    # =====================================================
    # USERS
    # =====================================================

    def add_user(self, user_id, username="", first_name=""):

        with self.connect() as db:
            with db.cursor() as c:

                c.execute("""
                    INSERT INTO users(user_id, username, first_name)
                    VALUES(%s, %s, %s)
                    ON CONFLICT(user_id)
                    DO UPDATE SET
                        username = EXCLUDED.username,
                        first_name = EXCLUDED.first_name
                """, (
                    user_id,
                    username,
                    first_name
                ))

            db.commit()

    def list_users(self):

        with self.connect() as db:
            with db.cursor() as c:

                c.execute("""
                    SELECT user_id
                    FROM users
                    ORDER BY user_id
                """)

                return [r[0] for r in c.fetchall()]

    def user_count(self):

        with self.connect() as db:
            with db.cursor() as c:

                c.execute("SELECT COUNT(*) FROM users")

                return c.fetchone()[0]

    # =====================================================
    # ADMINS
    # =====================================================

    def add_admin(self, user_id):

        with self.connect() as db:
            with db.cursor() as c:
                c.execute(
                    "INSERT INTO admins(user_id) VALUES(%s) ON CONFLICT DO NOTHING",
                    (user_id,)
                )
            db.commit()

    def remove_admin(self, user_id):

        with self.connect() as db:
            with db.cursor() as c:
                c.execute(
                    "DELETE FROM admins WHERE user_id=%s",
                    (user_id,)
                )
            db.commit()

    def is_admin(self, user_id):

        with self.connect() as db:
            with db.cursor() as c:

                c.execute(
                    "SELECT 1 FROM admins WHERE user_id=%s",
                    (user_id,)
                )

                return c.fetchone() is not None

    def list_admins(self):

        with self.connect() as db:
            with db.cursor() as c:

                c.execute(
                    "SELECT user_id FROM admins ORDER BY user_id"
                )

                return [r[0] for r in c.fetchall()]

    # =====================================================
    # PREMIUM
    # =====================================================

    def add_premium(self, user_id, days):

        now = datetime.now(timezone.utc)

        with self.connect() as db:
            with db.cursor() as c:

                c.execute(
                    "SELECT expires_at FROM premium WHERE user_id=%s",
                    (user_id,)
                )

                row = c.fetchone()

                if row and row[0] > now:
                    base = row[0]
                else:
                    base = now

                expires = base + timedelta(days=days)

                c.execute("""
                    INSERT INTO premium(user_id, expires_at)
                    VALUES(%s, %s)
                    ON CONFLICT(user_id)
                    DO UPDATE SET expires_at=EXCLUDED.expires_at
                """, (
                    user_id,
                    expires
                ))

            db.commit()

    def remove_premium(self, user_id):

        with self.connect() as db:
            with db.cursor() as c:

                c.execute(
                    "DELETE FROM premium WHERE user_id=%s",
                    (user_id,)
                )

            db.commit()

    def get_premium(self, user_id):

        with self.connect() as db:
            with db.cursor(cursor_factory=RealDictCursor) as c:

                c.execute("""
                    SELECT user_id, expires_at
                    FROM premium
                    WHERE user_id=%s
                      AND expires_at > NOW()
                """, (user_id,))

                return c.fetchone()

    def is_premium(self, user_id):

        return self.get_premium(user_id) is not None

    def list_premium(self):

        with self.connect() as db:
            with db.cursor(cursor_factory=RealDictCursor) as c:

                c.execute("""
                    SELECT user_id, expires_at
                    FROM premium
                    WHERE expires_at > NOW()
                    ORDER BY expires_at
                """)

                return c.fetchall()

    # =====================================================
    # LEGACY SUBSCRIPTION SUPPORT
    # =====================================================

    def add_subscription(self, user_id, days):
        self.add_premium(user_id, days)

    def remove_subscription(self, user_id):
        self.remove_premium(user_id)

    def is_subscribed(self, user_id):
        return self.is_premium(user_id)

    # =====================================================
    # BAN SYSTEM
    # =====================================================

    def ban_user(self, user_id, banned_by):

        with self.connect() as db:
            with db.cursor() as c:

                c.execute("""
                    INSERT INTO banned_users(user_id, banned_by)
                    VALUES(%s, %s)
                    ON CONFLICT(user_id)
                    DO UPDATE SET
                        banned_by=EXCLUDED.banned_by,
                        banned_at=NOW()
                """, (
                    user_id,
                    banned_by
                ))

            db.commit()

    def unban_user(self, user_id):

        with self.connect() as db:
            with db.cursor() as c:

                c.execute(
                    "DELETE FROM banned_users WHERE user_id=%s",
                    (user_id,)
                )

            db.commit()

    def is_banned(self, user_id):

        with self.connect() as db:
            with db.cursor() as c:

                c.execute(
                    "SELECT 1 FROM banned_users WHERE user_id=%s",
                    (user_id,)
                )

                return c.fetchone() is not None

    def list_banned(self):

        with self.connect() as db:
            with db.cursor(cursor_factory=RealDictCursor) as c:

                c.execute("""
                    SELECT user_id, banned_by, banned_at
                    FROM banned_users
                    ORDER BY banned_at DESC
                """)

                return c.fetchall()

    # =====================================================
    # FILES
    # =====================================================

    def add_file(self, channel_id, message_id, caption=""):

        file_id = uuid.uuid4().hex[:12]

        with self.connect() as db:
            with db.cursor() as c:

                c.execute("""
                    INSERT INTO files(
                        file_id,
                        channel_id,
                        message_id,
                        caption
                    )
                    VALUES(%s, %s, %s, %s)
                """, (
                    file_id,
                    int(channel_id),
                    int(message_id),
                    caption or ""
                ))

            db.commit()

        return file_id

    def get_file(self, file_id):

        with self.connect() as db:
            with db.cursor(cursor_factory=RealDictCursor) as c:

                c.execute(
                    "SELECT * FROM files WHERE file_id=%s",
                    (file_id,)
                )

                return c.fetchone()

    # =====================================================
    # BATCH
    # =====================================================

    def create_batch(self, file_ids):

        batch_id = uuid.uuid4().hex[:12]

        with self.connect() as db:
            with db.cursor() as c:

                c.execute(
                    "INSERT INTO batches(batch_id) VALUES(%s)",
                    (batch_id,)
                )

                for position, file_id in enumerate(file_ids):

                    c.execute("""
                        INSERT INTO batch_items(
                            batch_id,
                            file_id,
                            position
                        )
                        VALUES(%s, %s, %s)
                    """, (
                        batch_id,
                        file_id,
                        position
                    ))

            db.commit()

        return batch_id

    def get_batch_items(self, batch_id):

        with self.connect() as db:
            with db.cursor(cursor_factory=RealDictCursor) as c:

                c.execute("""
                    SELECT f.*
                    FROM batch_items b
                    JOIN files f
                      ON f.file_id=b.file_id
                    WHERE b.batch_id=%s
                    ORDER BY b.position
                """, (batch_id,))

                return c.fetchall()

    # =====================================================
    # SHORTENER TOKENS
    # =====================================================

    def create_token(self, user_id, target, hours=2):

        token = uuid.uuid4().hex

        expires = (
            datetime.now(timezone.utc)
            + timedelta(hours=hours)
        )

        with self.connect() as db:
            with db.cursor() as c:

                c.execute("""
                    INSERT INTO tokens(
                        token,
                        user_id,
                        target,
                        expires_at,
                        used
                    )
                    VALUES(%s, %s, %s, %s, FALSE)
                """, (
                    token,
                    user_id,
                    target,
                    expires
                ))

            db.commit()

        return token

    def consume_token(self, token):

        with self.connect() as db:
            with db.cursor(cursor_factory=RealDictCursor) as c:

                c.execute("""
                    SELECT *
                    FROM tokens
                    WHERE token=%s
                      AND used=FALSE
                      AND expires_at > NOW()
                    FOR UPDATE
                """, (token,))

                row = c.fetchone()

                if not row:
                    return None

                c.execute("""
                    UPDATE tokens
                    SET used=TRUE
                    WHERE token=%s
                """, (token,))

            db.commit()

        return row["user_id"], row["target"]

    # =====================================================
    # FORCE SUB
    # =====================================================

    def add_fsub(self, channel_id, invite_link="", title=""):

        with self.connect() as db:
            with db.cursor() as c:

                c.execute("""
                    INSERT INTO fsub_channels(
                        channel_id,
                        invite_link,
                        title
                    )
                    VALUES(%s, %s, %s)
                    ON CONFLICT(channel_id)
                    DO UPDATE SET
                        invite_link=EXCLUDED.invite_link,
                        title=EXCLUDED.title
                """, (
                    str(channel_id),
                    invite_link or "",
                    title or ""
                ))

            db.commit()

    def del_fsub(self, channel_id):

        with self.connect() as db:
            with db.cursor() as c:

                c.execute(
                    "DELETE FROM fsub_channels WHERE channel_id=%s",
                    (str(channel_id),)
                )

            db.commit()

    def list_fsub(self):

        with self.connect() as db:
            with db.cursor(cursor_factory=RealDictCursor) as c:

                c.execute("""
                    SELECT channel_id, invite_link, title
                    FROM fsub_channels
                    ORDER BY title
                """)

                return c.fetchall()

    # =====================================================
    # REQUESTS
    # =====================================================

    def add_request(self, user_id, request_text):

        request_id = uuid.uuid4().hex[:8]

        with self.connect() as db:
            with db.cursor() as c:

                c.execute("""
                    INSERT INTO requests(
                        request_id,
                        user_id,
                        request_text
                    )
                    VALUES(%s, %s, %s)
                """, (
                    request_id,
                    user_id,
                    request_text
                ))

            db.commit()

        return request_id

    # =====================================================
    # SETTINGS
    # =====================================================

    def set_setting(self, key, value):

        with self.connect() as db:
            with db.cursor() as c:

                c.execute("""
                    INSERT INTO settings(key, value)
                    VALUES(%s, %s)
                    ON CONFLICT(key)
                    DO UPDATE SET value=EXCLUDED.value
                """, (
                    key,
                    value
                ))

            db.commit()

    def get_setting(self, key, default=None):

        with self.connect() as db:
            with db.cursor() as c:

                c.execute(
                    "SELECT value FROM settings WHERE key=%s",
                    (key,)
                )

                row = c.fetchone()

                return row[0] if row else default

    # =====================================================
    # STATS
    # =====================================================

    def stats(self):

        with self.connect() as db:
            with db.cursor() as c:

                c.execute("SELECT COUNT(*) FROM files")
                files = c.fetchone()[0]

                c.execute("SELECT COUNT(*) FROM batches")
                batches = c.fetchone()[0]

                c.execute("SELECT COUNT(*) FROM admins")
                admins = c.fetchone()[0]

                c.execute("""
                    SELECT COUNT(*)
                    FROM premium
                    WHERE expires_at > NOW()
                """)
                premium = c.fetchone()[0]

                c.execute("SELECT COUNT(*) FROM users")
                users = c.fetchone()[0]

                c.execute("SELECT COUNT(*) FROM banned_users")
                banned = c.fetchone()[0]

                return {
                    "files": files,
                    "batches": batches,
                    "admins": admins,
                    "premium": premium,
                    "users": users,
                    "banned": banned,
                    "subscriptions": premium,
        }
