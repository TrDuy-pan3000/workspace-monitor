# OLP AI — Comprehensive Code Review

**Project path:** `E:\GITHUB LAB\OLPAI`  
**Review date:** 2026-07-14  
**Reviewer:** AI Code Review Agent  
**Scope:** `backend/` (main.py ~1200 lines, database.py), `client/client.py`, `frontend/` (index.html, app.js, style.css)

---

## Executive Summary

- **🚨 Hardcoded live secrets in `.env`**: Telegram bot token, API keys, and chat IDs are stored **in plaintext on disk**. This is the most critical finding — anyone with filesystem access can hijack the Telegram bot or consume the LLM API.
- **🏗️ Monolithic `main.py` (~59KB, ~1200 lines)** mixes FastAPI routes, Telegram bot logic, AI classification, database helpers, daemon threads, and RAG context building. This is the single largest maintainability liability.
- **🔒 Weak auth & permissive CORS**: A single shared API key (`default_olp_key_2026`) with `CORS allow_origins=["*"]` + `allow_credentials=True` is an insecure combination.
- **🐛 Inconsistent time-interval calculations**: The server assumes 2 minutes/log for dashboard stats, while the client actually sends data every 15 seconds. This causes wildly inaccurate KPI and streak numbers.
- **🧵 Race conditions on global state**: `last_telegram_alert_time`, `keystroke_count`, and `click_count` are accessed from multiple threads without proper synchronization.
- **📉 Missing logging, testing, and error recovery**: The project uses `print()` everywhere, has no unit tests, and many error paths silently swallow exceptions.

---

## Architecture

### Strengths
- Clear **three-tier** design: Windows client ↔ FastAPI backend ↔ Web dashboard is appropriate for the use case.
- **Batch log uploads** reduce HTTP overhead.
- **Hybrid classification** (regex → cache → LLM) is a smart trade-off between speed and accuracy.
- **Docker Compose** makes deployment straightforward.
- **Streak tracking** and **proactive alerts** add genuine motivational value.

### Modularity Issues

| Problem | Location | Impact |
|---------|----------|--------|
| `main.py` contains ALL backend logic | `backend/main.py:1~1200` | Impossible to test, reason about, or modify safely |
| Telegram logic mixed with API routes | `main.py:80-410` | Coupling prevents independent testing or replacement |
| Classification engine embedded in main | `main.py:415-500` | Cannot reuse for offline/batch classification |
| RAG context builder in same file | `main.py:200-300` | Tight coupling to DB schema, routes, and LLM config |
| Daemon threads started in `startup_event` | `main.py:410` | No lifecycle management, restart, or health check for background threads |

### Recommended Module Structure

```
backend/
├── main.py                  # App factory, CORS, middleware, static mount, startup
├── config.py                # Settings from env (Pydantic Settings)
├── models.py                # Pydantic request/response models
├── database.py              # DB init, connection helper (KEEP, enhance)
├── routers/
│   ├── __init__.py
│   ├── logs.py              # POST /api/v1/log, POST /api/v1/live-screen
│   ├── dashboard.py         # GET /api/v1/dashboard/stats, /chart, /study-plan
│   └── ai.py                # POST /api/v1/ai/command
├── services/
│   ├── __init__.py
│   ├── classifier.py        # classify_window_title(), TitleClassificationCache
│   ├── telegram.py          # send_message, send_photo, polling loop, incoming handler
│   ├── rag.py               # build_ai_context(), db_get_all_ai_memories()
│   ├── daemons.py           # proactive_alert_daemon(), telegram_polling_loop()
│   └── study.py             # calculate_streak(), db_get_user_activity_report()
└── utils/
    ├── __init__.py
    └── time_helpers.py      # format_minutes_to_hours(), time constants
```

---

## Security Findings

### 🔴 Critical

| # | Finding | File | Line(s) |
|---|---------|------|---------|
| 1 | **Live secrets in `.env` on disk** — Telegram token (`8564330020:AAFZaQsVBvmOspDQXPTVFq0PyUeNMF76vkg`), OpenAI API key (`sk-2c505d602e4d9f8b-…`), Chat ID (`-1003801560523`) are readable by any process/user with filesystem access. | `backend/.env` | 2-4, 7-8 |
| 2 | **`CORS: allow_origins=["*"]` + `allow_credentials=True`** — This is explicitly forbidden by the CORS spec in practice. When credentials are allowed, the wildcard origin is ignored and the actual `Origin` header must match an explicit allowlist. The current config can cause unexpected failures or security warnings. | `backend/main.py` | 18-23 |
| 3 | **Weak default API key** — `default_olp_key_2026` is both the hardcoded default AND the value in `.env`. This single key authenticates all clients; if leaked, there is no way to rotate without downtime. | `backend/main.py:496` + `backend/.env:3` | |
| 4 | **No HTTPS** — All traffic (including API key headers and screenshots) travels over plain HTTP. A MITM attacker can intercept the API key, logs, and screen captures. | docker-compose.yml | ports config |

### 🟡 High

| # | Finding | File | Line(s) |
|---|---------|------|---------|
| 5 | **No rate limiting** on any endpoint — `/api/v1/ai/command` calls an LLM on every request. An attacker could exhaust the API budget. | `main.py` | all routes |
| 6 | **Screenshot upload with no size/type validation** — `UploadFile` is accepted and written directly without checking MIME type, magic bytes, or size limits. A malicious client could fill the disk or upload non-image files. | `main.py` | `upload_live_screen()` |
| 7 | **API key comparison is not constant-time** — `x_api_key != stored_key` is vulnerable to timing side-channel attacks. | `main.py` | 496, 534 |
| 8 | **No input sanitization on `username` in chart endpoint** — `username` is used directly in SQL query (parameterized, so injection-safe) and directly returned in JSON responses. Not exploitable for SQLi, but could leak data. | `main.py` | `get_dashboard_chart()` |
| 9 | **Temporary screenshot file on disk** at `client/temp_{username}.jpg` — deleted in `finally` but the file exists momentarily with identifiable user data. | `client/client.py` | `capture_and_upload_screenshot()` |

### 🟢 Recommendations
- Move secrets out of `.env` file into Docker secrets, a vault, or environment variables set at container runtime (not committed to any file).
- Set `allow_origins` to explicit domain(s); never `["*"]` with credentials.
- Implement per-client API keys stored in DB with a rotation endpoint.
- Add `python-multipart` size limits (e.g., `MAX_CONTENT_SIZE`).
- Add rate limiting (e.g., `slowapi` or middleware).
- Terminate TLS at the reverse proxy (Caddy/Nginx) and use `https://` everywhere.

---

## Performance Issues

### 🔴 High Impact

| # | Issue | Location | Details |
|---|-------|----------|---------|
| 1 | **SQLite connection opened/closed per request** — `get_db()` creates a new `sqlite3.connect()` on every API call. No connection pooling. For a single-user tool this is acceptable, but with multiple concurrent clients + Telegram polling + proactive daemon, it wastes resources. | `main.py:39-42` | Use a single persistent connection with thread-local or a simple pool. SQLite in WAL mode supports concurrent reads. |
| 2 | **N+1 queries in `build_ai_context()`** — For N users, the function queries `SELECT DISTINCT username`, then runs a separate query per user. Same pattern in `get_dashboard_stats()`. | `main.py:219-260` | Fetch all users' stats in a single query with `GROUP BY`. |
| 3 | **`calculate_streak()` backfills day-by-day** — Each iteration runs N SQL queries (one per user). To calculate a 30-day streak, this runs 30×N queries. | `main.py:639-670` | Pre-compute a daily summary table or use a single date-batched query. |

### 🟡 Medium Impact

| # | Issue | Location | Details |
|---|-------|----------|---------|
| 4 | **LLM called for every new window title** — `classify_window_title()` calls the LLM on cache miss with a 12-second timeout. This blocks the request thread for every novel window title. | `main.py:478-500` | Consider async LLM calls, a queue-based approach, or local model inference (e.g., sentence-transformers). |
| 5 | **`proactive_alert_daemon()` calls LLM every 60 minutes** — Continuous token consumption even when no one is active. | `main.py:360-408` | Gate on "any user active today" before calling LLM. |
| 6 | **Polling interval ≠ server assumption** — Client sends every 15 seconds (batched to 8 = 120s), but server dashboard assumes 1 log = 2 minutes. The server-side multiplier is wrong. | `main.py:616` vs `client.py:111` | Read `check_interval_seconds` from the log payload rather than hardcoding. |

### 🟢 Low Impact
- **`get_dashboard_stats()` loads ALL of today's logs into memory** for each user. At thousands of logs/day, this is fine, but consider pagination or aggregation.
- **Frontend polls every 30 seconds** via `setInterval(fetchStats, 30000)`. Consider WebSocket or SSE for true real-time updates.

---

## Code Quality & Maintainability

### Naming & Language Consistency
- **Mixed Vietnamese/English**: Comments, log messages, and even variable names randomly switch between the two (e.g., `# Lấy danh sách các username`, `# Đảm bảo các cột mới tồn tại`).
- **Inconsistent naming conventions**: `user_display` (snake_case) alongside `is_idle`, `reply_text`. 
- **Non-descriptive variable names**: `text_clean`, `k`, `val`, `data`, `res`.

### Logging
- **100% `print()` usage** — no structured logging. No log levels (info/warning/error), no timestamps (except in client), no log rotation.
- **Recommendation**: Replace with `import logging` and use `logging.getLogger(__name__)` with proper levels.

### Error Handling
```python
except Exception as e:
    print(f"[...] Lỗi: {e}")
```
This pattern appears ~25 times. **Overly broad exception handling** masks bugs:
- `except:` catches `SystemExit` and `KeyboardInterrupt` too.
- No distinction between recoverable and fatal errors.
- No error reporting to the user (many failures in Telegram/LLM calls are silently swallowed).

### Duplicated Code

| Duplication | Lines | Suggestion |
|------------|-------|------------|
| Telegram token/chat_id resolution (DB → env fallback) | `send_telegram_message()`, `send_telegram_photo()`, `telegram_polling_loop()` | Extract to `get_telegram_credentials()` helper |
| Stats query pattern | `build_ai_context()` + `get_dashboard_stats()` | Extract to shared `Service` class |
| `[MEM_WRITE]` parsing and DB save | `handle_telegram_incoming_message()`, `proactive_alert_daemon()`, `execute_ai_command()` | Extract to `save_ai_memory_from_text()` helper |
| LLM request boilerplate (headers, URL, response parsing, `[DONE]` stripping) | ~5 locations | Create a `call_llm()` utility function |

### Frontend Quality
- **CDN dependencies** — Tailwind, Chart.js, FontAwesome loaded from CDN. Dashboard is unusable offline or behind a firewall. Bundle or vendor these.
- **No loading/error states** — `fetchStats()` and `fetchStudyPlan()` only `console.error()` on failure; the UI remains in "loading" state forever.
- **`onerror` handler on images** — falls back to placeholder, but if the image URL fails, the user sees a generic placeholder with no indication of why.
- **No responsive design beyond Tailwind defaults** — The 3-column layout on `lg` breaks on tablets.
- **Inline CSS in JS** for `animate-pulse` and `animate-bounce` — these Tailwind animations run continuously, even on hidden elements.

---

## Bugs & Edge Cases

### 🐛 Confirmed Bugs

| # | Bug | File | Lines | Explanation |
|---|-----|------|-------|-------------|
| 1 | **Incorrect time multiplier for KPI calculation** | `main.py:616` | Server calculates `learning_hours = (learning_logs_count * 2) / 60` assuming 1 log = 2 minutes. **But** the client sends one log per scan (15 sec), batched into groups of 8 (120 sec). Each individual log is 15 seconds, not 2 minutes. The actual learning hours are **overstated by 8×**. | 
| 2 | **Same bug in `calculate_streak()`** | `main.py:653` | `hours = (cnt * 2) / 60` — same factor of 2, should be `0.25` (15 sec = 0.25 min). |
| 3 | **Race condition: `last_telegram_alert_time`** | `main.py:562-565` | `now - last_alert > 900` check followed by `last_telegram_alert_time[username] = now` without a lock. Two concurrent requests for the same user can both pass the check and send duplicate alerts. |
| 4 | **Race condition: keystroke/click counters** | `client.py:28-34, 137-145` | The main thread reads `keystroke_count`/`click_count` and resets them to 0, while listener threads are incrementing. The `counters_lock` protects the increment, but the read-and-reset sequence is **not atomic**: `current_keys = keystroke_count; keystroke_count = 0`. A keystroke between these two lines is **lost**. |
| 5 | **`calculate_streak()` loops forever on empty DB** | `main.py:641-670` | If `usernames` is `["bluebird", "partner"]` and NO logs exist, `user_kpis_met` is always 0. For yesterday (`current_check_date != today`), it breaks. But if at least one user has logs from today and meets KPI, then `current_check_date` decrements without bound until a day with zero logs is hit — this works but is O(∞) in worst case. Not an infinite loop but fragile. |
| 6 | **`get_dashboard_stats()` uses hardcoded `["duy", "partner"]` fallback** | `main.py:599` | If the DB is empty, it creates stats entries with lowercase "duy" even though the actual client username might be "bluebird". The duplicate names cause confusion. |
| 7 | **`reply_to_message_id` may fail if original message is deleted** | `main.py:345` | Telegram API will return 400 if the replied-to message no longer exists. No error handling for this specific case. |
| 8 | **No `Content-Type` header check on `/api/v1/live-screen`** | `main.py:529` | A malformed client could send non-image data; it's blindly saved as `.jpg`. |

### 🟡 Edge Cases

| # | Edge Case | Location |
|---|-----------|----------|
| 1 | **Empty `window_title` from `get_active_window_title()`** — returns `"Unknown Window"` if both win32gui and pywinctl fail. The classifier treats this as Learning → cache hit → correct. OK. | `client.py:107` |
| 2 | **Timezone: `datetime.now().date()` vs ISO timestamps** — `log_client_data()` uses `datetime.now().isoformat()` (no timezone). `get_dashboard_stats()` uses `datetime.now().date()` (also no timezone). These match only if server and client are in the same TZ. The container sets `TZ=Asia/Ho_Chi_Minh`, but the code doesn't localize the datetime objects. | multiple locations |
| 3 | **`format_minutes_to_hours(0)` returns `"0 phút"`** — minor but odd for a dashboard showing "0h 0p". | `main.py:692` |
| 4 | **`study_plan` INSERT OR REPLACE** overwrites the same week without warning. | `main.py:714` |
| 5 | **`db_get_user_activity_report()` uses `0.25` minutes per log** which IS correct (15 sec), but the dashboard uses `2` minutes. | `main.py:700` vs `main.py:616` |

---

## main.py Refactoring Plan

The `main.py` file (~1200 lines, ~59KB) must be broken down. Here is a specific extraction plan:

### Phase 1: Service Extraction (no behavior change)

| Module | Extract these functions | Est. Lines |
|--------|----------------------|------------|
| `backend/config.py` | All `os.getenv()` calls + `load_dotenv` + Pydantic `Settings` class | 40 |
| `backend/models.py` | `SingleLogItem`, `BatchLogPayload`, `AICommandPayload` | 20 |
| `backend/services/telegram.py` | `send_telegram_message()`, `send_telegram_photo()`, `telegram_polling_loop()`, `db_add_chat_log()`, `db_write_ai_memory()`, `db_get_all_ai_memories()`, `handle_telegram_incoming_message()`, `build_ai_context()`, `get_telegram_credentials()` (new) | 280 |
| `backend/services/classifier.py` | `classify_window_title()` | 85 |
| `backend/services/daemons.py` | `proactive_alert_daemon()`, `startup_event()` (moved) | 50 |
| `backend/services/helpers.py` | `format_minutes_to_hours()`, `get_db()`, `get_config()`, `set_config()` | 30 |

### Phase 2: Router Extraction

| Module | Extract these endpoints | Est. Lines |
|--------|----------------------|------------|
| `backend/routers/logs.py` | `POST /api/v1/log`, `POST /api/v1/live-screen` | 100 |
| `backend/routers/dashboard.py` | `GET /api/v1/dashboard/stats`, `GET /api/v1/dashboard/chart`, `GET /api/v1/study-plan`, `calculate_streak()` | 120 |
| `backend/routers/ai.py` | `POST /api/v1/ai/command`, `AI_FUNCTIONS` dict, all `db_update_*` functions, `get_system_status()` | 200 |

### Phase 3: Cleanup (smaller remaining `main.py`)

After extraction, `main.py` should only contain:
```python
app = FastAPI(title="...")
app.add_middleware(CORSMiddleware, ...)
app.mount("/static", ...)
app.include_router(logs_router)
app.include_router(dashboard_router)
app.include_router(ai_router)

@app.on_event("startup")
def startup():
    import threading
    threading.Thread(target=telegram_polling_loop, daemon=True).start()
    threading.Thread(target=proactive_alert_daemon, daemon=True).start()
```

Target: **~50 lines** from ~1200.

---

## Priority Recommendations (Sorted by Impact)

| Prio | Action | Type | Effort | Impact |
|------|--------|------|--------|--------|
| **P0** | 🔴 **Remove hardcoded secrets from `.env`** — Use Docker secrets or env vars set at runtime only, never written to a file on disk. Rotate the leaked Telegram token immediately. | Security | 1h | Critical |
| **P1** | 🐛 **Fix time-interval multipliers** — Change all `* 2 / 60` calculations to use `* 15 / 3600` (15 seconds per log). Or better, have the client include `interval_seconds` in the batch payload so the server uses the actual value. | Bug | 30min | High — KPI and streak numbers are wrong by 8× |
| **P1** | 🧵 **Fix race conditions** — (a) Add `threading.Lock` for `last_telegram_alert_time`. (b) Use atomic RMW for keystroke/click counters (e.g., `counters_lock` around the entire read+reset block). | Bug | 30min | High — duplicate alerts and lost keystrokes |
| **P2** | 🏗️ **Split `main.py` into modules** — Follow the plan above. Start with `services/` extraction, then `routers/`. | Maintainability | 4h | Medium — directly blocks all future work |
| **P2** | 🔒 **Fix CORS and credentials** — Replace `allow_origins=["*"]` with `["https://yourdomain.com"]` or remove `allow_credentials=True` if wildcard is needed. | Security | 10min | Medium |
| **P2** | 🔒 **Implement rate limiting** — At minimum on `/api/v1/ai/command` (LLM calls cost money) and `/api/v1/log` (prevent DB flooding). | Security | 1h | Medium |
| **P2** | 🔒 **Validate uploaded files** — Check MIME type, magic bytes, max file size (e.g., 2MB) in `upload_live_screen()`. | Security | 20min | Medium |
| **P3** | 📝 **Replace `print()` with structured logging** — Use `import logging` with log levels and timestamps. | Quality | 1h | Low-Med |
| **P3** | ♻️ **Deduplicate LLM call boilerplate** — Extract `call_llm(messages, tools=None, timeout=30)` utility. | Quality | 1h | Low-Med |
| **P3** | ♻️ **Deduplicate Telegram credential resolution** — Extract `get_telegram_credentials()` helper. | Quality | 15min | Low |
| **P3** | 🐛 **Fix `calculate_streak()` to not iterate unbounded** — Limit to 365 days and add a safety break. | Bug | 15min | Low |
| **P3** | 🌐 **Localize datetime objects** — Use `pytz` or `zoneinfo` (Python 3.9+) with the configured TZ instead of relying on `datetime.now()` which uses system local time. | Quality | 30min | Low |
| **P4** | 🧪 **Add unit tests** — Start with `classify_window_title()`, `calculate_streak()`, and the API endpoints using `TestClient` from FastAPI. | Quality | 4h | Low (but valuable) |
| **P4** | 📦 **Vendor frontend dependencies** — Download Tailwind, Chart.js, FontAwesome instead of CDN. Add a `package.json` or script to update them. | Quality | 1h | Low |
| **P4** | 📋 **Add proper `__init__.py` files** — After splitting, ensure all packages are importable. | Quality | 10min | Low |

---

## Additional Notes

### Client (`client.py`)
- The `pygetwindow` / `pywinctl` fallback is a good practice for environments without `win32gui`.
- The random screenshot timer (`random.randint(180, 720)`) makes captures unpredictable — good for accountability.
- The batch upload retry logic is missing: if the server is down, the local logs queue grows unbounded (memory leak).
- `ImageGrab.grab()` captures ALL monitors. On multi-monitor setups, the JPEG will be very wide. Consider specifying `bbox` or `all_screens=False`.

### Database (`database.py`)
- The `ALTER TABLE ADD COLUMN` fallbacks are a **migration anti-pattern**. Consider ` Alembic` or a versioned schema.
- No indexes on `chat_history.chat_id` or `user_logs.timestamp` beyond the composite index. Chat history queries by `chat_id` will be full-table scans.
- The `title_classification_cache` has no TTL/eviction policy — it will grow indefinitely.

### Frontend
- The chart tab vs. screen tab switching works but there is no WebSocket/SSE for live screen updates. The user must manually switch tabs to refresh the image.
- AI chat widget uses `escapeHTML()` to prevent XSS — good practice.
- The dashboard image cache-bust (`?t=${new Date().getTime()}`) works but is aggressive (polls every 30s means a new image request every 30s even if nothing changed).

---

## Summary

OLP AI is a thoughtfully designed productivity system with genuine utility — the Telegram integration, hybrid classifier, and streak tracking show real user understanding. However, the **code does not yet match the quality of the concept**. The monolithic backend, hardcoded secrets, inconsistent time calculations, and threading bugs represent significant technical debt that should be addressed before production deployment. **The most urgent fix is rotating the leaked Telegram and OpenAI API keys**, followed by the KPI calculation bug that silently reports incorrect learning hours.

**Total estimated effort for P0-P2 items:** ~8 hours  
**Total estimated effort for all items:** ~16 hours
