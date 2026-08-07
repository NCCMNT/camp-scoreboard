from fastapi.responses import FileResponse
from pathlib import Path
import logging
import os
import shutil
from datetime import datetime

import gspread
from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.staticfiles import StaticFiles

from database import db
from utils import (
    DAY_COLUMNS,
    LEADER_COLUMN,
    LOGS_WORKSHEET,
    POINTS_COLUMN,
    SCORE_WORKSHEET,
    SESSION_MAX_AGE_SECONDS,
    TEAM_COLUMN,
    bootstrap_if_empty,
    configured_day_columns,
    day_sheet_label,
    ensure_column,
    ensure_day_columns,
    get_current_day_index,
    get_sheet_client,
    set_current_day_index,
    verify_session,
)

app = FastAPI()
logger = logging.getLogger("uvicorn.error")

CAMP_PIN = os.environ.get("CAMP_PIN")
CRON_SECRET = os.environ.get("CRON_SECRET")

BASE_DIR = Path(__file__).resolve().parent
PUBLIC_DIR = BASE_DIR / "public"
UPLOAD_DIR = Path("/tmp/emblems") if os.environ.get("VERCEL") else PUBLIC_DIR / "emblems"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


@app.on_event("startup")
async def startup_event():
    try:
        bootstrap_if_empty()
    except Exception as exc:
        logger.error("Startup bootstrap skipped due to error: %s", exc)


@app.get("/api/health")
async def health_check():
    """Use this endpoint to verify your Vercel deployment status."""
    return {
        "status": "online",
        "has_pin": bool(CAMP_PIN),
        "has_sheet_name": bool(os.environ.get("SPREADSHEET_NAME")),
        "has_service_account": bool(os.environ.get("G_SERVICE_ACCOUNT_JSON")),
        "has_turso": bool(os.environ.get("TURSO_DATABASE_URL")),
    }


# --- ENDPOINTS ---


@app.post("/api/upload-emblem")
async def upload_emblem(team_key: str = Form(...), emblem: UploadFile = File(...)):
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    file_location = f"{UPLOAD_DIR}/{team_key}.png"
    
    with open(file_location, "wb") as buffer:
        shutil.copyfileobj(emblem.file, buffer)
    
    try:
        import colorgram
        colors = colorgram.extract(file_location, 2)
        hex_colors = ['#{:02x}{:02x}{:02x}'.format(c.rgb.r, c.rgb.g, c.rgb.b) for c in colors]
    except Exception:
        hex_colors = ["#FFFFFF", "#000000"]
    
    p_color = hex_colors[0] if len(hex_colors) > 0 else "#FFFFFF"
    s_color = hex_colors[1] if len(hex_colors) > 1 else "#000000"
    emblem_url = f"/emblems/{team_key}.png"

    db.execute(
        "UPDATE teams SET emblem_url = ?, primary_color = ?, secondary_color = ? WHERE team = ?",
        (emblem_url, p_color, s_color, team_key)
    )
    
    return {
        "status": "success",
        "emblem_url": emblem_url,
        "primary_color": p_color,
        "secondary_color": s_color
    }

@app.post("/api/login")
async def login(request: Request, response: Response):
    submitted_pin = ""
    content_type = request.headers.get("content-type", "")

    if "application/json" in content_type:
        try:
            data = await request.json()
            submitted_pin = str(data.get("pin", ""))
        except Exception:
            submitted_pin = ""
    else:
        form = await request.form()
        submitted_pin = str(form.get("pin", ""))

    expected_pin = str(os.environ.get("CAMP_PIN", "")).strip().strip("'\"")
    submitted_pin = submitted_pin.strip().strip("'\"")

    if not expected_pin or submitted_pin != expected_pin:
        raise HTTPException(status_code=401, detail="Incorrect PIN")

    response.set_cookie(
        key="counselor_session",
        value="authenticated_token",
        httponly=True,
        secure=True,
        max_age=SESSION_MAX_AGE_SECONDS,
        path="/",
        samesite="lax",
    )
    return {"status": "success", "message": "Authenticated successfully"}

@app.post("/api/logout")
async def logout(response: Response):
    response.delete_cookie("counselor_session", path="/")
    return {"status": "success"}

@app.get("/api/auth")
async def auth_status(request: Request):
    verify_session(request)
    return {"authenticated": True}

@app.get("/api/scores")
async def get_scores():
    try:
        teams = db.execute("SELECT * FROM teams")
    except Exception as e:
        logger.error("Error fetching teams: %s", e)
        teams = []

    results = []
    for t in teams:
        item = {
            TEAM_COLUMN: t["team"],
            LEADER_COLUMN: t.get("leader", ""),
            POINTS_COLUMN: t.get("points", 0),
            "emblem_url": t.get("emblem_url"),
            "primary_color": t.get("primary_color"),
            "secondary_color": t.get("secondary_color")
        }

        for d in DAY_COLUMNS:
            if d:
                item[d] = t.get(d, 0)
        results.append(item)

    return results

@app.get("/api/day-columns")
async def day_columns():
    """Return the configured day columns and the current active day."""
    configured_days = configured_day_columns()
    curr_idx = get_current_day_index()
    
    current_day_col = configured_days[curr_idx] if configured_days and curr_idx < len(configured_days) else None
    current_day_idx = curr_idx + 1 if current_day_col else None

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
    day: str = Form(None)
):
    if change == 0:
        raise HTTPException(status_code=400, detail="Change must be non-zero")

    configured_days = configured_day_columns()
    if not configured_days:
        raise HTTPException(status_code=500, detail="No camp days configured (set DAY_COLUMNS)")

    curr_idx = get_current_day_index()
    active_idx = min(curr_idx, len(configured_days) - 1)

    if day:
        if day not in configured_days:
            raise HTTPException(status_code=400, detail="Invalid day column")
        target_idx = configured_days.index(day)
        if target_idx > active_idx:
            raise HTTPException(status_code=400, detail="That day hasn't started yet")
        target_day_col = day
    else:
        target_day_col = configured_days[active_idx]
        target_idx = active_idx

    is_historical_day = target_idx < active_idx

    # Update team score directly in SQLite DB
    db.execute(
        f"UPDATE teams SET {target_day_col} = COALESCE({target_day_col}, 0) + ? WHERE team = ?",
        (change, team)
    )

    if is_historical_day and POINTS_COLUMN:
        db.execute(
            "UPDATE teams SET points = COALESCE(points, 0) + ? WHERE team = ?",
            (change, team)
        )

    # Log change locally
    db.execute(
        "INSERT INTO logs (timestamp, team, change_val, day_col, synced) VALUES (?, ?, ?, ?, 0)",
        (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), team, change, target_day_col)
    )

    updated_team = db.execute("SELECT * FROM teams WHERE team = ?", (team,))[0]

    return {
        "status": "success",
        "team": team,
        "day": target_day_col,
        "new_score": updated_team.get(target_day_col, 0),
        "new_summary": updated_team.get("points", 0),
    }

@app.post("/api/capture-snapshot", dependencies=[Depends(verify_session)])
async def capture_snapshot():
    """Capture the current active day and advance the day pointer."""
    configured_days = configured_day_columns()
    curr_idx = get_current_day_index()

    if curr_idx >= len(configured_days):
        raise HTTPException(status_code=400, detail="All configured camp days have already been captured")

    target_day_col = configured_days[curr_idx]

    db.execute(f"UPDATE teams SET points = COALESCE(points, 0) + COALESCE({target_day_col}, 0)")

    new_idx = curr_idx + 1
    set_current_day_index(new_idx)

    teams = db.execute("SELECT * FROM teams")
    captured = [
        {
            "team": t["team"],
            "day_points": t.get(target_day_col, 0),
            "running_total": t.get("points", 0)
        }
        for t in teams
    ]

    return {
        "status": "success",
        "day": curr_idx + 1,
        "next_day": new_idx + 1 if new_idx < len(configured_days) else None,
        "column": target_day_col,
        "captured": captured
    }

@app.get("/api/all-days", dependencies=[Depends(verify_session)])
async def get_all_days():
    try:
        configured_days = configured_day_columns()
        if not configured_days:
            return {"days": [], "active_day": None}

        idx = min(get_current_day_index(), len(configured_days) - 1)
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

def _perform_sheets_sync() -> dict:
    """Push local SQLite state to Google Sheets. Shared by the cron and manual endpoints."""
    client = get_sheet_client()

    # Sync scores & points to SCORE_WORKSHEET
    teams = db.execute("SELECT * FROM teams")
    if teams:
        score_sheet = client.worksheet(SCORE_WORKSHEET)
        header_row = score_sheet.row_values(1)

        ensure_column(score_sheet, header_row, TEAM_COLUMN)
        points_col_idx = ensure_column(score_sheet, header_row, POINTS_COLUMN) if POINTS_COLUMN else None

        # Day columns are written under their configured sheet label
        # (e.g. "Day 1"), not the internal day_1 DB column name.
        day_col_indices = {
            d: ensure_column(score_sheet, header_row, day_sheet_label(d)) for d in DAY_COLUMNS
        }

        records = score_sheet.get_all_records()
        existing_map = {str(rec.get(TEAM_COLUMN)): idx + 2 for idx, rec in enumerate(records)}

        updates = []
        for t in teams:
            team_name = t["team"]
            row_idx = existing_map.get(team_name)

            if not row_idx:
                score_sheet.append_row([team_name])
                row_idx = len(existing_map) + 2
                existing_map[team_name] = row_idx

            if points_col_idx:
                updates.append({
                    "range": gspread.utils.rowcol_to_a1(row_idx, points_col_idx),
                    "values": [[t.get("points", 0)]]
                })
            for d_col, col_idx in day_col_indices.items():
                updates.append({
                    "range": gspread.utils.rowcol_to_a1(row_idx, col_idx),
                    "values": [[t.get(d_col, 0)]]
                })

        if updates:
            score_sheet.batch_update(updates)

    # Append unsynced logs to LOGS_WORKSHEET
    unsynced_logs = db.execute("SELECT * FROM logs WHERE synced = 0 ORDER BY id ASC")
    if unsynced_logs:
        logs_sheet = client.worksheet(LOGS_WORKSHEET)
        rows = [[log["timestamp"], log["team"], log["change_val"], log["day_col"]] for log in unsynced_logs]
        logs_sheet.append_rows(rows)

        for log in unsynced_logs:
            db.execute("UPDATE logs SET synced = 1 WHERE id = ?", (log["id"],))

    return {
        "status": "success",
        "teams_synced": len(teams),
        "logs_synced": len(unsynced_logs),
        "timestamp": datetime.now().isoformat()
    }


@app.get("/api/sync-to-sheets")
async def sync_to_sheets(request: Request):
    """Sync local SQLite state to Google Sheets. Called by the cron job."""
    if CRON_SECRET:
        auth_header = request.headers.get("Authorization")
        if auth_header != f"Bearer {CRON_SECRET}":
            raise HTTPException(status_code=401, detail="Unauthorized cron request")

    try:
        return _perform_sheets_sync()
    except Exception as e:
        logger.error(f"Sync failed: {e}")
        raise HTTPException(status_code=500, detail=f"Sync failed: {str(e)}")


@app.post("/api/sync-to-sheets", dependencies=[Depends(verify_session)])
async def manual_sync_to_sheets():
    """Let a logged-in counselor trigger an on-demand sync from the admin panel."""
    try:
        return _perform_sheets_sync()
    except Exception as e:
        logger.error(f"Manual sync failed: {e}")
        raise HTTPException(status_code=500, detail=f"Sync failed: {str(e)}")

@app.get("/")
async def serve_index():
    index_path = PUBLIC_DIR / "index.html"
    if index_path.exists():
        return FileResponse(index_path)
    raise HTTPException(status_code=404, detail="index.html not found")


if PUBLIC_DIR.exists():
    app.mount("/", StaticFiles(directory=str(PUBLIC_DIR), html=True), name="static")

if os.path.exists("public/emblems"):
    app.mount("/emblems", StaticFiles(directory="public/emblems"), name="emblems")