import os
import json
import sqlite3
from dotenv import load_dotenv
from urllib.request import Request, urlopen

load_dotenv()

DATABASE_URL = os.environ.get("TURSO_DATABASE_URL")
AUTH_TOKEN = os.environ.get("TURSO_AUTH_TOKEN")
LOCAL_DB_FILE = os.environ.get("LOCAL_DB_FILE", "camp_data.db")

class DatabaseManager:
    def __init__(self):
        self.is_turso = bool(DATABASE_URL and AUTH_TOKEN)
        if self.is_turso:
            url = DATABASE_URL.replace("libsql://", "https://")
            self.turso_url = f"{url}/v2/pipeline"
            self.headers = {
                "Authorization": f"Bearer {AUTH_TOKEN}",
                "Content-Type": "application/json"
            }
        self.init_db()

    def _execute_local(self, sql: str, params: tuple = ()):
        conn = sqlite3.connect(LOCAL_DB_FILE)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute(sql, params)
        conn.commit()
        if sql.strip().upper().startswith(("SELECT", "PRAGMA")):
            rows = [dict(r) for r in cursor.fetchall()]
        else:
            rows = []
        conn.close()
        return rows

    def _execute_turso(self, sql: str, params: tuple = ()):
        args = []
        for p in params:
            if isinstance(p, int):
                args.append({"type": "integer", "value": str(p)})
            elif isinstance(p, float):
                args.append({"type": "float", "value": p})
            elif p is None:
                args.append({"type": "null"})
            else:
                args.append({"type": "text", "value": str(p)})

        payload = {
            "requests": [
                {
                    "type": "execute",
                    "stmt": {"sql": sql, "args": args}
                },
                {"type": "close"}
            ]
        }

        request = Request(
            self.turso_url,
            data=json.dumps(payload).encode("utf-8"),
            headers=self.headers,
            method="POST",
        )

        with urlopen(request, timeout=10.0) as response:
            data = json.loads(response.read().decode("utf-8"))
            
            result = data["results"][0]["response"]["result"]
            cols = [c["name"] for c in result["cols"]]
            rows = []
            for r in result["rows"]:
                row_dict = {}
                for idx, col_name in enumerate(cols):
                    val_obj = r[idx]
                    val = val_obj.get("value")
                    if val_obj.get("type") == "integer" and val is not None:
                        val = int(val)
                    row_dict[col_name] = val
                rows.append(row_dict)
            return rows

    def execute(self, sql: str, params: tuple = ()):
        if self.is_turso:
            return self._execute_turso(sql, params)
        return self._execute_local(sql, params)

    def ensure_columns(self, table: str, columns: dict) -> None:
        """Add any of `columns` ({name: type_and_default_sql}) missing from `table`.

        Idempotent and additive only - existing columns/data are never touched.
        """
        existing = {row["name"] for row in self.execute(f"PRAGMA table_info({table})")}
        for name, declaration in columns.items():
            if name not in existing:
                self.execute(f"ALTER TABLE {table} ADD COLUMN {name} {declaration}")

    def init_db(self):
        # App state pointer for active day
        self.execute("""
            CREATE TABLE IF NOT EXISTS app_state (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)
        existing = self.execute("SELECT value FROM app_state WHERE key = 'current_day_index'")
        if not existing:
            self.execute("INSERT INTO app_state (key, value) VALUES ('current_day_index', '0')")

        # Teams & Points Table. Day columns (day_1, day_2, ...) are no longer
        # fixed here - they're added on demand by utils.ensure_day_columns()
        # based on however many days are configured, so the schema is not
        # capped at 6 camp days.
        self.execute("""
            CREATE TABLE IF NOT EXISTS teams (
                team TEXT PRIMARY KEY,
                leader TEXT,
                points INTEGER DEFAULT 0,
                emblem_url TEXT,
                primary_color TEXT,
                secondary_color TEXT
            )
        """)

        # Sync Logs Table
        self.execute("""
            CREATE TABLE IF NOT EXISTS logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT,
                team TEXT,
                change_val INTEGER,
                day_col TEXT,
                synced INTEGER DEFAULT 0
            )
        """)

db = DatabaseManager()