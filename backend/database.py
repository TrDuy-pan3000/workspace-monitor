import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "database.db")

def get_connection():
    """Tạo kết nối đến SQLite database với WAL mode + timeout."""
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn

def init_db():
    """Khởi tạo cấu trúc bảng và dữ liệu mặc định."""
    conn = get_connection()
    cursor = conn.cursor()

    # 1. Bảng user_logs
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS user_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT NOT NULL,
        timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
        window_title TEXT,
        status TEXT NOT NULL, -- 'Learning', 'Distracted', 'Idle'
        efficiency INTEGER NOT NULL, -- Điểm từ 0 đến 100
        keystrokes INTEGER DEFAULT 0,
        clicks INTEGER DEFAULT 0
    )
    """)

    # Đảm bảo các cột mới tồn tại nếu database đã được khởi tạo từ trước
    try:
        cursor.execute("ALTER TABLE user_logs ADD COLUMN keystrokes INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass
    try:
        cursor.execute("ALTER TABLE user_logs ADD COLUMN clicks INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass

    # Index giúp truy vấn log nhanh hơn
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_user_logs_user_time ON user_logs (username, timestamp)")

    # 2. Bảng title_classification_cache
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS title_classification_cache (
        window_title TEXT PRIMARY KEY,
        status TEXT NOT NULL,
        efficiency INTEGER NOT NULL,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )
    """)

    # 3. Bảng system_config
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS system_config (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL,
        description TEXT
    )
    """)

    # 4. Bảng study_plan
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS study_plan (
        week_number INTEGER PRIMARY KEY,
        topic TEXT NOT NULL,
        tasks TEXT NOT NULL -- Lưu danh sách tác vụ
    )
    """)

    # 5. Bảng chat_history
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS chat_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        chat_id TEXT,
        username TEXT,
        sender_name TEXT,
        message TEXT,
        is_ai INTEGER DEFAULT 0, -- 1 nếu là AI trả lời, 0 nếu là user chat
        timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
    )
    """)

    # 6. Bảng ai_memories
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS ai_memories (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL,
        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )
    """)

    # Chèn cấu hình mặc định nếu chưa tồn tại
    default_configs = [
        ("kpi_hours", "2.0", "Số giờ code tối thiểu mỗi ngày của mỗi người (REAL)"),
        ("allowed_distraction", "15", "Số phút tối đa được phép lướt web giải trí (INTEGER)"),
        ("telegram_token", "YOUR_TELEGRAM_BOT_TOKEN", "Token của Telegram Bot"),
        ("telegram_chat_id", "YOUR_TELEGRAM_CHAT_ID", "ID của nhóm Telegram để nhận cảnh báo"),
        ("api_key", "default_olp_key_2026", "API Key dùng để xác thực request gửi lên từ Client")
    ]

    for key, val, desc in default_configs:
        cursor.execute("""
        INSERT OR IGNORE INTO system_config (key, value, description)
        VALUES (?, ?, ?)
        """, (key, val, desc))

    # Chèn giáo án tuần 1 mặc định nếu chưa tồn tại
    cursor.execute("""
    INSERT OR IGNORE INTO study_plan (week_number, topic, tasks)
    VALUES (1, 'Khởi động OLP AI & Làm quen Kaggle', '1. Tìm hiểu luật chơi OLP AI và tạo tài khoản Kaggle.\n2. Hoàn thành notebook giới thiệu PyTorch/TensorFlow cơ bản.\n3. Thiết lập và chạy thử nghiệm Client giám sát hiệu suất.')
    """)

    conn.commit()
    conn.close()

if __name__ == "__main__":
    init_db()
    print("Khởi tạo Database thành công!")
