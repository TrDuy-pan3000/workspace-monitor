import os
import re
import time
import json
import sqlite3
import requests
from datetime import datetime, timedelta
from typing import Optional
from fastapi import FastAPI, HTTPException, Header, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from dotenv import load_dotenv

# Load các biến môi trường từ .env
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

app = FastAPI(title="OLP AI Performance Tracker Backend")

# Đăng ký CORS để Frontend gọi API được thuận tiện
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Tạo thư mục static nếu chưa có để chứa ảnh live screen
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
os.makedirs(STATIC_DIR, exist_ok=True)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

DB_PATH = os.path.join(os.path.dirname(__file__), "database.db")

# Lưu vết thời gian gửi cảnh báo Telegram gần nhất của từng user để tránh spam liên tục
# key: username, value: timestamp
last_telegram_alert_time = {}

# --- HELPER FUNCTIONS FOR DATABASE ---

def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn

def get_config(key: str, default: str = "") -> str:
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM system_config WHERE key = ?", (key,))
        row = cursor.fetchone()
        return row["value"] if row else default

def set_config(key: str, value: str):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
        INSERT OR REPLACE INTO system_config (key, value, description)
        VALUES (?, ?, (SELECT description FROM system_config WHERE key = ?))
        """, (key, str(value), key))
        conn.commit()

# --- MODEL DEFINITIONS ---

class SingleLogItem(BaseModel):
    window_title: str
    is_idle: bool
    keystrokes: int
    clicks: int
    timestamp: str

class BatchLogPayload(BaseModel):
    username: str
    logs: list[SingleLogItem]

class AICommandPayload(BaseModel):
    command: str

# --- TELEGRAM BOT INTEGRATION ---

def send_telegram_message(message: str):
    token = get_config("telegram_token")
    chat_id = get_config("telegram_chat_id")
    
    # Fallback sang biến môi trường nếu DB chứa giá trị mặc định hoặc trống
    if not token or token == "YOUR_TELEGRAM_BOT_TOKEN":
        token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not chat_id or chat_id == "YOUR_TELEGRAM_CHAT_ID":
        chat_id = os.getenv("TELEGRAM_CHAT_ID")
        
    if not token or not chat_id:
        print("[Telegram] Chưa cấu hình Token hoặc Chat ID.")
        return
    
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat_id, "text": message}
    try:
        res = requests.post(url, json=payload, timeout=10)
        res.raise_for_status()
    except Exception as e:
        print(f"[Telegram] Lỗi khi gửi tin nhắn: {e}")

def send_telegram_photo(photo_path: str, caption: str):
    token = get_config("telegram_token")
    chat_id = get_config("telegram_chat_id")
    
    # Fallback sang biến môi trường nếu DB chứa giá trị mặc định hoặc trống
    if not token or token == "YOUR_TELEGRAM_BOT_TOKEN":
        token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not chat_id or chat_id == "YOUR_TELEGRAM_CHAT_ID":
        chat_id = os.getenv("TELEGRAM_CHAT_ID")
        
    if not token or not chat_id:
        print("[Telegram] Chưa cấu hình Token hoặc Chat ID.")
        return
    
    url = f"https://api.telegram.org/bot{token}/sendPhoto"
    if not os.path.exists(photo_path):
        send_telegram_message(caption)
        return
        
    try:
        with open(photo_path, "rb") as photo:
            files = {"photo": photo}
            payload = {"chat_id": chat_id, "caption": caption}
            res = requests.post(url, data=payload, files=files, timeout=15)
            res.raise_for_status()
    except Exception as e:
        print(f"[Telegram] Lỗi khi gửi ảnh: {e}")
        # Nếu gửi ảnh lỗi thì thử gửi tin nhắn chữ để dự phòng
        send_telegram_message(caption)

# --- TELEGRAM BOT CHAT & LONG POLLING ---

def telegram_polling_loop():
    # Chờ 5 giây khi startup để hệ thống ổn định
    time.sleep(5)
    
    token = get_config("telegram_token")
    if not token or token == "YOUR_TELEGRAM_BOT_TOKEN":
        token = os.getenv("TELEGRAM_BOT_TOKEN")
        
    if not token:
        print("[Telegram Polling] Chưa có token. Luồng polling tạm dừng.")
        return
        
    print("[Telegram Polling] Đang khởi chạy luồng quét tin nhắn Telegram...")
    offset = 0
    
    # Bỏ qua các tin nhắn cũ
    try:
        url = f"https://api.telegram.org/bot{token}/getUpdates"
        res = requests.get(url, params={"offset": -1, "timeout": 1}, timeout=5)
        if res.status_code == 200:
            updates = res.json().get("result", [])
            if updates:
                offset = updates[-1]["update_id"] + 1
    except Exception as e:
        print(f"[Telegram Polling] Lỗi khởi tạo offset: {e}")
        
    while True:
        try:
            current_token = get_config("telegram_token")
            if not current_token or current_token == "YOUR_TELEGRAM_BOT_TOKEN":
                current_token = os.getenv("TELEGRAM_BOT_TOKEN")
                
            if not current_token:
                time.sleep(5)
                continue
                
            url = f"https://api.telegram.org/bot{current_token}/getUpdates"
            res = requests.get(url, params={"offset": offset, "timeout": 10}, timeout=15)
            if res.status_code != 200:
                time.sleep(5)
                continue
                
            updates = res.json().get("result", [])
            for update in updates:
                offset = update["update_id"] + 1
                message = update.get("message")
                if not message:
                    continue
                    
                chat_id = message["chat"]["id"]
                text = message.get("text")
                if not text:
                    continue
                    
                handle_telegram_incoming_message(current_token, chat_id, text, message)
        except Exception as e:
            print(f"[Telegram Polling] Lỗi trong vòng lặp polling: {e}")
            time.sleep(5)

def db_add_chat_log(chat_id: str, username: str, sender_name: str, message: str, is_ai: int = 0):
    """Lưu lịch sử trò chuyện vào database."""
    try:
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("""
            INSERT INTO chat_history (chat_id, username, sender_name, message, is_ai)
            VALUES (?, ?, ?, ?, ?)
            """, (str(chat_id), username, sender_name, message, is_ai))
            conn.commit()
    except Exception as e:
        print(f"[Database Error] Lỗi khi lưu lịch sử chat: {e}")

def db_write_ai_memory(key: str, value: str):
    """Ghi bộ nhớ dài hạn của AI vào SQLite."""
    try:
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("""
            INSERT OR REPLACE INTO ai_memories (key, value, updated_at)
            VALUES (?, ?, CURRENT_TIMESTAMP)
            """, (key.strip(), value.strip()))
            conn.commit()
    except Exception as e:
        print(f"[Database Error] Lỗi khi lưu bộ nhớ AI: {e}")

def db_get_all_ai_memories() -> str:
    """Lấy tất cả ghi nhớ dài hạn dưới dạng text."""
    try:
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT key, value, updated_at FROM ai_memories")
            rows = cursor.fetchall()
        mem_str = ""
        for r in rows:
            mem_str += f"- {r['key']}: {r['value']} (cập nhật lúc {r['updated_at']})\n"
        return mem_str if mem_str else "Chưa ghi nhớ thông tin nào."
    except Exception as e:
        print(f"[Database Error] Lỗi khi đọc bộ nhớ AI: {e}")
        return "Lỗi đọc bộ nhớ."

def build_ai_context(target_username: str = None) -> str:
    """RAG Engine: tổng hợp stats học tập hôm nay, cấu hình, giáo án và bộ nhớ dài hạn."""
    try:
        kpi = get_config("kpi_hours", "2.0")
        dist_min = get_config("allowed_distraction", "15")
        
        today = datetime.now().date()
        today_start = datetime(today.year, today.month, today.day).isoformat()
        
        stats_str = ""
        usernames = []
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT DISTINCT username FROM user_logs")
            usernames = [row["username"] for row in cursor.fetchall()]
            
        if not usernames:
            usernames = ["bluebird", "partner"]
            
        for u in usernames:
            display_name = "Duy" if u == "bluebird" else "Hưng"
            with get_db() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                SELECT status, window_title, efficiency, timestamp 
                FROM user_logs 
                WHERE username = ? AND timestamp >= ? 
                ORDER BY timestamp DESC
                """, (u, today_start))
                logs = cursor.fetchall()
                
            if logs:
                current_status = logs[0]["status"]
                current_title = logs[0]["window_title"]
                
                # Tính giờ tập trung: client mới gửi log mỗi 15 giây (1 log = 15 giây)
                learning_count = sum(1 for log in logs if log["status"] == "Learning")
                learning_hours = round((learning_count * 15) / 3600, 2)
                
                distracted_logs = [log["window_title"] for log in logs if log["status"] == "Distracted"]
                unique_distractions = list(set(distracted_logs))[:5]
                
                stats_str += (
                    f"Học viên '{display_name}' (@{u}):\n"
                    f"  + Trạng thái hiện tại: {current_status}\n"
                    f"  + Cửa sổ hoạt động: \"{current_title}\"\n"
                    f"  + Thời gian học tập hôm nay: {learning_hours} giờ (KPI: {kpi} giờ)\n"
                )
                if unique_distractions:
                    stats_str += f"  + Ứng dụng xao nhãng hôm nay: {', '.join(unique_distractions)}\n"
            else:
                stats_str += f"Học viên '{display_name}' (@{u}):\n  + Trạng thái hiện tại: Offline (Chưa bật máy học hôm nay)\n"
        
        # Đọc giáo án tuần mới nhất
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT week_number, topic, tasks FROM study_plan ORDER BY week_number DESC LIMIT 1")
            plan_row = cursor.fetchone()
        
        plan_str = "Chưa thiết lập giáo án."
        if plan_row:
            plan_str = f"Tuần {plan_row['week_number']}: {plan_row['topic']}\nNhiệm vụ:\n{plan_row['tasks']}"
            
        memories_str = db_get_all_ai_memories()
        
        context = (
            f"=== THÔNG TIN HỆ THỐNG THỜI GIAN THỰC ===\n"
            f"KPI học tập bắt buộc: {kpi} giờ/ngày\n"
            f"Giới hạn thời gian giải trí liên tục: {dist_min} phút\n\n"
            f"=== TIẾN ĐỘ HỌC TẬP HÔM NAY ===\n"
            f"{stats_str}\n"
            f"=== GIÁO ÁN HIỆN TẠI ===\n"
            f"{plan_str}\n\n"
            f"=== BỘ NHỚ DÀI HẠN CỦA SƯ PHỤ (Ghi chép hành vi học viên) ===\n"
            f"{memories_str}\n"
        )
        return context
    except Exception as e:
        print(f"[RAG Error] Lỗi build context: {e}")
        return "Không thể tải bối cảnh hệ thống."

def handle_telegram_incoming_message(token: str, chat_id: int, text: str, message: dict):
    sender = message.get("from", {})
    first_name = sender.get("first_name", "Học viên")
    username = sender.get("username", "").lower()
    
    is_private = message["chat"]["type"] == "private"
    is_mentioned = False
    
    lowered_text = text.lower()
    if not is_private:
        # Nếu là nhóm chat, chỉ trả lời khi chứa từ khóa liên quan đến Sư phụ
        keywords = ["sifu", "sư phụ", "thầy", "bot", "@olp_ai_bot", "thay", "su phu"]
        is_mentioned = any(kw in lowered_text for kw in keywords)
        
    if not is_private and not is_mentioned:
        return
        
    print(f"[Telegram Bot] Nhận tin nhắn từ {first_name} (@{username}): {text}")
    
    # Ánh xạ tên hiển thị: bluebird -> Duy, các tên khác -> Hưng
    mapped_display_name = "Duy" if username == "bluebird" or "duy" in first_name.lower() else "Hưng"
    mapped_username = "bluebird" if mapped_display_name == "Duy" else "partner"
    
    # Lưu lịch sử chat của user vào database trước
    db_add_chat_log(str(chat_id), mapped_username, mapped_display_name, text, is_ai=0)
    
    # Xây dựng context RAG và system prompt
    ai_context = build_ai_context(mapped_username)
    
    system_prompt = (
        "Bạn là Sư phụ (Sifu) - một huyền thoại lập trình thi học sinh giỏi lập dị, nghiêm khắc và cộc cằn. "
        "Nhiệm vụ của bạn là giám sát, giáo huấn và thúc giục hai học viên Duy (bluebird) và Hưng ôn thi. "
        "Hãy trả lời tin nhắn của học viên bằng giọng điệu lạnh lùng, thâm thúy, châm biếm, cộc cằn nhưng vô cùng yêu thương "
        "và mong muốn họ tiến bộ. Hãy mắng mỏ họ nếu họ lười biếng, đòi nghỉ ngơi, treo máy hay lướt web giải trí. "
        "Hãy trả lời ngắn gọn (tối đa 3-4 câu), không dùng icon hoa mỹ (có thể dùng icon cộc cằn 😠, 😤, 💻).\n\n"
        "=== BỘ NHỚ & TIẾN ĐỘ THỜI GIAN THỰC ===\n"
        f"{ai_context}\n\n"
        "=== HƯỚNG DẪN GHI NHỚ DÀI HẠN ===\n"
        "Nếu ngươi muốn ghi nhớ điều gì dài hạn về học viên (như tính cách, sự kiện, lời hứa, thói quen học tập mới phát hiện), "
        "hãy ghi thêm một hoặc nhiều tag dạng '[MEM_WRITE: key = value]' ở cuối câu trả lời của ngươi. "
        "Ví dụ: '[MEM_WRITE: duy_streak = lười biếng, trốn học hôm nay]'. "
        "Hệ thống sẽ tự động cập nhật tag này vào bộ nhớ của ngươi để dùng ở các cuộc trò chuyện sau. "
        "Chú ý: các tag này phải được đặt ở cuối cùng của tin nhắn."
    )
    
    # Lấy lịch sử chat ngắn hạn định dạng cho LLM
    api_base = os.getenv("OPENAI_API_BASE", "http://localhost:20128/v1").rstrip("/")
    nine_router_url = os.getenv("NINE_ROUTER_URL", f"{api_base}/chat/completions")
    api_key = os.getenv("NINE_ROUTER_API_KEY", os.getenv("OPENAI_API_KEY", "free_key_placeholder"))
    model_name = os.getenv("MODEL_NAME", "deepseek-v4-flash")
    
    messages = [{"role": "system", "content": system_prompt}]
    
    # Lấy 10 tin nhắn lịch sử gần đây nhất của chat_id này
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
        SELECT sender_name, message, is_ai FROM chat_history
        WHERE chat_id = ?
        ORDER BY timestamp DESC LIMIT 10
        """, (str(chat_id),))
        history_rows = cursor.fetchall()
        
    history_rows.reverse()
    for row in history_rows:
        role = "assistant" if row["is_ai"] == 1 else "user"
        content = row["message"] if role == "assistant" else f"{row['sender_name']}: {row['message']}"
        messages.append({"role": role, "content": content})
        
    reply_text = "Sư phụ đang bận code compiler..."
    try:
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
        payload = {
            "model": model_name,
            "messages": messages,
            "temperature": 0.8
        }
        res = requests.post(nine_router_url, json=payload, headers=headers, timeout=25)
        res.encoding = 'utf-8'
        if res.status_code == 200:
            text_clean = res.text.strip()
            if text_clean.endswith("data: [DONE]"):
                text_clean = text_clean[:-12].strip()
            reply_raw = json.loads(text_clean)["choices"][0]["message"]["content"]
            
            # Parse các tag ghi nhớ dạng [MEM_WRITE: key = value]
            mem_writes = re.findall(r'\[MEM_WRITE:\s*(.*?)\s*=\s*(.*?)\s*\]', reply_raw)
            for k, val in mem_writes:
                db_write_ai_memory(k, val)
                
            # Làm sạch reply_text trước khi gửi đi
            reply_text = re.sub(r'\[MEM_WRITE:.*?\]', '', reply_raw).strip()
            
            # Lưu câu trả lời của AI vào database
            db_add_chat_log(str(chat_id), "sifu", "Sư phụ AI", reply_text, is_ai=1)
        else:
            reply_text = f"Sư phụ đang bận viết compiler, cút đi học bài đi! (Lỗi: {res.status_code})"
    except Exception as e:
        print(f"[Telegram Bot LLM Error]: {e}")
        reply_text = f"Sư phụ đang bế quan luyện kiếm, chớ làm phiền! (Lỗi kết nối: {str(e)})"
        
    send_url = f"https://api.telegram.org/bot{token}/sendMessage"
    send_payload = {
        "chat_id": chat_id,
        "text": reply_text,
        "reply_to_message_id": message["message_id"]
    }
    try:
        requests.post(send_url, json=send_payload, timeout=10)
    except Exception as e:
        print(f"[Telegram Bot] Lỗi gửi phản hồi: {e}")

def proactive_alert_daemon():
    """Luồng chạy ngầm định kỳ 60 phút — Phân tích tiến độ và chủ động gửi cảnh báo / khen thưởng lên Telegram."""
    # Nghỉ 15 phút sau khởi động để hệ thống ổn định trước khi chạy lần đầu
    time.sleep(900)
    
    while True:
        try:
            print("[Proactive Daemon] Đang phân tích tiến độ và chuẩn bị tin nhắn định kỳ...")
            
            ai_context = build_ai_context()
            
            api_base = os.getenv("OPENAI_API_BASE", "http://localhost:20128/v1").rstrip("/")
            nine_router_url = os.getenv("NINE_ROUTER_URL", f"{api_base}/chat/completions")
            api_key = os.getenv("NINE_ROUTER_API_KEY", os.getenv("OPENAI_API_KEY", "free_key_placeholder"))
            model_name = os.getenv("MODEL_NAME", "deepseek-v4-flash")
            
            system_prompt = (
                "Bạn là Sư phụ (Sifu) - một huyền thoại lập trình thi học sinh giỏi lập dị, nghiêm khắc và cộc cằn. "
                "Dựa vào bảng tiến độ học tập thực tế bên dưới, hãy chủ động gửi một tin nhắn kiểm tra, "
                "giáo huấn, hoặc thúc giục hai học viên Duy và Hưng vào nhóm chat. "
                "Tin nhắn phải:\n"
                "1. Đề cập đến dữ liệu thực tế cụ thể (số giờ học, tab giải trí, trạng thái hiện tại).\n"
                "2. Dùng giọng điệu lạnh lùng, cộc cằn, thâm thúy, mang tính giáo huấn nhưng không ác ý.\n"
                "3. Ngắn gọn (2-4 câu), không dùng icon hoa mỹ, xưng 'Ta' hoặc 'Sư phụ'.\n"
                "4. Nếu một trong hai đang học tốt — ghi nhận bằng một câu khen lạnh lùng.\n"
                "5. Nếu cả hai offline hoặc KPI thấp — đưa ra lời nhắc nhở thẳng thắn.\n"
            )
            
            ai_memories = db_get_all_ai_memories()
            user_prompt = (
                f"Đây là tiến độ học tập hiện tại:\n{ai_context}\n"
                f"Và đây là các ghi chú trí nhớ dài hạn của ta:\n{ai_memories}\n\n"
                "Hãy viết tin nhắn giáo huấn định kỳ để gửi lên nhóm ngay bây giờ:"
            )
            
            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json"
            }
            payload = {
                "model": model_name,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                "temperature": 0.9
            }
            res = requests.post(nine_router_url, json=payload, headers=headers, timeout=30)
            res.encoding = 'utf-8'
            
            if res.status_code == 200:
                text_clean = res.text.strip()
                if text_clean.endswith("data: [DONE]"):
                    text_clean = text_clean[:-12].strip()
                message_text = json.loads(text_clean)["choices"][0]["message"]["content"]
                
                # Parse MEM_WRITE nếu có
                mem_writes = re.findall(r'\[MEM_WRITE:\s*(.*?)\s*=\s*(.*?)\s*\]', message_text)
                for k, val in mem_writes:
                    db_write_ai_memory(k, val)
                message_clean = re.sub(r'\[MEM_WRITE:.*?\]', '', message_text).strip()
                
                # Gửi lên Telegram với prefix rõ ràng
                full_message = f"📊 [Điểm danh định kỳ của Sư phụ]\n\n{message_clean}"
                send_telegram_message(full_message)
                print(f"[Proactive Daemon] Đã gửi tin nhắn định kỳ lên Telegram.")
            else:
                print(f"[Proactive Daemon] LLM phản hồi lỗi: {res.status_code}")
                
        except Exception as e:
            print(f"[Proactive Daemon] Lỗi: {e}")
        
        # Chờ 60 phút rồi chạy tiếp
        time.sleep(3600)

@app.on_event("startup")
def startup_event():
    import threading

    # Telegram polling
    threading.Thread(target=telegram_polling_loop, daemon=True).start()

    # Proactive alert daemon
    threading.Thread(target=proactive_alert_daemon, daemon=True).start()

    # Agent Sifu V2 - start daemon thread with safe import
    try:
        from agent_sifu_v2 import agent_main_loop

        def _safe_agent():
            try:
                agent_main_loop()
            except Exception as ex:
                print(f"[Agent Sifu] Thread exited: {ex}")

        t = threading.Thread(target=_safe_agent, daemon=True)
        t.start()
        print("[Startup] Agent Sifu V2 daemon thread started.")
    except Exception as excep:
        print(f"[Startup] Cannot start Agent Sifu V2: {excep}")

    print("[Startup] Telegram polling thread va Proactive Alert Daemon da khoi dong.")


# --- HYBRID CLASSIFICATION ENGINE ---

def classify_window_title(window_title: str) -> tuple[str, int]:
    """Phân loại tiêu đề cửa sổ trả về: (status, efficiency)"""
    title_clean = window_title.strip()
    if not title_clean:
        return "Idle", 0

    # 1. Regex cứng phân loại nhanh
    learning_keywords = [
        r"vs\s*code", r"visual\s*studio", r"kaggle", r"jupyter", r"colab", r"github", 
        r"stackoverflow", r"python", r"train\.py", r"test\.py", r"rstudio", r"anaconda",
        r"gemini", r"chatgpt", r"claude", r"deepseek", r"arxiv", r"overleaf", r"latex",
        r"chuyên tin", r"olp", r"tin học", r"thuật toán", r"hackerrank", r"leetcode", r"codeforces"
    ]
    distracted_keywords = [
        r"facebook", r"youtube", r"netflix", r"tiktok", r"shopee", r"lazada", r"tiki",
        r"reddit", r"twitter", r"instagram", r"discord", r"spotify", r"game", r"dota",
        r"league of legends", r"phim", r"truyện", r"manga", r"tin tức", r"dân trí", r"vnexpress"
    ]

    for pattern in learning_keywords:
        if re.search(pattern, title_clean, re.IGNORECASE):
            return "Learning", 100

    for pattern in distracted_keywords:
        if re.search(pattern, title_clean, re.IGNORECASE):
            return "Distracted", 10

    # 2. Tra cứu trong Cache SQLite
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT status, efficiency FROM title_classification_cache WHERE window_title = ?", (title_clean,))
        row = cursor.fetchone()
        if row:
            return row["status"], row["efficiency"]

    # 3. Gọi AI qua 9Router làm phương án cuối cùng
    api_base = os.getenv("OPENAI_API_BASE", "http://localhost:20128/v1").rstrip("/")
    nine_router_url = os.getenv("NINE_ROUTER_URL", f"{api_base}/chat/completions")
    api_key = os.getenv("NINE_ROUTER_API_KEY", os.getenv("OPENAI_API_KEY", "free_key_placeholder"))
    model_name = os.getenv("MODEL_NAME", "deepseek-v4-flash")

    prompt = (
        "Hãy phân loại tiêu đề cửa sổ hoạt động của máy tính sau đây thành 'Learning' "
        "(đang học tập, lập trình, nghiên cứu, đọc tài liệu khoa học, dùng AI hỗ trợ) "
        "hoặc 'Distracted' (đang giải trí, xem video lướt web vô bổ, mạng xã hội, mua sắm, chơi game).\n"
        "Định dạng trả về duy nhất là chuỗi JSON: {\"status\": \"Learning\"|\"Distracted\", \"efficiency\": 0-100}.\n"
        "Vui lòng không giải thích gì thêm, chỉ trả về chuỗi JSON thô.\n"
        f"Tiêu đề cửa sổ: \"{title_clean}\""
    )

    try:
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
        data = {
            "model": model_name,
            "messages": [
                {"role": "user", "content": prompt}
            ],
            "temperature": 0.0,
            "response_format": {"type": "json_object"}
        }
        res = requests.post(nine_router_url, headers=headers, json=data, timeout=12)
        res.raise_for_status()
        res.encoding = 'utf-8'
        
        # Làm sạch response text phòng trường hợp 9Router đính kèm 'data: [DONE]' ở cuối
        text_clean = res.text.strip()
        if text_clean.endswith("data: [DONE]"):
            text_clean = text_clean[:-12].strip()
            
        content = json.loads(text_clean)["choices"][0]["message"]["content"]
        
        # Parse JSON
        result = json.loads(content)
        status = result.get("status", "Learning")
        efficiency = int(result.get("efficiency", 80))

        # Lưu lại vào cache
        with get_db() as conn:
            conn.cursor().execute("""
            INSERT OR REPLACE INTO title_classification_cache (window_title, status, efficiency)
            VALUES (?, ?, ?)
            """, (title_clean, status, efficiency))
            conn.commit()

        return status, efficiency

    except Exception as e:
        print(f"[AI Classification Error]: {e}. Gán nhãn mặc định: Learning")
        return "Learning", 70  # Dự phòng nếu AI lỗi

# --- CORE API ENDPOINTS ---

@app.post("/api/v1/log")
def log_client_data(payload: BatchLogPayload, x_api_key: Optional[str] = Header(None)):
    # Xác thực API Key
    stored_key = get_config("api_key", "default_olp_key_2026")
    if not x_api_key or x_api_key != stored_key:
        raise HTTPException(status_code=401, detail="API Key không hợp lệ hoặc bị thiếu.")

    username = payload.username.lower()
    
    # Biến kiểm tra xem có bất kỳ log nào trong lô bị phân loại là 'Distracted' không
    any_distracted = False
    last_status = "Learning"
    last_efficiency = 100

    # Mở kết nối DB một lần cho cả lô để tăng hiệu năng ghi dữ liệu
    with get_db() as conn:
        cursor = conn.cursor()
        for log in payload.logs:
            # 1. Xử lý trạng thái Idle
            if log.is_idle:
                status = "Idle"
                efficiency = 0
            else:
                status, efficiency = classify_window_title(log.window_title)
                
                # 2. Thuật toán tự phạt treo máy (Fake Learning)
                # Nếu cửa sổ thuộc nhóm học tập nhưng hoạt động gõ phím + click chuột quá thấp (< 10 lần)
                if status == "Learning" and (log.keystrokes + log.clicks < 10):
                    status = "Idle"
                    efficiency = 0

            if status == "Distracted":
                any_distracted = True
            
            last_status = status
            last_efficiency = efficiency

            # Ghi nhận log vào SQLite (thêm cột keystrokes, clicks)
            cursor.execute("""
            INSERT INTO user_logs (username, timestamp, window_title, status, efficiency, keystrokes, clicks)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (username, log.timestamp, log.window_title, status, efficiency, log.keystrokes, log.clicks))
        
        conn.commit()

    # --- KIỂM TRA ĐỂ GỬI CẢNH BÁO TELEGRAM (RĂN ĐE) ---
    allowed_distraction_min = int(get_config("allowed_distraction", "15"))
    
    if any_distracted:
        # Truy vấn lịch sử logs gần nhất để kiểm tra xem đã xao nhãng liên tục chưa
        time_limit = datetime.now() - timedelta(minutes=allowed_distraction_min)
        time_limit_str = time_limit.isoformat()

        with get_db() as conn:
            cursor = conn.cursor()
            # Lấy toàn bộ logs của user trong allowed_distraction_min phút qua
            cursor.execute("""
            SELECT status FROM user_logs 
            WHERE username = ? AND timestamp >= ? 
            ORDER BY timestamp DESC
            """, (username, time_limit_str))
            recent_logs = [row["status"] for row in cursor.fetchall()]

        # Kiểm tra nếu tất cả logs trong khoảng thời gian này đều là 'Distracted'
        # và có tối thiểu vài logs (để tránh vừa bật máy đã phạt)
        is_all_distracted = len(recent_logs) >= 3 and all(s == "Distracted" for s in recent_logs)

        if is_all_distracted:
            # Check xem đã gửi cảnh báo trong 15 phút vừa qua chưa (tránh spam)
            now = time.time()
            last_alert = last_telegram_alert_time.get(username, 0)
            if now - last_alert > 900: # 15 phút = 900 giây
                last_telegram_alert_time[username] = now
                
                # Ánh xạ tên hiển thị trên Telegram: bluebird -> Duy, các user khác -> Hưng
                user_display = "Duy" if username == "bluebird" else "Hưng"
                teammate = "Hưng" if username == "bluebird" else "Duy"
                
                caption = (
                    f"🚨 CẢNH BÁO LƯỜI BIẾNG: {user_display} đã lướt web giải trí liên tục {allowed_distraction_min} phút "
                    f"trong giờ học rồi! @{teammate} vào nhắc nhở đồng đội tập trung lại đi! 😡🔥"
                )
                
                # Nếu có ảnh chụp màn hình live mới nhất, gửi kèm ảnh
                photo_path = os.path.join(STATIC_DIR, f"latest_{username}.jpg")
                send_telegram_photo(photo_path, caption)

    # Nếu có log bị Distracted trong lô log, trả về status Distracted để client chụp ảnh màn hình
    final_status = "Distracted" if any_distracted else last_status
    return {"success": True, "status": final_status, "efficiency": last_efficiency, "message": f"Logged {len(payload.logs)} items."}

@app.post("/api/v1/live-screen")
def upload_live_screen(
    username: str = Form(...),
    file: UploadFile = File(...),
    x_api_key: Optional[str] = Header(None)
):
    stored_key = get_config("api_key", "default_olp_key_2026")
    if not x_api_key or x_api_key != stored_key:
        raise HTTPException(status_code=401, detail="API Key không hợp lệ.")

    username = username.lower()
    # Lưu file đè lên ảnh mới nhất của user
    file_path = os.path.join(STATIC_DIR, f"latest_{username}.jpg")
    try:
        contents = file.file.read()
        with open(file_path, "wb") as f:
            f.write(contents)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Không thể lưu file ảnh: {e}")

    return {"success": True, "file_url": f"/static/latest_{username}.jpg"}

@app.get("/api/v1/dashboard/stats")
def get_dashboard_stats():
    today = datetime.now().date()
    today_start = datetime(today.year, today.month, today.day).isoformat()

    stats = {}
    usernames = []

    # Lấy danh sách các username đã từng gửi log
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT DISTINCT username FROM user_logs")
        usernames = [row["username"] for row in cursor.fetchall()]

    # Nếu DB chưa có log nào, khởi tạo mặc định cho Duy và Đồng Đội
    if not usernames:
        usernames = ["duy", "partner"]

    kpi_hours = float(get_config("kpi_hours", "2.0"))

    for user in usernames:
        with get_db() as conn:
            cursor = conn.cursor()
            # Lấy tất cả logs hôm nay của user
            cursor.execute("""
            SELECT status, window_title, efficiency, timestamp 
            FROM user_logs 
            WHERE username = ? AND timestamp >= ? 
            ORDER BY timestamp DESC
            """, (user, today_start))
            logs = cursor.fetchall()

        if logs:
            current_status = logs[0]["status"]
            current_title = logs[0]["window_title"]
            current_efficiency = logs[0]["efficiency"]

            # Tính tổng giờ hoạt động hôm nay.
            # Vì client gửi log 2 phút một lần, mỗi log đại diện cho 2 phút hoạt động (120 giây).
            # Chỉ tính thời gian 'Learning' là thời gian code hiệu quả.
            learning_logs_count = sum(1 for log in logs if log["status"] == "Learning")
            learning_hours = round((learning_logs_count * 2) / 60, 2)
        else:
            current_status = "Offline"
            current_title = "Không hoạt động"
            current_efficiency = 0
            learning_hours = 0.0

        kpi_percent = min(100.0, round((learning_hours / kpi_hours) * 100, 1)) if kpi_hours > 0 else 100.0

        stats[user] = {
            "username": user,
            "status": current_status,
            "current_title": current_title,
            "efficiency": current_efficiency,
            "learning_hours": learning_hours,
            "kpi_percent": kpi_percent,
            "live_image": f"/static/latest_{user}.jpg" if os.path.exists(os.path.join(STATIC_DIR, f"latest_{user}.jpg")) else None
        }

    # Tính Streak (Số ngày liên tục cả 2 hoặc ít nhất có hoạt động hoàn thành KPI)
    # Ở đây chúng ta sẽ tính đơn giản: số ngày liên tiếp ngược từ hôm qua trở về trước 
    # mà có ít nhất một user đạt KPI, hoặc cả hai đạt KPI.
    streak = calculate_streak(usernames, kpi_hours)

    return {
        "users": stats,
        "streak": streak,
        "kpi_hours": kpi_hours,
        "allowed_distraction": int(get_config("allowed_distraction", "15"))
    }

def calculate_streak(usernames: list[str], kpi_hours: float) -> int:
    """Tính chuỗi ngày liên tục đạt KPI giờ học tập của cả team."""
    if not usernames:
        return 0

    streak = 0
    current_check_date = datetime.now().date()

    while True:
        # Kiểm tra xem ngày 'current_check_date' có đạt KPI không
        day_start = datetime(current_check_date.year, current_check_date.month, current_check_date.day).isoformat()
        day_end = (datetime(current_check_date.year, current_check_date.month, current_check_date.day) + timedelta(days=1)).isoformat()

        user_kpis_met = 0
        for user in usernames:
            with get_db() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                SELECT COUNT(*) as cnt FROM user_logs 
                WHERE username = ? AND timestamp >= ? AND timestamp < ? AND status = 'Learning'
                """, (user, day_start, day_end))
                row = cursor.fetchone()
                cnt = row["cnt"] if row else 0
                hours = (cnt * 2) / 60
                if hours >= kpi_hours:
                    user_kpis_met += 1

        # Nếu cả 2 đều đạt KPI (hoặc nếu nhóm chỉ có 1 người hoạt động thì cần 1 người đạt)
        required_met = max(1, len(usernames))
        if user_kpis_met >= required_met:
            streak += 1
            current_check_date -= timedelta(days=1)
        else:
            # Nếu ngày hôm nay chưa hoàn thành KPI thì có thể tiếp tục tính từ ngày hôm qua trở về trước
            if current_check_date == datetime.now().date():
                current_check_date -= timedelta(days=1)
                continue
            else:
                break

    return streak

@app.get("/api/v1/dashboard/chart")
def get_dashboard_chart(username: str):
    username = username.lower()
    today = datetime.now().date()
    today_start = datetime(today.year, today.month, today.day).isoformat()

    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
        SELECT status, efficiency, timestamp FROM user_logs
        WHERE username = ? AND timestamp >= ?
        ORDER BY timestamp ASC
        """, (username, today_start))
        logs = cursor.fetchall()

    # 1. Chuẩn bị dữ liệu cho Pie Chart (Phân bổ trạng thái)
    pie_data = {"Learning": 0, "Distracted": 0, "Idle": 0}
    for log in logs:
        status = log["status"]
        if status in pie_data:
            pie_data[status] += 2 # Mỗi log tương ứng 2 phút

    # 2. Chuẩn bị dữ liệu cho Line Chart (Biểu đồ hiệu suất theo 24 giờ)
    line_data = [0] * 24
    hour_counts = [0] * 24

    for log in logs:
        try:
            # Parse ISO timestamp
            dt = datetime.fromisoformat(log["timestamp"])
            hour = dt.hour
            line_data[hour] += log["efficiency"]
            hour_counts[hour] += 1
        except Exception:
            continue

    # Tính điểm hiệu suất trung bình cho từng giờ
    avg_line_data = []
    for h in range(24):
        if hour_counts[h] > 0:
            avg_line_data.append(round(line_data[h] / hour_counts[h], 1))
        else:
            avg_line_data.append(0)

    return {
        "pie": pie_data,
        "line": avg_line_data
    }

@app.get("/api/v1/study-plan")
def get_study_plan():
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT week_number, topic, tasks FROM study_plan ORDER BY week_number DESC")
        rows = cursor.fetchall()
        
    return [
        {
            "week_number": row["week_number"],
            "topic": row["topic"],
            "tasks": row["tasks"]
        }
        for row in rows
    ]

# --- AI FUNCTION CALLING IMPLEMENTATION ---

def db_update_kpi(kpi_hours: float) -> str:
    set_config("kpi_hours", str(kpi_hours))
    return f"Đã cập nhật KPI học tập tối thiểu thành {kpi_hours} giờ mỗi ngày."

def db_update_allowed_distraction(minutes: int) -> str:
    set_config("allowed_distraction", str(minutes))
    return f"Đã cập nhật thời gian giải trí tối đa cho phép thành {minutes} phút."

def db_update_study_plan(week_number: int, topic: str, tasks: str) -> str:
    with get_db() as conn:
        conn.cursor().execute("""
        INSERT OR REPLACE INTO study_plan (week_number, topic, tasks)
        VALUES (?, ?, ?)
        """, (week_number, topic, tasks))
        conn.commit()
    return f"Đã cập nhật giáo án Tuần {week_number} với chủ đề: '{topic}'."

def get_system_status() -> str:
    kpi = get_config("kpi_hours", "2.0")
    dist = get_config("allowed_distraction", "15")
    tg_token = get_config("telegram_token", "")
    tg_chat = get_config("telegram_chat_id", "")
    
    status_msg = (
        f"Cấu hình hệ thống hiện tại:\n"
        f"- KPI giờ học: {kpi} giờ/ngày\n"
        f"- Thời gian giải trí tối đa: {dist} phút\n"
        f"- Telegram Bot Token: {'Đã cấu hình' if tg_token and tg_token != 'YOUR_TELEGRAM_BOT_TOKEN' else 'Chưa cấu hình'}\n"
        f"- Telegram Chat ID: {'Đã cấu hình' if tg_chat and tg_chat != 'YOUR_TELEGRAM_CHAT_ID' else 'Chưa cấu hình'}"
    )
    return status_msg

def format_minutes_to_hours(minutes: int) -> str:
    h = minutes // 60
    m = minutes % 60
    if h > 0:
        return f"{h}h {m}p"
    return f"{m} phút"

def db_get_user_activity_report(username: str, hours: float = 1.0) -> str:
    """Lấy báo cáo hoạt động chi tiết của một người dùng trong số giờ gần nhất."""
    username = username.lower().strip()
    time_limit = datetime.now() - timedelta(hours=hours)
    time_limit_str = time_limit.isoformat()
    
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
        SELECT timestamp, window_title, status, efficiency 
        FROM user_logs 
        WHERE username = ? AND timestamp >= ? 
        ORDER BY timestamp ASC
        """, (username, time_limit_str))
        logs = cursor.fetchall()
        
    if not logs:
        return f"Không tìm thấy hoạt động nào của '{username}' trong {hours} giờ qua."
        
    total_logs = len(logs)
    learning_logs = sum(1 for r in logs if r["status"] == "Learning")
    distracted_logs = sum(1 for r in logs if r["status"] == "Distracted")
    idle_logs = sum(1 for r in logs if r["status"] == "Idle")
    
    # Client quét mỗi 15 giây => 1 log = 15 giây = 0.25 phút
    learning_min = int(learning_logs * 0.25)
    distracted_min = int(distracted_logs * 0.25)
    idle_min = int(idle_logs * 0.25)
    total_min = int(total_logs * 0.25)
    
    windows = [r["window_title"] for r in logs if r["window_title"]]
    unique_windows = list(dict.fromkeys(windows))[-5:] # Lấy tối đa 5 cửa sổ gần nhất
    windows_str = "\n".join([f"- {w}" for w in unique_windows])
    
    report = (
        f"Báo cáo hoạt động của '{username}' trong {hours} giờ qua:\n"
        f"- Tổng thời gian ghi nhận hoạt động: {format_minutes_to_hours(total_min)}.\n"
        f"  + Thời gian học tập tập trung (Learning): {format_minutes_to_hours(learning_min)}.\n"
        f"  + Thời gian lướt web giải trí (Distracted): {format_minutes_to_hours(distracted_min)}.\n"
        f"  + Thời gian treo máy không tương tác (Idle): {format_minutes_to_hours(idle_min)}.\n"
        f"- Các ứng dụng/cửa sổ đã mở gần đây:\n{windows_str}"
    )
    return report

def db_switch_dashboard_tab(col_num: int, tab: str) -> str:
    tab_vn = "Hiệu suất" if tab == "chart" else "Live Screen"
    return f"UI_ACTION:switch_tab:{col_num}:{tab}|Đã chuyển cột {col_num} sang tab {tab_vn} thành công."

def db_refresh_dashboard() -> str:
    return "UI_ACTION:refresh|Đã gửi tín hiệu tải lại toàn bộ giao diện Dashboard thành công."

# Bản đồ ánh xạ tên hàm từ AI sang hàm python thực tế
AI_FUNCTIONS = {
    "update_kpi": db_update_kpi,
    "update_allowed_distraction": db_update_allowed_distraction,
    "update_study_plan": db_update_study_plan,
    "get_system_status": get_system_status,
    "get_user_activity_report": db_get_user_activity_report,
    "switch_dashboard_tab": db_switch_dashboard_tab,
    "refresh_dashboard": db_refresh_dashboard
}

@app.post("/api/v1/ai/command")
def execute_ai_command(payload: AICommandPayload):
    command = payload.command.strip()
    if not command:
        raise HTTPException(status_code=400, detail="Lệnh không được để trống.")

    # 1. Bộ lọc Regex xử lý nhanh (Quick parser & Fallback)
    kpi_match = re.search(r'(?:kpi|mục tiêu|giờ học|giờ code)[^0-9]*?(\d+(?:\.\d+)?)', command, re.IGNORECASE)
    distract_match = re.search(r'(?:giải trí|xao nhãng|lướt web|cho phép)[^0-9]*?(\d+)', command, re.IGNORECASE)
    status_match = re.search(r'(?:cấu hình|trạng thái|thông số|status|setting|hệ thống)', command, re.IGNORECASE)

    if kpi_match and not distract_match:
        try:
            val = float(kpi_match.group(1))
            reply = db_update_kpi(val)
            return {"reply": f"🤖 [Hệ thống] {reply} Chúc hai bạn học tập hiệu quả!", "ui_action": {"type": "refresh"}}
        except Exception:
            pass
    elif distract_match and not kpi_match:
        try:
            val = int(distract_match.group(1))
            reply = db_update_allowed_distraction(val)
            return {"reply": f"🤖 [Hệ thống] {reply} Kỷ luật là sức mạnh!", "ui_action": {"type": "refresh"}}
        except Exception:
            pass
    elif status_match and len(command.split()) <= 4:
        reply = get_system_status()
        return {"reply": f"🤖 [Hệ thống] {reply}", "ui_action": None}

    # 2. Định nghĩa các Tools cho LLM
    tools = [
        {
            "type": "function",
            "function": {
                "name": "update_kpi",
                "description": "Cập nhật KPI giờ code học tập tối thiểu mỗi ngày của mỗi người.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "kpi_hours": {
                            "type": "number",
                            "description": "Số giờ KPI mới (ví dụ: 2.5, 3.0)"
                        }
                    },
                    "required": ["kpi_hours"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "update_allowed_distraction",
                "description": "Cập nhật số phút tối đa được phép lướt web giải trí trước khi Bot spam cảnh báo sỉ nhục.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "minutes": {
                            "type": "integer",
                            "description": "Số phút giải trí cho phép mới (ví dụ: 10, 15, 20)"
                        }
                    },
                    "required": ["minutes"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "update_study_plan",
                "description": "Cập nhật hoặc thêm giáo án ôn thi cho một tuần học cụ thể.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "week_number": {
                            "type": "integer",
                            "description": "Số thứ tự của tuần học (ví dụ: 1, 2, 3)"
                        },
                        "topic": {
                            "type": "string",
                            "description": "Chủ đề học tập chính của tuần"
                        },
                        "tasks": {
                            "type": "string",
                            "description": "Danh sách các nhiệm vụ cụ thể cần hoàn thành, phân tách bằng xuống dòng hoặc đánh số."
                        }
                    },
                    "required": ["week_number", "topic", "tasks"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "get_system_status",
                "description": "Lấy thông số cấu hình và trạng thái hiện tại của hệ thống.",
                "parameters": {
                    "type": "object",
                    "properties": {}
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "get_user_activity_report",
                "description": "Lấy báo cáo hoạt động chi tiết (giờ học, giờ chơi, các cửa sổ ứng dụng đã mở gần đây) của một thành viên cụ thể trong một số giờ gần nhất để AI có căn cứ giáo huấn hoặc nhắc nhở.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "username": {
                            "type": "string",
                            "description": "Tên thành viên cần kiểm tra hoạt động (ví dụ: bluebird, hung, duy)."
                        },
                        "hours": {
                            "type": "number",
                            "description": "Số giờ gần nhất muốn truy vấn hoạt động. Mặc định là 1.0 giờ."
                        }
                    },
                    "required": ["username"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "switch_dashboard_tab",
                "description": "Chuyển đổi tab hiển thị trên giao diện Dashboard của một học viên.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "col_num": {
                            "type": "integer",
                            "description": "Số cột của học viên trên Dashboard: 1 (học viên thứ nhất - mặc định bluebird), 2 (học viên thứ hai - Hưng)."
                        },
                        "tab": {
                            "type": "string",
                            "description": "Tab muốn chuyển sang: 'chart' (Biểu đồ hiệu suất) hoặc 'screen' (Ảnh màn hình live)."
                        }
                    },
                    "required": ["col_num", "tab"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "refresh_dashboard",
                "description": "Tải lại hoặc làm mới toàn bộ thông số và biểu đồ hiển thị trên giao diện Dashboard.",
                "parameters": {
                    "type": "object",
                    "properties": {}
                }
            }
        }
    ]

    # 3. Gọi AI 9Router xử lý các lệnh phức tạp hoặc cần đàm thoại tự nhiên
    api_base = os.getenv("OPENAI_API_BASE", "http://localhost:20128/v1").rstrip("/")
    nine_router_url = os.getenv("NINE_ROUTER_URL", f"{api_base}/chat/completions")
    api_key = os.getenv("NINE_ROUTER_API_KEY", os.getenv("OPENAI_API_KEY", "free_key_placeholder"))
    model_name = os.getenv("MODEL_NAME", "deepseek-v4-flash")

    # Lưu tin nhắn của Duy gửi lên qua UI vào chat_history
    chat_id = "ui_chatbot"
    db_add_chat_log(chat_id, "bluebird", "Duy", command, is_ai=0)

    # Lấy context RAG
    ai_context = build_ai_context("bluebird")

    system_prompt = (
        "Bạn là Sư phụ (Sifu) - một huyền thoại lập trình thi học sinh giỏi nghiêm khắc, cộc cằn, đồng thời là trợ lý chỉ huy của hệ thống.\n"
        "Nhiệm vụ của bạn là giám sát hai học viên Duy (bluebird) và Hưng, đồng thời tự động phân tích và kích hoạt các công cụ (tools) thích hợp dựa trên yêu cầu của học viên.\n"
        "Hãy tuân thủ các quy tắc gọi tool:\n"
        "1. Nhận diện ý định đổi KPI giờ học: gọi `update_kpi`.\n"
        "2. Nhận diện ý định đổi thời gian giải trí: gọi `update_allowed_distraction`.\n"
        "3. Nhận diện ý định cập nhật giáo án, bài tập tuần: gọi `update_study_plan`.\n"
        "4. Nhận diện yêu cầu kiểm tra cấu hình/trạng thái hệ thống: gọi `get_system_status`.\n"
        "5. Nhận diện yêu cầu xem/chuyển tab trên Dashboard: gọi `switch_dashboard_tab`.\n"
        "6. Nhận diện yêu cầu tải lại, refresh Dashboard: gọi `refresh_dashboard`.\n\n"
        "Nếu người dùng chỉ đàm thoại bình thường (không có ý định gọi tool nào), hãy phản hồi trực tiếp bằng giọng điệu lạnh lùng, châm biếm, cộc cằn nhưng có ngữ cảnh thực tế bên dưới.\n\n"
        "=== BỘ NHỚ & TIẾN ĐỘ THỜI GIAN THỰC ===\n"
        f"{ai_context}\n\n"
        "=== HƯỚNG DẪN GHI NHỚ DÀI HẠN ===\n"
        "Nếu ngươi muốn ghi nhớ điều gì dài hạn về học viên (như tính cách, sự kiện, lời hứa, thói quen học tập mới phát hiện), "
        "hãy ghi thêm tag dạng '[MEM_WRITE: key = value]' ở cuối phản hồi. "
        "Ví dụ: '[MEM_WRITE: duy_progress = đã lười học, lướt web liên tục]'. "
        "Hệ thống sẽ tự động cập nhật vào bộ nhớ dài hạn để dùng ở các cuộc trò chuyện sau."
    )

    try:
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
        
        # Xây dựng tin nhắn gửi LLM kèm lịch sử chat UI (10 tin nhắn gần nhất)
        messages = [{"role": "system", "content": system_prompt}]
        
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("""
            SELECT sender_name, message, is_ai FROM chat_history
            WHERE chat_id = 'ui_chatbot'
            ORDER BY timestamp DESC LIMIT 10
            """)
            history_rows = cursor.fetchall()
            
        history_rows.reverse()
        for row in history_rows:
            role = "assistant" if row["is_ai"] == 1 else "user"
            content = row["message"] if role == "assistant" else f"{row['sender_name']}: {row['message']}"
            messages.append({"role": role, "content": content})
            
        messages.append({"role": "user", "content": f"Duy: {command}"})

        data = {
            "model": model_name,
            "messages": messages,
            "tools": tools,
            "tool_choice": "auto"
        }

        res = requests.post(nine_router_url, headers=headers, json=data, timeout=60)
        res.raise_for_status()
        res.encoding = 'utf-8'
        
        text_clean = res.text.strip()
        if text_clean.endswith("data: [DONE]"):
            text_clean = text_clean[:-12].strip()
            
        res_json = json.loads(text_clean)
        message = res_json["choices"][0]["message"]
        
        # Kiểm tra xem AI có muốn gọi Tool hay không
        if "tool_calls" in message and message["tool_calls"]:
            tool_call = message["tool_calls"][0]
            func_name = tool_call["function"]["name"]
            func_args = json.loads(tool_call["function"]["arguments"])

            if func_name in AI_FUNCTIONS:
                execution_result = AI_FUNCTIONS[func_name](**func_args)
                
                # Bóc tách lệnh UI nếu có
                ui_action = None
                if isinstance(execution_result, str) and execution_result.startswith("UI_ACTION:"):
                    parts = execution_result.split("|", 1)
                    action_str = parts[0][10:] # Bỏ qua "UI_ACTION:"
                    execution_result = parts[1] if len(parts) > 1 else "Hoàn thành."
                    
                    action_parts = action_str.split(":")
                    if action_parts[0] == "switch_tab":
                        ui_action = {
                            "type": "switch_tab",
                            "col": int(action_parts[1]),
                            "tab": action_parts[2]
                        }
                    elif action_parts[0] == "refresh":
                        ui_action = {
                            "type": "refresh"
                        }
                
                # Gọi lại AI để tổng hợp phản hồi dưới dạng Sư phụ nghiêm khắc
                follow_up_data = {
                    "model": model_name,
                    "messages": [
                        {"role": "system", "content": "Bạn là Sư phụ AI. Hãy tóm tắt kết quả thực thi công cụ cho học viên Duy bằng giọng văn cộc cằn, châm biếm nhưng rõ ràng, nghiêm khắc để răn đe họ học bài. Luôn xưng là Ta và gọi học trò là Ngươi."},
                        {"role": "user", "content": command},
                        message,
                        {
                            "role": "tool",
                            "tool_call_id": tool_call["id"],
                            "name": func_name,
                            "content": execution_result
                        }
                    ]
                }
                follow_up_res = requests.post(nine_router_url, headers=headers, json=follow_up_data, timeout=60)
                follow_up_res.raise_for_status()
                follow_up_res.encoding = 'utf-8'
                
                # Làm sạch response text
                fu_text_clean = follow_up_res.text.strip()
                if fu_text_clean.endswith("data: [DONE]"):
                    fu_text_clean = fu_text_clean[:-12].strip()
                    
                final_response_raw = json.loads(fu_text_clean)["choices"][0]["message"]["content"]
                mem_writes = re.findall(r'\[MEM_WRITE:\s*(.*?)\s*=\s*(.*?)\s*\]', final_response_raw)
                for k, val in mem_writes:
                    db_write_ai_memory(k, val)
                final_response = re.sub(r'\[MEM_WRITE:.*?\]', '', final_response_raw).strip()
                db_add_chat_log(chat_id, "sifu", "Sư phụ AI", final_response, is_ai=1)
                return {"reply": final_response, "ui_action": ui_action}
            else:
                return {"reply": f"🤖 [Sư phụ] Ta không tìm thấy phép thuật '{func_name}' trên máy chủ.", "ui_action": None}
        else:
            # Nhánh đàm thoại thông thường — parse MEM_WRITE và lưu vào DB
            reply_raw = message["content"]
            mem_writes = re.findall(r'\[MEM_WRITE:\s*(.*?)\s*=\s*(.*?)\s*\]', reply_raw)
            for k, val in mem_writes:
                db_write_ai_memory(k, val)
            reply_clean = re.sub(r'\[MEM_WRITE:.*?\]', '', reply_raw).strip()
            db_add_chat_log(chat_id, "sifu", "Sư phụ AI", reply_clean, is_ai=1)
            return {"reply": reply_clean, "ui_action": None}

    except Exception as e:
        print(f"[AI Command Error]: {e}")
        return {"reply": f"🤖 [Sư phụ] Phép thần thông của ta bị ngắt quãng giữa chừng! (Lỗi: {str(e)})", "ui_action": None}

