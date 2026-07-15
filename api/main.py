import os
import json
from datetime import datetime
from fastapi import FastAPI, Depends, HTTPException, Response, Request, Form, UploadFile, File
import logging
import shutil
from fastapi.staticfiles import StaticFiles
import gspread
from dotenv import load_dotenv

load_dotenv()

app = FastAPI()
logger = logging.getLogger("uvicorn.error")

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

SNAPSHOT_REGISTRY = {f"day_{i}": False for i in range(len(DAY_COLUMNS))}

# The single source of truth for "which day is active right now".
# Only capture_snapshot() ever advances this. Editing a past day never
# touches it, so the active day can't accidentally shift backward.
CURRENT_DAY_INDEX = 0

def ensure_column(sheet, header_row: list, name: str) -> int:
    """Make sure `name` exists as a header column, returning its 1-based index."""
    if name not in header_row:
        sheet.update_cell(1, len(header_row) + 1, name)
        header_row.append(name)
    return header_row.index(name) + 1

def parse_int_cell(raw) -> int:
    """
    Safely parse a sheet cell as an int, including negative values.
    str.isdigit() returns False for "-5" (the '-' isn't a digit), which was
    silently resetting negative scores back to 0 on every subsequent update.
    """
    s = str(raw).strip()
    if not s:
        return 0
    try:
        return int(s)
    except ValueError:
        return 0

CAMP_PIN = os.environ.get("CAMP_PIN")
SESSION_MAX_AGE_SECONDS = 60 * 60 * 24 * 30

UPLOAD_DIR = "public/emblems"
os.makedirs(UPLOAD_DIR, exist_ok=True)

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

@app.post("/api/upload-emblem")
async def upload_emblem(team_key: str = Form(...), emblem: UploadFile = File(...)):
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    
    file_location = f"{UPLOAD_DIR}/{team_key}.png"
    
    try:
        with open(file_location, "wb") as buffer:
            shutil.copyfileobj(emblem.file, buffer)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save file: {str(e)}")
    
    try:
        import colorgram
        colors = colorgram.extract(file_location, 2)
        hex_colors = []
        for color in colors:
            rgb = color.rgb
            hex_code = '#{:02x}{:02x}{:02x}'.format(rgb.r, rgb.g, rgb.b)
            hex_colors.append(hex_code)
    except Exception:
        hex_colors = ["#FFFFFF", "#000000"]
    
    primary_color = hex_colors[0] if len(hex_colors) > 0 else "#FFFFFF"
    secondary_color = hex_colors[1] if len(hex_colors) > 1 else "#000000"
    
    # TODO: Update google sheet/DB with these colors
    
    return {
        "status": "success",
        "emblem_url": f"/uploads/{team_key}.png",
        "primary_color": primary_color,
        "secondary_color": secondary_color
    }

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
    and its human-readable index. The active day is always
    configured_days[CURRENT_DAY_INDEX] — it never shifts just because a
    counselor is editing a past day.
    """
    configured_days = [c for c in DAY_COLUMNS if c]

    current_day_col = None
    current_day_idx = None
    if configured_days and CURRENT_DAY_INDEX < len(configured_days):
        current_day_col = configured_days[CURRENT_DAY_INDEX]
        current_day_idx = CURRENT_DAY_INDEX + 1

    return {
        "days": configured_days,
        "summaryColumn": POINTS_COLUMN,
        "currentDayColumn": current_day_col,
        "currentDayIndex": current_day_idx
    }

@app.post("/api/update-score", dependencies=[Depends(verify_session)])
async def update_score(
    team: str = Form(...),
    change: int = Form(...),
    day: str = Form(None),
    client = Depends(get_sheet_client),
):
    if change == 0:
        raise HTTPException(status_code=400, detail="Change must be non-zero")
    
    try:
        change = int(change)
    except ValueError:
        raise HTTPException(status_code=400, detail="Change must be a whole number")

    configured_days = [c for c in DAY_COLUMNS if c]
    if not configured_days:
        raise HTTPException(status_code=500, detail="No DAY_1..DAY_6 columns are configured")
    if CURRENT_DAY_INDEX >= len(configured_days):
        # All days captured — still allow editing any past day explicitly.
        current_idx = len(configured_days) - 1
    else:
        current_idx = CURRENT_DAY_INDEX

    if day:
        if day not in configured_days:
            raise HTTPException(status_code=400, detail="Invalid day column")
        target_idx = configured_days.index(day)
        if target_idx > current_idx:
            raise HTTPException(status_code=400, detail="That day hasn't started yet")
        target_day_col = day
    else:
        target_day_col = configured_days[current_idx]
        target_idx = current_idx

    # Editing a day strictly before the active one means it was already
    # captured into the running total, so the total needs the same delta
    # applied to stay in sync. The active day itself only updates its own
    # column — its contribution to the total is added later at capture time.
    is_historical_day = target_idx < current_idx

    logs_sheet = client.worksheet(LOGS_WORKSHEET)
    logs_sheet.append_row([datetime.now().strftime("%Y-%m-%d %H:%M:%S"), team, change, target_day_col])

    score_sheet = client.worksheet(SCORE_WORKSHEET)
    records = score_sheet.get_all_records()
    header_row = score_sheet.row_values(1)

    row_idx = None
    current_val = 0
    current_summary = 0
    for i, rec in enumerate(records, start=2):
        if str(rec.get(TEAM_COLUMN)) == team:
            row_idx = i
            raw_points = str(rec.get(target_day_col, '')).strip()
            current_val = parse_int_cell(raw_points)
            raw_summary = str(rec.get(POINTS_COLUMN, '')).strip()
            current_summary = parse_int_cell(raw_summary)
            break

    if not row_idx:
        raise HTTPException(status_code=404, detail="Team not found")

    day_col_idx = ensure_column(score_sheet, header_row, target_day_col)
    new_score = current_val + change
    score_sheet.update_cell(row_idx, day_col_idx, new_score)

    new_summary = None
    if is_historical_day and POINTS_COLUMN:
        points_col_idx = ensure_column(score_sheet, header_row, POINTS_COLUMN)
        new_summary = current_summary + change
        score_sheet.update_cell(row_idx, points_col_idx, new_summary)

    return {
        "status": "success",
        "team": team,
        "day": target_day_col,
        "new_score": new_score,
        "new_summary": new_summary,
    }

@app.post("/api/capture-snapshot", dependencies=[Depends(verify_session)])
async def capture_snapshot(client = Depends(get_sheet_client)):
    """
    Triggered by the counselor-facing snapshot button. Always captures
    exactly configured_days[CURRENT_DAY_INDEX] — the active day — folding
    its points into the running total, then explicitly advances the pointer
    to the next day. This is the only place CURRENT_DAY_INDEX ever changes.
    """
    global CURRENT_DAY_INDEX

    configured_days = [c for c in DAY_COLUMNS if c]
    if not configured_days:
        raise HTTPException(status_code=500, detail="No DAY_1..DAY_6 columns are configured")
    if not POINTS_COLUMN:
        raise HTTPException(status_code=500, detail="POINTS_COLUMN is not configured")
    if CURRENT_DAY_INDEX >= len(configured_days):
        raise HTTPException(status_code=400, detail="All configured camp days have already been captured")

    target_day_col = configured_days[CURRENT_DAY_INDEX]
    day_number = CURRENT_DAY_INDEX + 1

    score_sheet = client.worksheet(SCORE_WORKSHEET)
    records = score_sheet.get_all_records()
    header_row = score_sheet.row_values(1)

    day_col_idx = ensure_column(score_sheet, header_row, target_day_col)
    points_col_idx = ensure_column(score_sheet, header_row, POINTS_COLUMN)

    updates = []
    captured = []

    for i, rec in enumerate(records, start=2):
        raw_summary = str(rec.get(POINTS_COLUMN, '')).strip()
        prev_summary = parse_int_cell(raw_summary)

        raw_day_pts = str(rec.get(target_day_col, '')).strip()
        day_points = parse_int_cell(raw_day_pts)

        new_summary = prev_summary + day_points

        updates.append({
            "range": gspread.utils.rowcol_to_a1(i, points_col_idx),
            "values": [[new_summary]]
        })

        captured.append({
            "team": rec.get(TEAM_COLUMN, "Unknown"),
            "day_points": day_points,
            "running_total": new_summary,
        })

    if updates:
        score_sheet.batch_update(updates)

    SNAPSHOT_REGISTRY[target_day_col] = True
    CURRENT_DAY_INDEX += 1

    return {
        "status": "success",
        "day": day_number,
        "next_day": day_number + 1 if CURRENT_DAY_INDEX < len(configured_days) else None,
        "column": target_day_col,
        "captured": captured
    }

@app.get("/api/all-days", dependencies=[Depends(verify_session)])
async def get_all_days():
    """
    Exposes every day up to and including the current active day, so the
    admin panel can offer a "view/edit a past day" picker. Selecting a past
    day here never changes CURRENT_DAY_INDEX — editing goes straight through
    /api/update-score with an explicit `day` param instead.
    """
    try:
        configured_days = [c for c in DAY_COLUMNS if c]
        if not configured_days:
            return {"days": [], "active_day": None}

        idx = min(CURRENT_DAY_INDEX, len(configured_days) - 1)
        current_active_day = configured_days[idx]
        selectable_days = configured_days[:idx + 1]

        days_payload = [
            {"column": col, "label": col.capitalize().replace('_', ' ')}
            for col in selectable_days
        ]

        return {
            "days": days_payload,
            "active_day": current_active_day
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

app.mount("/emblems", StaticFiles(directory="public/emblems"), name="emblems")
app.mount("/", StaticFiles(directory="public", html=True), name="static")