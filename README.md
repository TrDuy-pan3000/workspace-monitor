# Workspace Monitor

> Real-time productivity tracking for small teams — powered by FastAPI, a lightweight AI classifier, and a Telegram bot that keeps everyone accountable.

[![Python 3.11+](https://img.shields.io/badge/Python-3.11+-3776AB?style=flat-square&logo=python&logoColor=white)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.110-009688?style=flat-square&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![SQLite](https://img.shields.io/badge/SQLite-3-003B57?style=flat-square&logo=sqlite&logoColor=white)](https://sqlite.org)
[![Docker](https://img.shields.io/badge/Docker-Compose-2496ED?style=flat-square&logo=docker&logoColor=white)](https://docs.docker.com/compose)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow?style=flat-square)](LICENSE)

---

## Overview

Workspace Monitor is a self-hosted productivity system built for teams who want transparent, data-driven accountability. A lightweight Windows client runs silently in the background on each member's machine, periodically reporting the active window title and idle state to a central FastAPI server. The server classifies each activity using a hybrid AI engine, persists the data in SQLite, and surfaces everything through a live web dashboard.

When a user gets distracted for too long, the system automatically fires a Telegram alert — with a screenshot — to the group chat, triggering peer accountability. A conversational Telegram bot (backed by any OpenAI-compatible LLM endpoint) is also available for on-demand productivity check-ins.

---

## Key Features

| Feature | Description |
|---|---|
| **Real-time Dashboard** | Live web UI showing per-member status, KPI progress, time-series efficiency charts, and live screenshots |
| **Hybrid Activity Classifier** | Combines rule-based keyword matching with an LLM fallback (via any OpenAI-compatible API) to categorize window titles as `Learning`, `Distracted`, or `Idle` |
| **Classification Cache** | SQLite-backed cache prevents redundant LLM calls for previously seen window titles |
| **Idle Detection** | Client-side mouse/keyboard listener detects inactivity and reports it separately |
| **Live Screenshot Upload** | Client periodically captures and uploads a compressed JPEG screenshot; also triggered immediately upon `Distracted` detection |
| **Telegram Alerts** | Automated group alerts with screenshot attachment when a member is continuously distracted beyond the configured threshold |
| **Telegram AI Bot** | Long-polling bot with configurable trigger keywords; responds in character using the configured LLM; falls back gracefully on connection errors |
| **AI UI Controller** | Floating chat widget on the dashboard accepts natural-language commands to update KPIs, study plans, and system configuration without touching the database directly |
| **Streak Counter** | Tracks the number of consecutive days all team members met their daily KPI |

---

## Architecture

```
┌────────────────────────────────────────────────────────────────────┐
│                         VPS  (Docker)                              │
│                                                                    │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │                    FastAPI  :8000                            │  │
│  │                                                              │  │
│  │  POST /api/v1/log              ← Receives client heartbeats  │  │
│  │  POST /api/v1/live-screen      ← Receives screenshot upload  │  │
│  │  GET  /api/v1/dashboard/stats  ← Dashboard polling           │  │
│  │  GET  /api/v1/dashboard/chart  ← Per-user chart data         │  │
│  │  GET  /api/v1/study-plan       ← Study plan CRUD             │  │
│  │  POST /api/v1/ai/command       ← Natural-language config     │  │
│  │                                                              │  │
│  │  Hybrid Classifier  ──►  SQLite DB  (user_logs,             │  │
│  │  (Rules → Cache → LLM)           system_config,             │  │
│  │                                   title_classification_cache,│  │
│  │  Telegram Polling Thread          study_plan)                │  │
│  │  └─ Long-polls Telegram API                                  │  │
│  │  └─ Handles inbound chat via LLM                             │  │
│  └──────────────────────────────────────────────────────────────┘  │
│                         /static/ (served by FastAPI)               │
└────────────────────────────────────────────────────────────────────┘
              ▲  HTTP API (X-API-Key)            ▲  Browser
              │                                  │
┌─────────────┴──────────┐          ┌────────────┴────────────────┐
│   Client  (Windows)    │          │     Dashboard  (Browser)    │
│                        │          │                             │
│  pynput  mouse/kb      │          │  Live status cards          │
│  listener → idle check │          │  Pie + Line charts          │
│                        │          │  Live screenshot panel      │
│  win32gui / pywinctl   │          │  Study plan panel           │
│  → active window title │          │  AI chat widget (float)     │
│                        │          │                             │
│  Pillow screenshot     │          │  Polls /dashboard/stats     │
│  → JPEG quality 40     │          │  every 30 s automatically   │
│                        │          └─────────────────────────────┘
│  Heartbeat every N sec │
│  → POST /api/v1/log    │
└────────────────────────┘
```

---

## Project Structure

```
.
├── docker-compose.yml
├── .gitignore
├── README.md
│
├── backend/
│   ├── main.py                  # All API routes, AI classifier, Telegram integration
│   ├── database.py              # SQLite schema bootstrap
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── .env.example             # Environment variable template
│   └── static/                  # Served at /static/
│       ├── index.html
│       ├── style.css
│       ├── app.js
│       └── latest_<username>.jpg   # Auto-generated, not committed
│
├── frontend/                    # Source files for the static frontend
│   ├── index.html
│   ├── style.css
│   └── app.js
│
└── client/
    ├── client.py                # Windows monitoring agent
    ├── config.example.json      # Configuration template
    ├── requirements.txt
    ├── run_client.bat           # Launch with visible CMD window
    └── run_client_silent.vbs    # Launch silently in background
```

---

## Getting Started

### Prerequisites

- **Server:** Ubuntu 20.04+ VPS with Docker and Docker Compose installed
- **Client machines:** Windows 10/11 with Python 3.10+
- **LLM endpoint:** Any OpenAI-compatible API (local via Ollama, or a hosted provider)
- **Telegram Bot:** A bot token from [@BotFather](https://t.me/BotFather) and a group/channel Chat ID

---

### 1. Server Setup

```bash
# SSH into your VPS
ssh root@YOUR_VPS_IP

# Clone the repository
git clone https://github.com/YOUR_USERNAME/workspace-monitor.git
cd workspace-monitor

# Configure environment variables
cp backend/.env.example backend/.env
nano backend/.env
```

Fill in all values in `backend/.env`:

```env
# Telegram
TELEGRAM_TOKEN=<your bot token from @BotFather>
TELEGRAM_CHAT_ID=<group or channel chat ID>

# LLM backend (OpenAI-compatible)
OPENAI_API_BASE=http://your-llm-host:port/v1
OPENAI_API_KEY=<your api key>
MODEL_NAME=<model name, e.g. gpt-4o-mini or a local model>

# Timezone
TZ=Asia/Ho_Chi_Minh
```

> **How to get your Telegram Chat ID:** Add [@userinfobot](https://t.me/userinfobot) to your group — it will immediately reply with the Chat ID.

```bash
# Build and start the container
docker compose up -d --build

# Verify it is running
docker logs -f olp_ai_backend
```

You should see:
```
INFO:     Application startup complete.
[Telegram Polling] Starting Telegram polling thread...
```

The dashboard is now available at `http://YOUR_VPS_IP:8000/static/index.html`.

---

### 2. Client Setup (Windows)

Repeat these steps on each team member's machine.

```bash
# Navigate to the client directory
cd client

# Copy the configuration template
copy config.example.json config.json

# Edit config.json
notepad config.json
```

**`client/config.json` reference:**

| Key | Description | Default |
|-----|-------------|---------|
| `server_url` | Your VPS address including port | `http://localhost:8000` |
| `api_key` | Must match the `api_key` stored in the server database | `default_olp_key_2026` |
| `username` | Display name for this machine (lowercase, no spaces) | `user` |
| `idle_threshold_seconds` | Seconds without mouse/keyboard input before marking as `Idle` | `300` |
| `check_interval_seconds` | How often to send a heartbeat to the server | `120` |
| `live_screen_interval_seconds` | How often to upload a screenshot under normal conditions | `900` |

```bash
# Install Python dependencies
pip install -r requirements.txt

# Run the client (visible CMD window — useful for debugging)
run_client.bat

# Run the client silently in the background (recommended for daily use)
# Double-click: run_client_silent.vbs
```

To verify the client is running, open Task Manager and look for a `python.exe` process. You should also see your username appear in the Dashboard within the next heartbeat interval.

---

## Configuration

All runtime configuration is stored in the `system_config` SQLite table and can be updated live via the AI Chat Widget on the Dashboard (natural language) or directly via SQL.

### Via AI Chat Widget

Click the floating chat button in the bottom-right corner of the Dashboard and type commands in plain language:

```
"Set the daily KPI to 4 hours"
"Update the distraction limit to 20 minutes"
"Add week 3 to the study plan: topic Deep Learning, tasks: implement backprop, train MNIST"
"What is the current KPI?"
```

### Via Docker exec (direct SQL)

```bash
# Update KPI
docker exec -it olp_ai_backend python -c "
import sqlite3
conn = sqlite3.connect('/app/database.db')
conn.cursor().execute(\"UPDATE system_config SET value='4.0' WHERE key='kpi_hours'\")
conn.commit()
print('Done.')
"

# Update Telegram credentials without restarting
docker exec -it olp_ai_backend python -c "
import sqlite3
conn = sqlite3.connect('/app/database.db')
cursor = conn.cursor()
cursor.execute(\"UPDATE system_config SET value='TOKEN' WHERE key='telegram_token'\")
cursor.execute(\"UPDATE system_config SET value='CHAT_ID' WHERE key='telegram_chat_id'\")
conn.commit()
print('Done.')
"
```

---

## API Reference

All endpoints that accept writes require the `X-API-Key` header.

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `POST` | `/api/v1/log` | ✅ | Submit a heartbeat with window title and idle state |
| `POST` | `/api/v1/live-screen` | ✅ | Upload a JPEG screenshot (multipart/form-data) |
| `GET` | `/api/v1/dashboard/stats` | — | Aggregated today stats for all users + streak |
| `GET` | `/api/v1/dashboard/chart?username=X` | — | Per-user pie + hourly line chart data |
| `GET` | `/api/v1/study-plan` | — | List all study plan entries |
| `POST` | `/api/v1/ai/command` | — | Submit a natural-language command |

---

## Activity Classification

The classifier runs in three stages on every inbound heartbeat:

1. **Rule-based matching** — A set of curated regex patterns for common development tools, documentation sites, and known distraction sources. Zero latency, zero cost. Handles the majority of requests.

2. **SQLite cache lookup** — If the normalized window title was seen before, the cached result is returned immediately.

3. **LLM fallback** — Unknown titles are sent to the configured OpenAI-compatible endpoint with a structured prompt requesting a `{"status": "Learning"|"Distracted", "efficiency": 0-100}` JSON response. The result is written to the cache so the same title is never classified twice.

Possible status values returned to the client:

| Status | Meaning |
|--------|---------|
| `Learning` | Active productive work |
| `Distracted` | Entertainment, social media, or unrelated browsing |
| `Idle` | No mouse/keyboard input for `idle_threshold_seconds` |
| `Offline` | No heartbeat received today |

---

## Telegram Bot

### Automated Alerts (passive)

When a user's status is `Distracted` for every log entry within the configured `allowed_distraction` window (default 15 minutes), the bot sends a group message with the latest screenshot attached. A per-user cooldown of 15 minutes prevents alert spam.

### Conversational Bot (active)

The bot responds to direct messages or group messages containing configured trigger keywords (`sifu`, `bot`, `@your_bot_handle`, etc.). It receives the full inbound text, queries recent activity history from the database, and constructs a contextual prompt sent to the configured LLM. The response is sent back to the same chat.

---

## Deploying Code Updates

The `backend/static/` directory is bind-mounted into the container, so frontend changes take effect immediately without a restart. Backend Python changes require a container restart:

```bash
# From your local Windows machine — sync backend
scp "path\to\backend\main.py" root@YOUR_VPS_IP:/root/workspace-monitor/backend/main.py

# On the VPS — restart only the backend container
cd /root/workspace-monitor && docker compose restart backend
```

---

## Troubleshooting

**Dashboard not loading / 502 error**
```bash
docker ps                                  # Is the container running?
docker logs olp_ai_backend --tail 50       # Check for startup errors
docker compose restart backend             # Restart if crashed
```

**Study hours not increasing despite activity**
- Confirm `run_client_silent.vbs` is running (check Task Manager → `python.exe`)
- The active window title must be specific enough for the classifier (generic titles like `Desktop Window Manager` are unclassifiable)
- Verify the client's `check_interval_seconds` — hours are calculated from log count × interval

**Telegram bot not responding**
```bash
# Check polling logs
docker logs olp_ai_backend | grep Telegram

# Test sending a message manually
docker exec -it olp_ai_backend python -c "
from main import send_telegram_message
send_telegram_message('Connection test.')
"
```

**Client cannot reach the server**
- Ensure port `8000` is open in your VPS firewall / security group
- Confirm `api_key` in `config.json` matches the value in `system_config` (`SELECT value FROM system_config WHERE key = 'api_key'`)

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| API Server | FastAPI + Uvicorn |
| Database | SQLite (via Python `sqlite3`) |
| Containerization | Docker Compose |
| Frontend | Vanilla HTML / CSS / JS (no framework) |
| Windows Client | Python, `pynput`, `win32gui`, `psutil`, `Pillow` |
| AI Classification | Configurable OpenAI-compatible LLM endpoint |
| Notifications | Telegram Bot API (long polling) |

---

## License

MIT — see [LICENSE](LICENSE) for details.