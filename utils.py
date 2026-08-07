from database import db

import json
import logging
import os

import gspread
from dotenv import load_dotenv
from fastapi import HTTPException, Request

load_dotenv()


logger = logging.getLogger("uvicorn.error")

SPREADSHEET_NAME = os.environ.get("SPREADSHEET_NAME")
SCORE_WORKSHEET = os.environ.get("SCORE_WORKSHEET")
LOGS_WORKSHEET = os.environ.get("LOGS_WORKSHEET", "Logs")
TEAM_COLUMN = os.environ.get("TEAM_COLUMN")
LEADER_COLUMN = os.environ.get("LEADER_COLUMN")
POINTS_COLUMN = os.environ.get("POINTS_COLUMN")

# The camp can now run any number of days. DAY_COLUMNS is a single
# comma-separated env var holding the *Google Sheet* header text for each
# day, in order, e.g. "Day 1,Day 2,Day 3,Day 4,Day 5,Day 6,Day 7".
# The local DB always stores them positionally as day_1..day_N, so the
# sheet labels can be renamed/reordered-in-count without touching schema.
_raw_day_labels = os.environ.get("DAY_COLUMNS", "")
DAY_LABELS = [label.strip() for label in _raw_day_labels.split(",") if label.strip()]
DAY_COLUMNS = [f"day_{i + 1}" for i in range(len(DAY_LABELS))]
DAY_SHEET_LABELS = dict(zip(DAY_COLUMNS, DAY_LABELS))

SESSION_MAX_AGE_SECONDS = 60 * 60 * 24 * 30


def configured_day_columns() -> list[str]:
    """Return the local DB day columns (day_1..day_N) for however many days are configured."""
    return DAY_COLUMNS


def day_sheet_label(day_column: str) -> str:
    """Return the Google Sheet header text for a local day column."""
    return DAY_SHEET_LABELS.get(day_column, day_column)


def ensure_day_columns() -> None:
    """Make sure the local teams table has a column for every configured day.

    Safe to call on every startup: new days simply get ALTER TABLE'd in,
    nothing is ever removed (shrinking DAY_COLUMNS just stops using the
    extra columns, it doesn't delete historical data).
    """
    db.ensure_columns("teams", {col: "INTEGER DEFAULT 0" for col in DAY_COLUMNS})


def ensure_column(sheet, header_row: list, name: str) -> int:
    """Add a missing header column and return its 1-based index."""
    if name not in header_row:
        sheet.update_cell(1, len(header_row) + 1, name)
        header_row.append(name)
    return header_row.index(name) + 1


def parse_int_cell(raw) -> int:
    """Parse a sheet cell into an integer, falling back to 0."""
    text = str(raw).strip()
    if not text:
        return 0
    try:
        return int(text)
    except ValueError:
        return 0


def get_current_day_index() -> int:
    """Read the active day pointer from the database."""
    result = db.execute("SELECT value FROM app_state WHERE key = 'current_day_index'")
    return int(result[0]["value"]) if result else 0


def set_current_day_index(index: int) -> None:
    """Persist the active day pointer in the database."""
    db.execute("UPDATE app_state SET value = ? WHERE key = ?", (str(index), "current_day_index"))


def get_sheet_client() -> gspread.Spreadsheet:
    """Open the configured Google Sheets workbook safely."""
    secret_json_str = os.environ.get("G_SERVICE_ACCOUNT_JSON", "").strip()
    if not secret_json_str:
        raise ValueError("Critical error: G_SERVICE_ACCOUNT_JSON is missing.")

    try:
        credentials_info = json.loads(secret_json_str)
    except Exception as err:
        raise ValueError(f"Critical error: G_SERVICE_ACCOUNT_JSON is invalid JSON. Details: {err}")

    if not SPREADSHEET_NAME:
        raise ValueError("Critical error: SPREADSHEET_NAME environment variable is missing.")

    client = gspread.service_account_from_dict(credentials_info)
    return client.open(SPREADSHEET_NAME)


def verify_session(request: Request) -> None:
    """Reject requests without the counselor session cookie."""
    if request.cookies.get("counselor_session") != "authenticated_token":
        raise HTTPException(status_code=401, detail="Unauthorized access")


def bootstrap_if_empty() -> None:
    """Seed teams from Google Sheets when the local DB is empty."""
    teams = db.execute("SELECT * FROM teams")
    if teams or not SPREADSHEET_NAME or not os.environ.get("G_SERVICE_ACCOUNT_JSON"):
        return

    try:
        client = get_sheet_client()
        sheet = client.worksheet(SCORE_WORKSHEET)
        records = sheet.get_all_records()

        columns = ["team", "leader", "points"] + DAY_COLUMNS
        placeholders = ", ".join(["?"] * len(columns))
        column_list = ", ".join(columns)

        for record in records:
            team_name = str(record.get(TEAM_COLUMN, "")).strip()
            if not team_name:
                continue

            values = [
                team_name,
                str(record.get(LEADER_COLUMN, "")),
                parse_int_cell(record.get(POINTS_COLUMN, 0)),
            ]
            values += [parse_int_cell(record.get(day_sheet_label(col), 0)) for col in DAY_COLUMNS]

            db.execute(
                f"INSERT INTO teams ({column_list}) VALUES ({placeholders})",
                tuple(values),
            )
    except Exception as exc:
        logger.error("Failed to bootstrap DB from Sheets: %s", exc)