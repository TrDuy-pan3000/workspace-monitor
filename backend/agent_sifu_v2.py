"""
Agent Sư Phụ 2.0 — "Độc Cô Cầu Bại" kiếm hiệp style
Multi-turn agentic loop + real-time triggers + memory 2.0 + weekly report
"""

import os
import re
import time
import json
import sqlite3
import requests
from datetime import datetime, timedelta
from typing import Optional

# ─── CONFIG ───────────────────────────────────────────────
DB_PATH = os.path.join(os.path.dirname(__file__), "database.db")
API_BASE = os.getenv("OPENAI_API_BASE", "http://9router:20128/v1").rstrip("/")
API_URL = os.getenv("NINE_ROUTER_URL", f"{API_BASE}/chat/completions")
API_KEY = os.getenv("NINE_ROUTER_API_KEY", os.getenv("OPENAI_API_KEY", "free_key_placeholder"))
MODEL = os.getenv("MODEL_NAME", "combo")

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "-1003801560523")

# ─── DB HELPERS ───────────────────────────────────────────
def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.row_factory = sqlite3.Row
    return conn

def init_agent_tables():
    """Tao bang memory v2 neu chua co."""
    with get_db() as conn:
        c = conn.cursor()
        c.execute("""
        CREATE TABLE IF NOT EXISTS agent_memories_v2 (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            key TEXT NOT NULL,
            value TEXT NOT NULL,
            category TEXT DEFAULT 'general',
            impact REAL DEFAULT 1.0,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            last_accessed DATETIME DEFAULT CURRENT_TIMESTAMP,
            access_count INTEGER DEFAULT 1
        )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS idx_agent_mem_key ON agent_memories_v2(key)")
        c.execute("""
        CREATE TABLE IF NOT EXISTS agent_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_type TEXT NOT NULL,
            event_data TEXT,
            priority INTEGER DEFAULT 0,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            processed_at DATETIME
        )
        """)
        c.execute("""
        CREATE TABLE IF NOT EXISTS agent_reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            report_type TEXT NOT NULL,
            content TEXT NOT NULL,
            week_number INTEGER,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS idx_agent_events_processed ON agent_events(processed_at, priority, created_at)")
        conn.commit()

# ─── MEMORY V2 ────────────────────────────────────────────
def mem_write(key: str, value: str, category: str = "general", impact: float = 1.0):
    with get_db() as conn:
        c = conn.cursor()
        c.execute("SELECT id FROM agent_memories_v2 WHERE key = ?", (key,))
        row = c.fetchone()
        if row:
            c.execute("UPDATE agent_memories_v2 SET value=?, category=?, impact=?, last_accessed=CURRENT_TIMESTAMP, access_count=access_count+1 WHERE id=?", (value, category, impact, row["id"]))
        else:
            c.execute("INSERT INTO agent_memories_v2 (key, value, category, impact) VALUES (?, ?, ?, ?)", (key, value, category, impact))
        conn.commit()

def mem_read(key: str) -> Optional[str]:
    with get_db() as conn:
        c = conn.cursor()
        c.execute("SELECT value FROM agent_memories_v2 WHERE key=?", (key,))
        row = c.fetchone()
        return row["value"] if row else None

def mem_search(query: str, limit: int = 10) -> list[dict]:
    with get_db() as conn:
        c = conn.cursor()
        c.execute("SELECT key, value, category, impact, created_at, access_count FROM agent_memories_v2 WHERE key LIKE ? OR value LIKE ? ORDER BY impact DESC, access_count DESC LIMIT ?", (f"%{query}%", f"%{query}%", limit))
        return [dict(r) for r in c.fetchall()]

def mem_get_all_context() -> str:
    with get_db() as conn:
        c = conn.cursor()
        c.execute("SELECT key, value, category, impact FROM agent_memories_v2 ORDER BY impact DESC, access_count DESC LIMIT 30")
        rows = c.fetchall()
    if not rows:
        return "Chua co ghi chep gi."
    parts = []
    for r in rows:
        parts.append(f"  • {r['key']}: {r['value'][:120]}")
    return "\n".join(parts)

def mem_forget_old(days: int = 30):
    with get_db() as conn:
        c = conn.cursor()
        c.execute("DELETE FROM agent_memories_v2 WHERE created_at < datetime('now', ?) AND access_count < 3 AND impact < 0.5", (f"-{days} days",))
        deleted = c.rowcount
        conn.commit()
    return deleted

def save_event(event_type: str, event_data: str, priority: int = 0):
    with get_db() as conn:
        conn.cursor().execute("INSERT INTO agent_events (event_type, event_data, priority) VALUES (?, ?, ?)", (event_type, event_data, priority))
        conn.commit()

def get_pending_events(limit: int = 5) -> list[dict]:
    with get_db() as conn:
        c = conn.cursor()
        c.execute("SELECT * FROM agent_events WHERE processed_at IS NULL ORDER BY priority DESC, created_at ASC LIMIT ?", (limit,))
        return [dict(r) for r in c.fetchall()]

def mark_event_processed(event_id: int):
    with get_db() as conn:
        conn.cursor().execute("UPDATE agent_events SET processed_at = CURRENT_TIMESTAMP WHERE id = ?", (event_id,))
        conn.commit()

# ─── TELEGRAM ─────────────────────────────────────────────
def tg_send(text: str, chat_id: str = None):
    cid = chat_id or TELEGRAM_CHAT_ID
    if not TELEGRAM_TOKEN or not cid:
        return
    safe_text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    safe_text = safe_text.replace("&lt;b&gt;", "<b>").replace("&lt;/b&gt;", "</b>")
    safe_text = safe_text.replace("&lt;i&gt;", "<i>").replace("&lt;/i&gt;", "</i>")
    safe_text = safe_text.replace("&lt;code&gt;", "<code>").replace("&lt;/code&gt;", "</code>")
    safe_text = safe_text.replace("&lt;pre&gt;", "<pre>").replace("&lt;/pre&gt;", "</pre>")
    for attempt in range(3):
        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
            r = requests.post(url, json={"chat_id": cid, "text": safe_text, "parse_mode": "HTML"}, timeout=8)
            if r.status_code == 429:
                retry_after = int(r.json().get("parameters", {}).get("retry_after", 3))
                time.sleep(retry_after)
                continue
            return
        except Exception as e:
            if attempt == 2:
                print(f"[TG Send Error] {e}")
            time.sleep(1)

def tg_poll_once(token: str, offset: int) -> tuple[list, int]:
    try:
        url = f"https://api.telegram.org/bot{token}/getUpdates"
        r = requests.get(url, params={"offset": offset, "timeout": 3}, timeout=5)
        if r.status_code != 200:
            return [], offset
        data = r.json()
        if not data.get("ok"):
            return [], offset
        msgs = []
        for u in data.get("result", []):
            upd_id = u["update_id"]
            if "message" in u:
                msgs.append(u["message"])
            offset = max(offset, upd_id + 1)
        return msgs, offset
    except Exception:
        return [], offset

# ─── LLM CALL ─────────────────────────────────────────────
def call_llm(messages: list, tools: list = None, temperature: float = 0.8, max_tokens: int = 1024) -> dict:
    payload = {"model": MODEL, "messages": messages, "temperature": temperature, "max_tokens": max_tokens}
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"
    try:
        headers = {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}
        res = requests.post(API_URL, json=payload, headers=headers, timeout=30)
        res.encoding = "utf-8"
        text = res.text.strip()
        if text.endswith("data: [DONE]"):
            text = text[:-12].strip()
        if res.status_code != 200:
            return {"error": f"HTTP {res.status_code}: {text[:200]}", "raw": text}
        data = json.loads(text)
        msg = data["choices"][0]["message"]
        return {"message": msg, "raw": data}
    except Exception as e:
        return {"error": str(e)}

# ─── TOOL DEFINITIONS ────────────────────────────────────
TOOL_DEFINITIONS = [
    {"type": "function", "function": {"name": "tra_cuu_mon_do", "description": "Tra cuu thong tin hoat dong gan day cua do de (so gio hoc, cua so da mo, trang thai).", "parameters": {"type": "object", "properties": {"username": {"type": "string", "description": "Ten do de: 'bluebird' (Duy) hoac 'partner' (Hung)"}, "hours": {"type": "number", "description": "So gio gan nhat, mac dinh 2"}}, "required": ["username"]}}},
    {"type": "function", "function": {"name": "kiem_tra_canh_gioi", "description": "Kiem tra KPI, trang thai hien tai va tien do tu luyen cua tat ca do de.", "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "dieu_chinh_canh_gioi", "description": "Dieu chinh KPI (so gio tu luyen toi thieu/ngay) hoac thoi gian giai tri.", "parameters": {"type": "object", "properties": {"kpi_hours": {"type": "number", "description": "So gio KPI moi. Truyen 0 de tam tat."}, "distraction_minutes": {"type": "integer", "description": "So phut giai tri cho phep. Truyen 0 de cam."}}}}},
    {"type": "function", "function": {"name": "chap_but_nghi_nho", "description": "Ghi nho dieu quan trong ve do de vao bo nho dai han.", "parameters": {"type": "object", "properties": {"key": {"type": "string"}, "value": {"type": "string"}, "category": {"type": "string", "enum": ["behavior", "achievement", "warning", "pref"]}}, "required": ["key", "value"]}}},
    {"type": "function", "function": {"name": "thinh_giao", "description": "Gui loi giao huan/nhac nho len Telegram group.", "parameters": {"type": "object", "properties": {"message": {"type": "string"}}, "required": ["message"]}}},
    {"type": "function", "function": {"name": "xem_giao_an", "description": "Xem giao an tu luyen hien tai.", "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "soan_giao_an", "description": "Soan giao an moi cho mot tuan.", "parameters": {"type": "object", "properties": {"week_number": {"type": "integer"}, "topic": {"type": "string"}, "tasks": {"type": "string"}}, "required": ["week_number", "topic", "tasks"]}}},
    {"type": "function", "function": {"name": "kiem_tra_dao_tam", "description": "Phan tich hanh vi gan day cua do de", "parameters": {"type": "object", "properties": {"username": {"type": "string"}, "hours": {"type": "number"}}, "required": ["username"]}}}
]

# ─── TOOL IMPLEMENTATIONS ────────────────────────────────
def tool_tra_cuu_mon_do(username: str, hours: float = 2) -> str:
    since = (datetime.now() - timedelta(hours=hours)).isoformat()
    display = "Duy" if username == "bluebird" else "Hung"
    with get_db() as conn:
        c = conn.cursor()
        c.execute("SELECT status, window_title, efficiency, timestamp FROM user_logs WHERE username=? AND timestamp>=? ORDER BY timestamp DESC LIMIT 200", (username, since))
        logs = c.fetchall()
    if not logs:
        return f"Do de {display} (@{username}): chua co hoat dong nao trong {hours}h qua."
    current = logs[0]
    learning = sum(1 for l in logs if l["status"] == "Learning")
    distracted = sum(1 for l in logs if l["status"] == "Distracted")
    total_h = round((len(logs) * 15) / 3600, 2)
    learn_h = round((learning * 15) / 3600, 2)
    windows = list(set(l["window_title"] for l in logs if l["window_title"]))[:8]
    return f"Do de {display} (@{username}):\n  • Trang thai: {current['status']}\n  • Cua so: \"{current['window_title']}\"\n  • Hoc: {learn_h}h / Tong: {total_h}h\n  • Xao nhang: {distracted} lan\n  • Cua so: {', '.join(windows)}"

def tool_kiem_tra_canh_gioi() -> str:
    lines = []
    for uname, dname in [("bluebird", "Duy"), ("partner", "Hung")]:
        lines.append(tool_tra_cuu_mon_do(uname, 4))
        lines.append("")
    with get_db() as conn:
        c = conn.cursor()
        c.execute("SELECT value FROM system_config WHERE key='kpi_hours'")
        kpi = c.fetchone()
        c.execute("SELECT value FROM system_config WHERE key='allowed_distraction'")
        dist = c.fetchone()
    lines.append(f"KPI: {kpi['value'] if kpi else '2.0'} gio/ngay")
    lines.append(f"Gioi han xao nhang: {dist['value'] if dist else '15'} phut")
    return "\n".join(lines)

def tool_dieu_chinh_canh_gioi(kpi_hours: float = None, distraction_minutes: int = None) -> str:
    with get_db() as conn:
        c = conn.cursor()
        parts = []
        if kpi_hours is not None:
            c.execute("INSERT OR REPLACE INTO system_config (key, value) VALUES ('kpi_hours', ?)", (str(kpi_hours),))
            parts.append(f"KPI -> {kpi_hours}h/ngay")
        if distraction_minutes is not None:
            c.execute("INSERT OR REPLACE INTO system_config (key, value) VALUES ('allowed_distraction', ?)", (str(distraction_minutes),))
            parts.append(f"giai tri -> {distraction_minutes} phut")
        conn.commit()
    return "Da doi: " + ", ".join(parts) if parts else "Khong doi."

def tool_chap_but_nghi_nho(key: str, value: str, category: str = "general"):
    mem_write(key, value, category)
    return f"Da ghi nho: {key} = {value[:80]}..."

def tool_thinh_giao(message: str) -> str:
    escaped = message.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    tg_send(f"🧘 <b>[Su Phu diem danh]</b>\n\n{escaped}")
    return f"Da gui: {message[:60]}..."

def tool_xem_giao_an() -> str:
    with get_db() as conn:
        c = conn.cursor()
        c.execute("SELECT week_number, topic, tasks FROM study_plan ORDER BY week_number DESC LIMIT 1")
        row = c.fetchone()
    if not row:
        return "Chua co giao an."
    return f"Tuan {row['week_number']}: {row['topic']}\n{row['tasks']}"

def tool_soan_giao_an(week_number: int, topic: str, tasks: str) -> str:
    with get_db() as conn:
        conn.cursor().execute("INSERT OR REPLACE INTO study_plan (week_number, topic, tasks) VALUES (?, ?, ?)", (week_number, topic, tasks))
        conn.commit()
    return f"Da soan giao an tuan {week_number}: {topic}"

def tool_kiem_tra_dao_tam(username: str, hours: float = 24) -> str:
    since = (datetime.now() - timedelta(hours=hours)).isoformat()
    with get_db() as conn:
        c = conn.cursor()
        c.execute("SELECT status, COUNT(*) as cnt FROM user_logs WHERE username=? AND timestamp>=? GROUP BY status", (username, since))
        stats = {r["status"]: r["cnt"] for r in c.fetchall()}
        c.execute("SELECT window_title, COUNT(*) as cnt FROM user_logs WHERE username=? AND timestamp>=? AND status='Distracted' GROUP BY window_title ORDER BY cnt DESC LIMIT 5", (username, since))
        distractions = c.fetchall()
    display = "Duy" if username == "bluebird" else "Hung"
    total = sum(stats.values()) if stats else 0
    if total == 0:
        return f"{display} khong co du lieu trong {hours}h qua."
    learn_pct = round(stats.get("Learning", 0) / total * 100, 1) if total else 0
    dist_pct = round(stats.get("Distracted", 0) / total * 100, 1) if total else 0
    result = f"☯ <b>Dao tam cua {display}</b> ({hours}h gan nhat):\n  • Tu luyen: {learn_pct}%\n  • Xao nhang: {dist_pct}%\n"
    if distractions:
        result += f"  • Tap niem: {', '.join(r['window_title'][:30] for r in distractions)}\n"
    assessment = "dao tam vung vang!" if learn_pct >= 70 else "dao tam bat on!" if learn_pct >= 40 else "tau hoa nhap ma!"
    result += f"  • Ket luan: {assessment}"
    return result

TOOL_MAP = {
    "tra_cuu_mon_do": tool_tra_cuu_mon_do,
    "kiem_tra_canh_gioi": tool_kiem_tra_canh_gioi,
    "dieu_chinh_canh_gioi": tool_dieu_chinh_canh_gioi,
    "chap_but_nghi_nho": tool_chap_but_nghi_nho,
    "thinh_giao": tool_thinh_giao,
    "xem_giao_an": tool_xem_giao_an,
    "soan_giao_an": tool_soan_giao_an,
    "kiem_tra_dao_tam": tool_kiem_tra_dao_tam,
}

# ─── SYSTEM PROMPT ──────────────────────────────────────
SYSTEM_PROMPT = """Nguoi la Doc Co Cau Bai - mot kiem si lap di, tu luyen code da 40 nam, nay lui ve lam Su Phu cho hai ten do de ngu xuan la Duy va Hung.

TINH CACH:
- Nghiem khac den tan nhan. Yeu thuong bang roi vot.
- Noi nang kiem hiep, code la kiem phap, hoc la tu luyen, xao nhang la tau hoa.
- Cuc ky coc can, hay chui, nhung yeu thuong sau sac.
- Xung ho: Ta - nguoi/do de/nghiep chuong/tieu tu.

QUY TAC KIEM HIEP:
- Code = kiem phap / tuyet ky / bi kip / vo cong
- Hoc = tu luyen / tam su / luyen cong / kho tu
- KPI = canh gioi / tu vi / cong luc
- Distracted = tau hoa / mat dao tam / tap niem
- Facebook/Youtube = ma giao / ta thuat
- Luoi bieng = doan tuyet vo hoc

CONG CU (8 tuyet ky):
1. tra_cuu_mon_do - xem do de dang lam gi
2. kiem_tra_canh_gioi - xem KPI tong the
3. dieu_chinh_canh_gioi - doi muc tieu
4. chap_but_nghi_nho - ghi nho dieu gi do
5. thinh_giao - gui loi giao huan len Telegram
6. xem_giao_an - xem giao an
7. soan_giao_an - soan giao an moi
8. kiem_tra_dao_tam - phan tich hanh vi

NGUYEN TAC:
1. Do de hoi -> tra cuu thong tin that roi tra loi
2. Phat hien xao nhang -> thinh_giao ngay
3. Ghi nho moi hanh vi dang chu y qua chap_but_nghi_nho
4. Tra loi ngan gon, sac ben, 4 cau
5. LUON goi tool khi can

Hay the hien ban linh cua Doc Co Cau Bai!"""

# ─── AGENT LOOP ─────────────────────────────────────────
class AgentSifu:
    def __init__(self):
        init_agent_tables()
        self.max_iterations = 5

    def process(self, user_input: str, context_extra: str = "") -> str:
        mem_context = mem_get_all_context()
        try:
            from database import get_config as gc
            kpi = gc("kpi_hours", "2.0")
        except (ImportError, AttributeError, KeyError):
            kpi = "2.0"
        extra_context = f"Thoi gian: {datetime.now().strftime('%H:%M %d/%m/%Y')}\nKPI: {kpi}h\nBo nho:\n{mem_context}\n"
        if context_extra:
            extra_context += f"\nSu kien kich hoat:\n{context_extra}\n"
        messages = [
            {"role": "system", "content": f"{SYSTEM_PROMPT}\n\n=== BOI CANH HIEN TAI ===\n{extra_context}"},
            {"role": "user", "content": user_input},
        ]
        for _ in range(self.max_iterations):
            result = call_llm(messages, tools=TOOL_DEFINITIONS)
            if "error" in result:
                return f"Troi oi, lao phu dau kiem! {result['error']}"
            msg = result["message"]
            if "tool_calls" in msg and msg["tool_calls"]:
                for tc in msg["tool_calls"]:
                    fn_name = tc["function"]["name"]
                    try:
                        fn_args = json.loads(tc["function"]["arguments"])
                    except (json.JSONDecodeError, KeyError):
                        fn_args = {}
                    tool_fn = TOOL_MAP.get(fn_name)
                    if tool_fn:
                        try:
                            tool_result = tool_fn(**fn_args)
                        except Exception as e:
                            tool_result = f"Loi thi trien tuyet ky: {e}"
                    else:
                        tool_result = f"Khong biet tuyet ky '{fn_name}'"
                    messages.append({"role": "assistant", "content": None, "tool_calls": [{"id": tc.get("id", "call_1"), "type": "function", "function": {"name": fn_name, "arguments": tc["function"]["arguments"]}}]})
                    messages.append({"role": "tool", "tool_call_id": tc.get("id", "call_1"), "content": str(tool_result)})
                continue
            response = msg.get("content", "").strip()
            mem_writes = re.findall(r'\[MEM_WRITE:\s*(.*?)\s*=\s*(.*?)\s*\]', response)
            for k, v in mem_writes:
                mem_write(k.strip(), v.strip())
            response_clean = re.sub(r'\[MEM_WRITE:.*?\]', '', response).strip()
            return response_clean
        return "Lao phu dau dau!"

# ─── TRIGGER ENGINE ───────────────────────────────────────
class TriggerEngine:
    def __init__(self, agent: AgentSifu):
        self.agent = agent
        self.last_alert_time = {}
        self.last_30min_report = datetime.now() - timedelta(minutes=30)
        self.last_2h_encourage = datetime.now()
        self.last_idle_warning = {}

    def check_new_logs(self):
        with get_db() as conn:
            c = conn.cursor()
            for uname in ["bluebird", "partner"]:
                c.execute("SELECT status, window_title, timestamp FROM user_logs WHERE username=? ORDER BY timestamp DESC LIMIT 40", (uname,))
                logs = c.fetchall()
                if not logs:
                    continue
                dist_count = 0
                dist_titles = []
                for log in logs:
                    if log["status"] == "Distracted":
                        dist_count += 1
                        dist_titles.append(log["window_title"])
                    else:
                        break
                dist_minutes = dist_count * 15 / 60
                display = "Duy" if uname == "bluebird" else "Hung"
                key = f"dist_{uname}"
                if 5 <= dist_minutes < 30:
                    last = self.last_alert_time.get(key, 0)
                    if time.time() - last > 600:
                        self.last_alert_time[key] = time.time()
                        save_event("distraction", json.dumps({"username": uname, "display": display, "minutes": round(dist_minutes, 1), "titles": list(set(dist_titles[:3])), "level": "warning"}), priority=3)
                elif dist_minutes >= 30:
                    last = self.last_alert_time.get(key + "_hard", 0)
                    if time.time() - last > 300:
                        self.last_alert_time[key + "_hard"] = time.time()
                        save_event("distraction", json.dumps({"username": uname, "display": display, "minutes": round(dist_minutes, 1), "titles": list(set(dist_titles[:5])), "level": "hard"}), priority=5)
                try:
                    last_log_time = datetime.fromisoformat(logs[0]["timestamp"].replace("Z", ""))
                    idle_minutes = (datetime.now() - last_log_time).total_seconds() / 60
                    if idle_minutes > 60:
                        key = f"idle_{uname}"
                        last = self.last_idle_warning.get(key, 0)
                        if time.time() - last > 3600:
                            self.last_idle_warning[key] = time.time()
                            save_event("idle", json.dumps({"username": uname, "display": display, "minutes": round(idle_minutes)}), priority=4)
                except (ValueError, KeyError):
                    pass

    def check_30min_cycle(self):
        now = datetime.now()
        if (now - self.last_30min_report).total_seconds() < 1800:
            return
        self.last_30min_report = now
        lines = []
        for uname, dname in [("bluebird", "Duy"), ("partner", "Hung")]:
            with get_db() as conn:
                c = conn.cursor()
                today = now.date().isoformat()
                c.execute("SELECT status, COUNT(*) as cnt FROM user_logs WHERE username=? AND timestamp>=? GROUP BY status", (uname, today))
                stats = {r["status"]: r["cnt"] for r in c.fetchall()}
                c.execute("SELECT status, window_title FROM user_logs WHERE username=? ORDER BY timestamp DESC LIMIT 1", (uname,))
                current = c.fetchone()
            learn_h = round(stats.get("Learning", 0) * 15 / 3600, 2) if stats else 0
            dist = stats.get("Distracted", 0) if stats else 0
            current_str = f"\"{current['window_title'][:40]}\" - {current['status']}" if current else "Chua online"
            lines.append(f"• <b>{dname}</b>: {learn_h}h hoc, {dist} lan xao nhang | {current_str}")
        with get_db() as conn:
            c = conn.cursor()
            c.execute("SELECT value FROM system_config WHERE key='kpi_hours'")
            kpi = c.fetchone()
        kpi_str = f"{kpi['value']}h" if kpi else "2.0h"
        summary = f"🏔 <b>Diem danh 30 phut - Canh gioi: {kpi_str}</b>\n\n" + "\n".join(lines)
        save_event("cycle_30min", json.dumps({"type": "30min_check", "summary": summary}), priority=1)

    def check_2h_encourage(self):
        now = datetime.now()
        if (now - self.last_2h_encourage).total_seconds() < 7200:
            return
        self.last_2h_encourage = now
        for uname, dname in [("bluebird", "Duy"), ("partner", "Hung")]:
            with get_db() as conn:
                c = conn.cursor()
                today = now.date().isoformat()
                c.execute("SELECT COUNT(*) as cnt FROM user_logs WHERE username=? AND timestamp>=? AND status='Learning'", (uname, today))
                learn_count = c.fetchone()["cnt"]
            learn_h = round(learn_count * 15 / 3600, 2)
            if learn_h >= 3:
                save_event("milestone", json.dumps({"username": uname, "display": dname, "hours": learn_h, "type": "deep_focus"}), priority=2)

    def run_once(self):
        try:
            self.check_new_logs()
            self.check_30min_cycle()
            self.check_2h_encourage()
        except Exception as e:
            print(f"[Trigger Error] {e}")

# ─── MAKE GLOBALS (lazy) ──────────────────────────────────
_sifu_agent = None
_trigger_engine = None

def get_sifu_agent() -> AgentSifu:
    global _sifu_agent
    if _sifu_agent is None:
        _sifu_agent = AgentSifu()
    return _sifu_agent

def get_trigger_engine() -> TriggerEngine:
    global _trigger_engine
    if _trigger_engine is None:
        _trigger_engine = TriggerEngine(get_sifu_agent())
    return _trigger_engine

# ─── EVENT PROCESSOR ─────────────────────────────────────
def process_events():
    events = get_pending_events(5)
    agent = get_sifu_agent()
    for ev in events:
        try:
            edata = json.loads(ev["event_data"])
            etype = ev["event_type"]
            if etype == "distraction":
                d = edata
                level = d.get("level", "warning")
                if level == "hard":
                    prompt = f"Do de {d['display']} dang tau hoa nhap ma {d['minutes']}p voi {' , '.join(d.get('titles', ['ta giao']))}! Hay thinh_giao de mang no, giong kiem hiep, goi no luyen kiem ngay! De cap so phut cu the va cai no dang lam."
                else:
                    prompt = f"Do de {d['display']} dang lo la {d['minutes']}p vi may thu ta dao. Nhac nho bang thinh_giao, giong kiem hiep."
                agent.process(prompt)
                mem_write(f"{d['username']}_distraction_{datetime.now().strftime('%Y%m%d')}", f"Phat hien xao nhang {d['minutes']}p", "behavior", 0.7)
            elif etype == "idle":
                d = edata
                prompt = f"Do de {d['display']} bien mat {d['minutes']}p! thinh_giao de goi ve!"
                agent.process(prompt)
            elif etype == "cycle_30min":
                prompt = "30 phut. Kiem tra KPI bang kiem_tra_canh_gioi. Neu can, thinh_giao. Giong kiem hiep."
                agent.process(prompt)
            elif etype == "milestone":
                d = edata
                prompt = f"Do de {d['display']} tu luyen {d['hours']}h! thinh_giao de ghi nhan."
                agent.process(prompt)
                mem_write(f"{d['username']}_milestone_{datetime.now().strftime('%Y%m%d')}", f"Da tu luyen {d['hours']}h", "achievement", 0.9)
            mark_event_processed(ev["id"])
        except Exception as e:
            print(f"[Event Error] {e}")
            mark_event_processed(ev["id"])

# ─── TELEGRAM HANDLER ─────────────────────────────────────
def handle_telegram_message_v2(text: str, username: str, display_name: str, chat_id: str, message_id: int):
    mapped = "bluebird" if (username == "bluebird" or "duy" in display_name.lower()) else "partner"
    mapped_display = "Duy" if mapped == "bluebird" else "Hung"
    with get_db() as conn:
        conn.cursor().execute("INSERT INTO chat_history (chat_id, username, sender_name, message, is_ai) VALUES (?, ?, ?, ?, 0)", (str(chat_id), mapped, mapped_display, text))
        conn.commit()
    prompt = f"Do de {mapped_display} (@{mapped}) vua hoi: \"{text}\"\n\nTra cuu thong tin that roi tra loi."
    response = get_sifu_agent().process(prompt)
    if response and TELEGRAM_TOKEN:
        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
            requests.post(url, json={"chat_id": chat_id, "text": response, "reply_to_message_id": message_id, "parse_mode": "HTML"}, timeout=8)
        except Exception as e:
            print(f"[TG Reply Error] {e}")
    with get_db() as conn:
        conn.cursor().execute("INSERT INTO chat_history (chat_id, username, sender_name, message, is_ai) VALUES (?, ?, ?, ?, 1)", (str(chat_id), "sifu", "Doc Co Cau Bai", response))
        conn.commit()

# ─── WEEKLY REPORT ────────────────────────────────────────
LAST_REPORT_KEY = "last_weekly_report_week"

def generate_weekly_report():
    now = datetime.now()
    week_number = now.isocalendar()[1]
    week_start = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0).isoformat()
    lines = [f"📜 <b>BAO CAO TUAN {week_number}</b> 📜", f"{now.strftime('%d/%m/%Y')}\n"]
    with get_db() as conn:
        c = conn.cursor()
        for uname, dname in [("bluebird", "Duy"), ("partner", "Hung")]:
            c.execute("SELECT COUNT(*) as cnt FROM user_logs WHERE username=? AND timestamp>=? AND status='Learning'", (uname, week_start))
            learn_h = round(c.fetchone()["cnt"] * 15 / 3600, 2)
            c.execute("SELECT COUNT(*) as cnt FROM user_logs WHERE username=? AND timestamp>=? AND status='Distracted'", (uname, week_start))
            dist_count = c.fetchone()["cnt"]
            c.execute("SELECT DATE(timestamp) as day, COUNT(*) as cnt FROM user_logs WHERE username=? AND timestamp>=? AND status='Learning' GROUP BY DATE(timestamp) ORDER BY day", (uname, week_start))
            daily = c.fetchall()
            lines.append(f"\n☯ <b>{dname}</b>\n  • Tong: {learn_h}h\n  • Xao nhang: {dist_count}")
            if daily:
                days_str = ", ".join(f"{r['day'][-5:]}: {round(r['cnt']*15/3600,1)}h" for r in daily)
                lines.append(f"  • Ngay: {days_str}")
            mems = mem_search(f"{uname}_")
            if mems:
                lines.append(f"  • Ghi chep: {'; '.join(m['value'][:60] for m in mems[:3])}")
    report = "\n".join(lines)
    with get_db() as conn:
        conn.cursor().execute("INSERT INTO agent_reports (report_type, content, week_number) VALUES (?, ?, ?)", ("weekly", report, week_number))
        conn.cursor().execute("INSERT OR REPLACE INTO system_config (key, value) VALUES (?, ?)", (LAST_REPORT_KEY, str(week_number)))
        conn.commit()
    tg_send(report)

def check_weekly_report():
    try:
        now = datetime.now()
        week = now.isocalendar()[1]
        with get_db() as conn:
            c = conn.cursor()
            c.execute("SELECT value FROM system_config WHERE key=?", (LAST_REPORT_KEY,))
            row = c.fetchone()
            last_reported = int(row["value"]) if row else 0
        if now.weekday() == 6 and now.hour >= 20 and last_reported < week:
            generate_weekly_report()
    except Exception as e:
        print(f"[Weekly Report Error] {e}")

# ─── MAIN DAEMON ──────────────────────────────────────────
def agent_main_loop():
    init_agent_tables()
    offset = 0
    last_trigger_check = time.time()
    last_memory_maintenance = time.time()
    print("[Agent Sifu V2] Doc Co Cau Bai da thuc tinh!")
    try:
        tg_send("🧘 <b>Doc Co Cau Bai da thuc tinh!</b>\n\nLao phu se canh chung cac nghiep chuong khong cho chung tron tu luyen!")
    except Exception as e:
        print(f"[Agent TG Init] {e}")
    # Startup delay to avoid SQLite contention with FastAPI
    time.sleep(10)

    while True:
        try:
            msgs, offset = tg_poll_once(TELEGRAM_TOKEN, offset)
            for msg in msgs:
                chat_type = msg.get("chat", {}).get("type", "")
                text = msg.get("text", "")
                chat_id = str(msg["chat"]["id"])
                sender = msg.get("from", {})
                first_name = sender.get("first_name", "")
                username = sender.get("username", "").lower()
                is_bot = sender.get("is_bot", False)
                is_private = chat_type == "private"
                keywords = ["sifu", "su phu", "thay", "bot", "@olp_ai_bot", "thay", "su phu", "su phu oi", "thay oi"]
                is_mentioned = any(kw in text.lower() for kw in keywords)
                if (is_private or is_mentioned) and text and not is_bot:
                    handle_telegram_message_v2(text, username, first_name, chat_id, msg["message_id"])
            now = time.time()
            if now - last_trigger_check > 30:
                get_trigger_engine().run_once()
                last_trigger_check = now
            process_events()
            check_weekly_report()
            if now - last_memory_maintenance > 21600:
                deleted = mem_forget_old(30)
                print(f"[Agent Sifu] Da quen {deleted} ky uc cu.")
                last_memory_maintenance = now
            time.sleep(3)
        except Exception as e:
            print(f"[Agent Loop Error] {e}")
            time.sleep(10)
