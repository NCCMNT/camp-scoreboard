# Summer camp leaderboard

A simple leaderboard REST API application for tracking and displaying scores for teams on competetive games. Designed for automatic synchronization between counselors view for changing points and main leaderboard for displaying scores.

App utilizes **FastAPI** backend paired with an asynchronous frontend polling engine and persisting data remotely via **Google Sheets API**. The system is built with lightweight, vanilla web environments styled around custom CSS variable palettes.

---

## Prerequisites & Configuration

### 1. Environment Variables (`.env`)
Create a `.env` configuration file in the root directory to map your exact spreadsheet structure and dynamic matrix column arrays:

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

```

### 2. Google Cloud Service Account

Place your service account JSON file in your project path or export the credentials globally to grant `gspread` authenticated read/write scopes on the specified target sheet workbook.

---

## Local Development Workflow

This project leverages **`uv`** Python package installer and resolver.

### 1. Project Initialization & Virtual Env

Ensure `uv` is installed on your machine. Then, spawn the virtual environment sandbox:

```bash
# Sync and instantiate the .venv directory automatically
uv venv

# Activate the isolated shell environment
source .venv/bin/activate

```

### 2. Dependency Resolution

Install the package matrix defined inside your requirements configuration:

```bash
uv pip install -r requirements.txt
```
or utilizing `uv.lock` file:

```
uv sync
```

---

## Deploying on the LAN

By default, launching Uvicorn binds explicitly to `127.0.0.1` (localhost), restricting API traffic solely to your host machine. To open requests to external target units like cellular devices, tablets, or remote client screens on the same local network, you must bind to `0.0.0.0`.

### 1. Run the Uvicorn Service Broadcast

Execute the following command inside your active runtime terminal:

```bash
uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload

```

### 2. Identify Host LAN IP Address

Locate your machine's unique interface address assigned by your routing gateway:

* **Linux / macOS:** Run `hostname -I` or `ifconfig | grep "inet "`
* **Windows (WSL/CMD):** Run `ipconfig`

Look for your standard internal IPv4 address layout (typically resembling `192.168.X.X` or `10.0.X.X`).

### 3. Connect from External Devices

Ensure your phone or target device is connected to the **exact same Wi-Fi network**. Open your target web browser and navigate directly to the host machine's IP address assignment:

* **Public Scoreboard View:** `http://<YOUR_LAN_IP>:8000/`
* **Counselor/Admin Dashboard Panel:** `http://<YOUR_LAN_IP>:8000/admin.html`

---

## API Performance Specs

* **Data Aggregation:** The client application uses `fetch()` API operations mapping into JSON array structures.


* **Polling Frequency:** Front-facing client states are decoupled from heavy event loops via low-overhead background `setInterval` polling loops firing strictly once every 10 seconds silently.


* **Transaction Batching:** Bulk record updates utilize the `gspread.worksheet.batch_update()` endpoint execution schema to group payloads into singular HTTP requests, bypassing Google API quota limit throttles.