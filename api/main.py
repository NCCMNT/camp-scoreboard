import os
import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from fastapi import FastAPI, Depends, HTTPException, Response, Request, Form
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
import gspread
import pandas as pd
from dotenv import load_dotenv

load_dotenv()

app = FastAPI()

# Configuration variables from environment variables (Vercel Dashboard Secrets)
SPREADSHEET_NAME = os.environ.get("SPREADSHEET_NAME")
SCORE_WORKSHEET = os.environ.get("SCORE_WORKSHEET")
LOGS_WORKSHEET = os.environ.get("LOGS_WORKSHEET", "Logs")
TEAM_COLUMN = os.environ.get("TEAM_COLUMN")
LEADER_COLUMN = os.environ.get("LEADER_COLUMN")
POINTS_COLUMN = os.environ.get("POINTS_COLUMN")

DAY_1 = os.environ.get("DAY_1")
DAY_2 = os.environ.get("DAY_2")
DAY_3 = os.environ.get("DAY_3")
DAY_4 = os.environ.get("DAY_4")
DAY_5 = os.environ.get("DAY_5")
DAY_6 = os.environ.get("DAY_6")
DAY_COLUMNS = [DAY_1, DAY_2, DAY_3, DAY_4, DAY_5, DAY_6]

# The calendar date (YYYY-MM-DD, in TIMEZONE below) on which Day 1 begins.
# Each camp "day" rolls over at 4:00 AM local time rather than midnight.
CAMP_START_DATE = os.environ.get("CAMP_START_DATE")
TIMEZONE = os.environ.get("TIMEZONE", "Europe/Warsaw")

# Optional shared secret so only your own cron job (or you, manually) can
# trigger a snapshot capture. Set this in Vercel and pass it as
# "Authorization: Bearer <secret>" (Vercel Cron does this automatically
# when CRON_SECRET is configured in the project).
CRON_SECRET = os.environ.get("CRON_SECRET")

CAMP_PIN = os.environ.get("CAMP_PIN")
SESSION_MAX_AGE_SECONDS = 60 * 60 * 24 * 30


def get_camp_day_number(now: datetime) -> int:
    """
    Returns which camp day we're currently in (1-6), where each day's
    window opens at 4:00 AM local time and stays open until the next
    day's snapshot is captured. Returns a number < 1 before Day 1 starts,
    and a number > 6 once Day 6 has passed.
    """
    if not CAMP_START_DATE:
        raise HTTPException(status_code=500, detail="CAMP_START_DATE is not configured")

    start_date = datetime.strptime(CAMP_START_DATE, "%Y-%m-%d").date()
    effective_date = now.date() if now.hour >= 4 else (now.date() - timedelta(days=1))
    return (effective_date - start_date).days + 1

# Initialize Google Sheets connection securely via service account
def get_sheet_client():
    secret_json_str = os.environ.get("G_SERVICE_ACCOUNT_JSON")
    if not secret_json_str:
        raise ValueError("Critical error: G_SERVICE_ACCOUNT_JSON is missing.")
        
    credentials_info = json.loads(secret_json_str)
    # Correct method to parse directly from dictionary object memory
    gc = gspread.service_account_from_dict(credentials_info)
    return gc.open(SPREADSHEET_NAME)

# --- AUTH MIDDLEWARE ---
def verify_session(request: Request):
    if request.cookies.get("counselor_session") != "authenticated_token":
        raise HTTPException(status_code=401, detail="Unauthorized access")

# --- ENDPOINTS ---

@app.post("/api/login")
async def login(response: Response, pin: str = Form(...)):
    if pin == CAMP_PIN:
        # Sets a secure cookie that keeps them logged in on mobile browsers
        response.set_cookie(
            key="counselor_session",
            value="authenticated_token",
            httponly=True,
            max_age=SESSION_MAX_AGE_SECONDS,
            expires=SESSION_MAX_AGE_SECONDS,
            path="/",
        )
        return {"status": "success", "message": "Authenticated successfully"}
    raise HTTPException(status_code=401, detail="Incorrect PIN")

@app.post("/api/logout")
async def logout(response: Response):
    response.delete_cookie("counselor_session", path="/")
    return {"status": "success"}

@app.get("/api/auth")
async def auth_status(request: Request):
    verify_session(request)
    return {"authenticated": True}

@app.get("/api/scores")
async def get_scores(client = Depends(get_sheet_client)):
    sheet = client.worksheet(SCORE_WORKSHEET)
    return sheet.get_all_records()

@app.post("/api/update-score", dependencies=[Depends(verify_session)])
async def update_score(team: str = Form(...), change: int = Form(...), client = Depends(get_sheet_client)):
    if change == 0:
        raise HTTPException(status_code=400, detail="Change must be non-zero")
        
    logs_sheet = client.worksheet(LOGS_WORKSHEET)
    logs_sheet.append_row([datetime.now().strftime("%Y-%m-%d %H:%M:%S"), team, change])
    
    score_sheet = client.worksheet(SCORE_WORKSHEET)
    records = score_sheet.get_all_records()
    
    row_idx = None
    for i, rec in enumerate(records, start=2):
        if str(rec.get(TEAM_COLUMN)) == team:
            row_idx = i
            raw_points = str(rec.get(POINTS_COLUMN, '')).strip()
            current_val = int(raw_points) if raw_points.isdigit() else 0
            break
            
    if not row_idx:
        raise HTTPException(status_code=404, detail="Team not found")
        
    col_idx = score_sheet.find(POINTS_COLUMN).col
    new_score = current_val + change
    score_sheet.update_cell(row_idx, col_idx, new_score)
    
    return {"status": "success", "team": team, "new_score": new_score}

@app.get("/api/day-columns")
async def day_columns():
    """Exposes the configured DAY_1..DAY_6 column names (in order) so the
    frontend can look up daily snapshot values without hardcoding headers."""
    return {
        "days": [c for c in DAY_COLUMNS if c],
        "summaryColumn": POINTS_COLUMN,
    }



@app.get("/api/capture-snapshot")
async def capture_snapshot(request: Request, client = Depends(get_sheet_client)):
    """
    Meant to be hit once a day (e.g. via a Vercel Cron job) shortly after
    4:00 AM. Figures out which camp day we're in, and if that day hasn't
    been captured yet, copies each team's current POINTS_COLUMN
    value into that day's column. Safe to call more than once - already
    captured cells are left untouched, so nothing gets overwritten.
    """
    if CRON_SECRET:
        auth_header = request.headers.get("authorization", "")
        provided = auth_header.replace("Bearer ", "").strip()
        if provided != CRON_SECRET:
            raise HTTPException(status_code=401, detail="Unauthorized")

    now = datetime.now(ZoneInfo(TIMEZONE))
    day_number = get_camp_day_number(now)

    if day_number < 1 or day_number > 6:
        return {"status": "skipped", "reason": f"No capture window active (computed day {day_number})"}

    target_column_name = DAY_COLUMNS[day_number - 1]
    if not target_column_name:
        return {"status": "skipped", "reason": f"DAY_{day_number} is not configured"}

    score_sheet = client.worksheet(SCORE_WORKSHEET)
    records = score_sheet.get_all_records()

    # Make sure the target day column exists in the sheet; add it if missing.
    header_row = score_sheet.row_values(1)
    if target_column_name not in header_row:
        score_sheet.update_cell(1, len(header_row) + 1, target_column_name)
        header_row.append(target_column_name)

    day_col_idx = header_row.index(target_column_name) + 1

    captured = []
    updates = []
    for i, rec in enumerate(records, start=2):
        existing_val = str(rec.get(target_column_name, '')).strip()
        if existing_val != '':
            continue  # Already captured for this day - don't overwrite.

        raw_summary = str(rec.get(POINTS_COLUMN, '')).strip()
        summary_val = int(raw_summary) if raw_summary.lstrip('-').isdigit() else 0

        updates.append({
            "range": gspread.utils.rowcol_to_a1(i, day_col_idx),
            "values": [[summary_val]],
        })
        captured.append({"team": rec.get(TEAM_COLUMN), "value": summary_val})

    if updates:
        score_sheet.batch_update(updates)

    return {
        "status": "success",
        "day": day_number,
        "column": target_column_name,
        "captured": captured,
    }


app.mount("/", StaticFiles(directory="public", html=True), name="static")