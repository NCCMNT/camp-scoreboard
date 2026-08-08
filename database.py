import os
import json
import sqlite3
import logging
from dotenv import load_dotenv
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

load_dotenv()

logger = logging.getLogger(__name__)

DATABASE_URL = os.environ.get("TURSO_DATABASE_URL", "").strip().strip("'\"")
AUTH_TOKEN = os.environ.get("TURSO_AUTH_TOKEN", "").strip().strip("'\"")

default_local_db = "/tmp/camp_data.db" if os.environ.get("VERCEL") else "camp_data.db"
LOCAL_DB_FILE = os.environ.get("LOCAL_DB_FILE", default_local_db).strip().strip("'\"")


class DatabaseManager:
    def __init__(self):
        self.is_turso = bool(DATABASE_URL and AUTH_TOKEN)
        if self.is_turso:
            url = DATABASE_URL.replace("libsql://", "https://")
            if not url.startswith("http"):
                url = f"https://{url}"
            self.turso_url = f"{url.rstrip('/')}/v2/pipeline"
            self.headers = {
                "Authorization": f"Bearer {AUTH_TOKEN}",
                "Content-Type": "application/json"
            }
        
        # Safely attempt database schema initialization
        try:
            self.init_db()
        except Exception as e:
            logger.error("Failed to initialize database schema on startup: %s", str(e))

    def _execute_local(self, sql: str, params: tuple = ()):
        # Ensure directory exists if path contains directories
        db_dir = os.path.dirname(LOCAL_DB_FILE)
        if db_dir:
            os.makedirs(db_dir, exist_ok=True)

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

        try:
            with urlopen(request, timeout=10.0) as response:
                data = json.loads(response.read().decode("utf-8"))
        except HTTPError as e:
            err_body = e.read().decode("utf-8", errors="ignore")
            raise Exception(f"Turso HTTP Error {e.code}: {err_body}") from e
        except URLError as e:
            raise Exception(f"Turso Connection Error: {e.reason}") from e

        results = data.get("results", [])
        if not results:
            return []

        first_res = results[0]
        if first_res.get("type") == "error":
            err_msg = first_res.get("error", {}).get("message", "Unknown Turso Error")
            raise Exception(f"Turso Execution Error: {err_msg}")

        res_body = first_res.get("response", {}).get("result", {})
        cols = [c["name"] for c in res_body.get("cols", [])]
        rows = []
        for r in res_body.get("rows", []):
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

        # Teams & Points Table
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
        self.ensure_columns("logs", {"counselor": "TEXT"})


db = DatabaseManager()