import os
import json
from datetime import datetime
from fastapi import FastAPI, Depends, HTTPException, Response, Request, Form
from fastapi.staticfiles import StaticFiles
import gspread
from dotenv import load_dotenv

load_dotenv()

app = FastAPI()

# Configuration variables from env
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

CAMP_PIN = os.environ.get("CAMP_PIN")
SESSION_MAX_AGE_SECONDS = 60 * 60 * 24 * 30

# Initialize Google Sheets connection securely via service account
def get_sheet_client() -> gspread.Spreadsheet:
    secret_json_str = os.environ.get("G_SERVICE_ACCOUNT_JSON")
    if not secret_json_str:
        raise ValueError("Critical error: G_SERVICE_ACCOUNT_JSON is missing.")
        
    credentials_info = json.loads(secret_json_str)

    # Correct method to parse directly from dictionary object memory
    gc = gspread.service_account_from_dict(credentials_info)
    return gc.open(SPREADSHEET_NAME)

def verify_session(request: Request) -> None:
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

@app.get("/api/day-columns")
async def day_columns(client = Depends(get_sheet_client)):
    """
    Exposes all days, the summary column, the currently active day column, 
    and its human-readable index dynamically.
    """
    configured_days = [c for c in DAY_COLUMNS if c]
    score_sheet = client.worksheet(SCORE_WORKSHEET)
    header_row = score_sheet.row_values(1)
    
    # Find the first day column that has an empty cell or isn't fully locked
    records = score_sheet.get_all_records()
    current_day_col = None
    current_day_idx = None
    
    for idx, col in enumerate(configured_days, start=1):
        # The day is active if any team has a blank/empty value in it
        if col not in header_row or any(str(rec.get(col, '')).strip() == '' for rec in records):
            current_day_col = col
            current_day_idx = idx
            break

    return {
        "days": configured_days,
        "summaryColumn": POINTS_COLUMN,
        "currentDayColumn": current_day_col,
        "currentDayIndex": current_day_idx
    }

@app.post("/api/update-score", dependencies=[Depends(verify_session)])
async def update_score(team: str = Form(...), change: int = Form(...), client = Depends(get_sheet_client)):
    if change == 0:
        raise HTTPException(status_code=400, detail="Change must be non-zero")
        
    logs_sheet = client.worksheet(LOGS_WORKSHEET)
    logs_sheet.append_row([datetime.now().strftime("%Y-%m-%d %H:%M:%S"), team, change])
    
    score_sheet = client.worksheet(SCORE_WORKSHEET)
    records = score_sheet.get_all_records()
    header_row = score_sheet.row_values(1)
    
    # Dynamically find the running day column
    configured_days = [c for c in DAY_COLUMNS if c]
    current_day_col = None
    for col in configured_days:
        if col not in header_row or any(str(rec.get(col, '')).strip() == '' for rec in records):
            current_day_col = col
            break
            
    if not current_day_col:
        raise HTTPException(status_code=400, detail="No active camp day is currently open.")
    
    row_idx = None
    for i, rec in enumerate(records, start=2):
        if str(rec.get(TEAM_COLUMN)) == team:
            row_idx = i
            raw_points = str(rec.get(current_day_col, '')).strip()
            current_val = int(raw_points) if raw_points.isdigit() else 0
            break
            
    if not row_idx:
        raise HTTPException(status_code=404, detail="Team not found")
        
    # Ensure the column exists in the sheet headers safely
    if current_day_col not in header_row:
        score_sheet.update_cell(1, len(header_row) + 1, current_day_col)
        header_row.append(current_day_col)
    
    col_idx = header_row.index(current_day_col) + 1
    new_score = current_val + change
    score_sheet.update_cell(row_idx, col_idx, new_score)
    
    return {"status": "success", "team": team, "new_score": new_score}

@app.post("/api/capture-snapshot", dependencies=[Depends(verify_session)])
async def capture_snapshot(client = Depends(get_sheet_client)):
    """
    Triggered by the counselor-facing snapshot button. Finds the next
    uncaptured camp day (the first DAY_N column that's still empty for
    every team), and locks in the current overall points into that day's column.
    """

    configured_days = [c for c in DAY_COLUMNS if c]
    if not configured_days:
        raise HTTPException(status_code=500, detail="No DAY_1..DAY_6 columns are configured")
    if not POINTS_COLUMN:
        raise HTTPException(status_code=500, detail="POINTS_COLUMN is not configured")

    score_sheet = client.worksheet(SCORE_WORKSHEET)
    records = score_sheet.get_all_records()
    header_row = score_sheet.row_values(1)

    def ensure_column(name):
        nonlocal header_row
        if name not in header_row:
            score_sheet.update_cell(1, len(header_row) + 1, name)
            header_row.append(name)
        return header_row.index(name) + 1

    # Find the first day column that's still empty for any team.
    target_day_col = None
    for col in configured_days:
        if col not in header_row or any(str(rec.get(col, '')).strip() == '' for rec in records):
            target_day_col = col
            break

    if not target_day_col:
        raise HTTPException(status_code=400, detail="All configured camp days have already been captured")

    day_number = configured_days.index(target_day_col) + 1
    day_col_idx = ensure_column(target_day_col)

    updates = []
    captured = []
    
    for i, rec in enumerate(records, start=2):

        # Read the running overall summary score safely
        raw_summary = str(rec.get(POINTS_COLUMN, '')).strip()
        prev_summary = int(raw_summary) if raw_summary.isdigit() else 0

        # Calculate this day's points performance
        raw_day_pts = str(rec.get(target_day_col, '')).strip()
        day_points = int(raw_day_pts) if raw_day_pts.isdigit() else 0

        new_summary = prev_summary + day_points

        # Save today's standalone performance into the historic Day column
        updates.append({
            "range": gspread.utils.rowcol_to_a1(i, day_col_idx),
            "values": [[day_points]]
        })

        captured.append({
            "team": rec.get(TEAM_COLUMN, "Unknown"),
            "day_points": day_points,
            "running_total": new_summary,
        })

    if updates:
        score_sheet.batch_update(updates)

    return {
        "status": "success",
        "day": day_number,
        "next_day": day_number + 1 if day_number <= len(DAY_COLUMNS) else None,
        "column": target_day_col,
        "captured": captured
    }

app.mount("/", StaticFiles(directory="public", html=True), name="static")