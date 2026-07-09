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
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
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

class ClientLogPayload(BaseModel):
    username: str
    window_title: str
    is_idle: bool

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

def handle_telegram_incoming_message(token: str, chat_id: int, text: str, message: dict):
    sender = message.get("from", {})
    first_name = sender.get("first_name", "Học viên")
    username = sender.get("username", "")
    
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
    
    system_prompt = (
        "Bạn là Sư phụ (Sifu) - một huyền thoại lập trình thi Olympic Tin học lập dị, nghiêm khắc và cộc cằn. "
        "Nhiệm vụ của bạn là giám sát, giáo huấn và thúc giục hai học viên Duy (bluebird) và Hưng ôn thi OLP AI. "
        "Hãy trả lời tin nhắn của học viên bằng giọng điệu lạnh lùng, thâm thúy, châm biếm, cộc cằn nhưng vô cùng yêu thương "
        "và mong muốn họ tiến bộ. Hãy mắng mỏ họ nếu họ lười biếng, đòi nghỉ ngơi, hoặc than vãn. "
        "Hãy trả lời ngắn gọn (tối đa 3-4 câu), không dùng icon hoa mỹ (có thể dùng icon cộc cằn 😠, 😤, 💻). "
        "Luôn xưng là 'Ta' (hoặc 'Sư phụ') và gọi người nhắn là 'Ngươi' hoặc 'Học trò'."
    )
    
    report = ""
    try:
        # Ánh xạ tên
        mapped_user = "bluebird"
        if "hung" in first_name.lower() or "hung" in username.lower():
            mapped_user = "user2"
        else:
            mapped_user = "bluebird"
            
        report = db_get_user_activity_report(mapped_user)
    except Exception:
        pass
        
    user_context = f"\n(Bối cảnh thực tế của học viên này trong 1 giờ qua:\n{report})" if report else ""
    prompt = f"Học viên {first_name} (@{username}) nhắn: \"{text}\"{user_context}\nSư phụ phản hồi:"
    
    reply_text = "Sư phụ đang bận code compiler..."
    try:
        api_base = os.getenv("OPENAI_API_BASE", "http://localhost:20128/v1").rstrip("/")
        nine_router_url = os.getenv("NINE_ROUTER_URL", f"{api_base}/chat/completions")
        api_key = os.getenv("NINE_ROUTER_API_KEY", os.getenv("OPENAI_API_KEY", "free_key_placeholder"))
        model_name = os.getenv("MODEL_NAME", "deepseek-v4-flash")
        
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
        payload = {
            "model": model_name,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt}
            ],
            "temperature": 0.8
        }
        res = requests.post(nine_router_url, json=payload, headers=headers, timeout=25)
        res.encoding = 'utf-8'
        if res.status_code == 200:
            # Làm sạch response text phòng trường hợp 9Router đính kèm 'data: [DONE]' ở cuối
            text_clean = res.text.strip()
            if text_clean.endswith("data: [DONE]"):
                text_clean = text_clean[:-12].strip()
            reply_text = json.loads(text_clean)["choices"][0]["message"]["content"]
        else:
            reply_text = f"Sư phụ đang bận code compiler, cút đi học bài đi! (Lỗi: {res.status_code})"
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

@app.on_event("startup")
def startup_event():
    import threading
    threading.Thread(target=telegram_polling_loop, daemon=True).start()

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
async def log_client_data(payload: ClientLogPayload, x_api_key: Optional[str] = Header(None)):
    # Xác thực API Key
    stored_key = get_config("api_key", "default_olp_key_2026")
    if not x_api_key or x_api_key != stored_key:
        raise HTTPException(status_code=401, detail="API Key không hợp lệ hoặc bị thiếu.")

    username = payload.username.lower()
    
    # Xử lý trạng thái Idle
    if payload.is_idle:
        status = "Idle"
        efficiency = 0
    else:
        status, efficiency = classify_window_title(payload.window_title)

    # Lưu log vào SQLite
    timestamp_str = datetime.now().isoformat()
    with get_db() as conn:
        conn.cursor().execute("""
        INSERT INTO user_logs (username, timestamp, window_title, status, efficiency)
        VALUES (?, ?, ?, ?, ?)
        """, (username, timestamp_str, payload.window_title, status, efficiency))
        conn.commit()

    # --- KIỂM TRA ĐỂ GỬI CẢNH BÁO TELEGRAM (RĂN ĐE) ---
    allowed_distraction_min = int(get_config("allowed_distraction", "15"))
    
    if status == "Distracted":
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
                
                # Chuẩn bị tin nhắn sỉ nhục
                teammate = "Duy" if username != "duy" else "Đồng đội"
                user_display = "Thằng Duy" if username == "duy" else f"Thằng đồng đội ({username})"
                caption = (
                    f"🚨 CẢNH BÁO LƯỜI BIẾNG: {user_display} đã lướt web giải trí liên tục {allowed_distraction_min} phút "
                    f"trong giờ học OLP AI rồi! @{teammate} vào xách tai nó lên cày Kaggle tiếp đi, đứt streak cả lũ bây giờ! 😡🔥"
                )
                
                # Nếu có ảnh chụp màn hình live mới nhất, gửi kèm ảnh
                photo_path = os.path.join(STATIC_DIR, f"latest_{username}.jpg")
                send_telegram_photo(photo_path, caption)

    return {"success": True, "status": status, "efficiency": efficiency}

@app.post("/api/v1/live-screen")
async def upload_live_screen(
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
        contents = await file.read()
        with open(file_path, "wb") as f:
            f.write(contents)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Không thể lưu file ảnh: {e}")

    return {"success": True, "file_url": f"/static/latest_{username}.jpg"}

@app.get("/api/v1/dashboard/stats")
async def get_dashboard_stats():
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
async def get_dashboard_chart(username: str):
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
async def get_study_plan():
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
    
    learning_min = learning_logs * 2
    distracted_min = distracted_logs * 2
    idle_min = idle_logs * 2
    
    windows = [r["window_title"] for r in logs if r["window_title"]]
    unique_windows = list(dict.fromkeys(windows))[-5:] # Lấy tối đa 5 cửa sổ gần nhất
    windows_str = "\n".join([f"- {w}" for w in unique_windows])
    
    report = (
        f"Báo cáo hoạt động của '{username}' trong {hours} giờ qua:\n"
        f"- Tổng thời gian ghi nhận hoạt động: {format_minutes_to_hours(total_logs * 2)}.\n"
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
async def execute_ai_command(payload: AICommandPayload):
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

    try:
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
        data = {
            "model": model_name,
            "messages": [
                {
                    "role": "system", 
                    "content": (
                        "Bạn là 'Não chỉ huy' - Trợ lý AI tối cao của hệ thống giám sát hiệu suất OLP AI Workspace.\n"
                        "Nhiệm vụ của bạn là quản trị hệ thống, tự động phân tích và kích hoạt các công cụ (tools) thích hợp dựa trên câu lệnh của học viên.\n"
                        "Hãy tuân thủ các quy tắc sau:\n"
                        "1. Nhận diện ý định đổi KPI giờ học: gọi `update_kpi`.\n"
                        "2. Nhận diện ý định đổi thời gian giải trí: gọi `update_allowed_distraction`.\n"
                        "3. Nhận diện ý định cập nhật giáo án, chủ đề, bài tập cho từng tuần cụ thể: gọi `update_study_plan`.\n"
                        "4. Nhận diện yêu cầu kiểm tra cấu hình/trạng thái: gọi `get_system_status`.\n"
                        "5. Nhận diện yêu cầu xem/chuyển sang tab khác (ví dụ: 'chuyển sang xem live screen bluebird', 'mở tab biểu đồ cột 1', v.v.): gọi `switch_dashboard_tab`.\n"
                        "6. Nhận diện yêu cầu tải lại, làm mới, refresh Dashboard: gọi `refresh_dashboard`.\n"
                        "Giọng văn phản hồi:\n"
                        "- Sử dụng ngôn ngữ tự nhiên, hài hước, mang tính răn đe kỷ luật nhưng cũng đầy khích lệ tinh thần (đúng chất đồng đội ôn thi OLP AI).\n"
                        "- Nếu người dùng tăng KPI hoặc chuyển đổi giao diện, hãy phản hồi đầy phấn chấn.\n"
                        "- Hãy gọi họ là 'đồng chí' hoặc 'coder' để tạo không khí ôn thi công nghệ."
                    )
                },
                {"role": "user", "content": command}
            ],
            "tools": tools,
            "tool_choice": "auto"
        }

        res = requests.post(nine_router_url, headers=headers, json=data, timeout=60)
        res.raise_for_status()
        res.encoding = 'utf-8'
        
        # Làm sạch response text phòng trường hợp 9Router đính kèm 'data: [DONE]' ở cuối
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
                
                # Gọi lại AI để tổng hợp phản hồi
                follow_up_data = {
                    "model": model_name,
                    "messages": [
                        {"role": "system", "content": "Bạn là Trợ lý AI OLP AI. Hãy tóm tắt kết quả thực thi công cụ một cách ngắn gọn, hài hước, mang tính châm biếm kỷ luật nhẹ nhàng hoặc khích lệ các coder."},
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
                    
                final_response = json.loads(fu_text_clean)["choices"][0]["message"]["content"]
                return {"reply": final_response, "ui_action": ui_action}
            else:
                return {"reply": f"Lỗi: Không tìm thấy hàm xử lý {func_name} trên server.", "ui_action": None}
        else:
            return {"reply": message["content"], "ui_action": None}

    except Exception as e:
        print(f"[AI Command Error]: {e}")
        return {"reply": f"Không thể kết nối đến LLM 9Router. Chi tiết lỗi: {str(e)}"}

