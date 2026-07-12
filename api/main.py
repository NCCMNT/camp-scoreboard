import os
import json
from datetime import datetime
from fastapi import FastAPI, Depends, HTTPException, Response, Request, Form
from fastapi.staticfiles import StaticFiles
import gspread
from dotenv import load_dotenv

load_dotenv()

app = FastAPI()

# Configuration variables from environment variables
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
def get_sheet_client():
    secret_json_str = os.environ.get("G_SERVICE_ACCOUNT_JSON")
    if not secret_json_str:
        raise ValueError("Critical error: G_SERVICE_ACCOUNT_JSON is missing.")
        
    credentials_info = json.loads(secret_json_str)
    # Correct method to parse directly from dictionary object memory
    gc = gspread.service_account_from_dict(credentials_info)
    return gc.open(SPREADSHEET_NAME)

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

app.mount("/", StaticFiles(directory="public", html=True), name="static")