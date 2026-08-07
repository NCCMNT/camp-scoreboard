# Summer camp leaderboard

A simple leaderboard REST API for tracking and displaying team scores during a summer camp. The app provides:

- a FastAPI backend (`main.py`)
- a lightweight static frontend (`public/`) for scoreboard and admin pages
- optional synchronization with Google Sheets via `gspread`

---

## Prerequisites & Configuration

1) Create a `.env` file in the project root (the project uses `python-dotenv`). Example:

```ini
SPREADSHEET_NAME="Camp Arena Scores"
SCORE_WORKSHEET="Sheet1"

TEAM_COLUMN="Team Name"
LEADER_COLUMN="Leader"
POINTS_COLUMN="Overall Points"

DAY_1="Day 1"
DAY_2="Day 2"
DAY_3="Day 3"
DAY_4="Day 4"
DAY_5="Day 5"
DAY_6="Day 6"

# Optional secrets
CAMP_PIN=1234
CRON_SECRET="your-cron-secret"
```

2) Google Sheets access: create a Google Cloud service account with Sheets API access and either:

- set `GOOGLE_APPLICATION_CREDENTIALS=/path/to/service-account.json` in your environment, or
- place the JSON file in the project and set the path in your shell before running the app.

The project uses `gspread` for authenticated read/write to the spreadsheet named in `SPREADSHEET_NAME`.

---

## Local development

Recommended steps to set up a virtual environment and install dependencies:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

You can also install via `pyproject.toml` tooling (Poetry/PDm) if you prefer.

---

## Run the app

Start the FastAPI app (entrypoint is `main:app`):

```bash
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

Visit the public scoreboard at `http://<HOST_IP>:8000/` and the admin panel at `http://<HOST_IP>:8000/admin.html`.

If you only need local access, bind to `127.0.0.1` instead of `0.0.0.0`.

---

## How it works

- The FastAPI endpoints in `main.py` provide JSON APIs for scores, day columns, and admin actions.
- Local state is persisted in an embedded SQLite DB (see `database.py`).
- A sync routine pushes local state to Google Sheets using `gspread` (see `_perform_sheets_sync()` in `main.py`).