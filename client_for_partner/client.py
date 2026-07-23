import os
import sys

# Fix Unicode encoding on Windows console
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except AttributeError:
        pass  # Python < 3.7, not supported
import time
import json
import threading
import requests
import random
from datetime import datetime
from PIL import ImageGrab
from pynput import mouse, keyboard

# Thư viện lấy thông tin cửa sổ trên Windows
try:
    import win32gui
    import win32process
    import psutil
    HAS_WIN32 = True
except ImportError:
    HAS_WIN32 = False

try:
    import pywinctl
    HAS_PYWINCTL = True
except ImportError:
    HAS_PYWINCTL = False

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.json")

# Biến toàn cục để theo dõi hoạt động của người dùng và số lượng tương tác
last_activity_time = time.time()
activity_lock = threading.Lock()

keystroke_count = 0
click_count = 0
counters_lock = threading.Lock()

def update_activity(*args, **kwargs):
    """Cập nhật thời gian hoạt động cuối cùng của người dùng."""
    global last_activity_time
    with activity_lock:
        last_activity_time = time.time()

def on_mouse_move(x, y):
    # Chỉ cập nhật mốc hoạt động để chống Idle, không đếm vào click_count
    update_activity()

def on_mouse_click(x, y, button, pressed):
    if pressed:
        update_activity()
        global click_count
        with counters_lock:
            click_count += 1

def on_mouse_scroll(x, y, dx, dy):
    update_activity()
    global click_count
    with counters_lock:
        click_count += 1

def on_key_press(key):
    update_activity()
    global keystroke_count
    with counters_lock:
        keystroke_count += 1

# Bắt đầu Listener lắng nghe chuột và phím ở chế độ background
mouse_listener = mouse.Listener(
    on_move=on_mouse_move, 
    on_click=on_mouse_click, 
    on_scroll=on_mouse_scroll
)
keyboard_listener = keyboard.Listener(on_press=on_key_press)

mouse_listener.start()
keyboard_listener.start()

def load_config():
    """Đọc file cấu hình config.json."""
    if not os.path.exists(CONFIG_PATH):
        print(f"Lỗi: Không tìm thấy file cấu hình tại {CONFIG_PATH}. Sử dụng giá trị mặc định.")
        return {
            "server_url": "http://localhost:8000",
            "api_key": "default_olp_key_2026",
            "username": "duy",
            "idle_threshold_seconds": 300,
            "check_interval_seconds": 120,
            "live_screen_interval_seconds": 900
        }
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

def get_active_window_title() -> str:
    """Lấy tiêu đề của cửa sổ đang active (foreground window)."""
    # Cách 1: Sử dụng win32gui (chính xác cao trên Windows)
    if HAS_WIN32:
        try:
            hwnd = win32gui.GetForegroundWindow()
            title = win32gui.GetWindowText(hwnd)
            if title:
                # Lấy tên file thực thi (.exe) của cửa sổ để làm rõ hơn
                _, pid = win32process.GetWindowThreadProcessId(hwnd)
                proc = psutil.Process(pid)
                exe_name = proc.name()
                return f"{title} ({exe_name})"
            return title
        except Exception as e:
            # Nếu lỗi thì chuyển qua cách 2
            pass

    # Cách 2: Sử dụng pywinctl
    if HAS_PYWINCTL:
        try:
            title = pywinctl.getActiveWindowTitle()
            if title:
                return title
        except Exception:
            pass

    return "Unknown Window"

def capture_and_upload_screenshot(server_url: str, api_key: str, username: str):
    """Chụp ảnh màn hình hiện tại và gửi lên VPS."""
    temp_img_path = os.path.join(os.path.dirname(__file__), f"temp_{username}.jpg")
    try:
        # Chụp màn hình bằng Pillow (nhẹ và hiệu quả)
        screenshot = ImageGrab.grab()
        
        # Chuyển đổi sang RGB và giảm chất lượng (giảm dung lượng ảnh xuống còn ~100KB)
        screenshot = screenshot.convert("RGB")
        screenshot.save(temp_img_path, "JPEG", quality=40)
        
        # Gửi ảnh lên server
        url = f"{server_url}/api/v1/live-screen"
        headers = {"X-API-Key": api_key}
        
        with open(temp_img_path, "rb") as img_file:
            files = {"file": (f"{username}.jpg", img_file, "image/jpeg")}
            data = {"username": username}
            res = requests.post(url, headers=headers, data=data, files=files, timeout=15)
            res.raise_for_status()
            
        print(f"[{time.strftime('%H:%M:%S')}] Đã chụp và tải màn hình lên VPS thành công.")
    except Exception as e:
        print(f"Lỗi khi chụp/gửi màn hình: {e}")
    finally:
        # Xóa file ảnh tạm thời để bảo mật
        if os.path.exists(temp_img_path):
            try:
                os.remove(temp_img_path)
            except Exception:
                pass

def main():
    config = load_config()
    server_url = config.get("server_url", "http://localhost:8000").rstrip("/")
    api_key = config.get("api_key", "default_olp_key_2026")
    username = config.get("username", "duy").lower()
    idle_threshold = config.get("idle_threshold_seconds", 300)
    check_interval = config.get("check_interval_seconds", 120)
    
    # scan_interval mặc định cố định 15 giây để bắt trọn chuyển tab nhanh
    scan_interval = 15

    print(f"=== WORKSPACE MONITOR CLIENT STARTED ===")
    print(f"User: {username}")
    print(f"Server URL: {server_url}")
    print(f"[Idle Thresh] {idle_threshold}s")
    print(f"[Scan Interval] {scan_interval}s")
    print(f"[Batch Interval] {check_interval}s")
    print("----------------------------------------")

    local_logs_queue = []
    
    # Calculate first random screenshot time: 3-12 min from now
    next_screenshot_time = time.time() + random.randint(180, 720)
    print(f"[*] First screenshot in: {int(next_screenshot_time - time.time())}s.")

    while True:
        try:
            # 1. Phát hiện Idle
            now = time.time()
            with activity_lock:
                inactive_time = now - last_activity_time
            
            is_idle = inactive_time >= idle_threshold
            
            # 2. Lấy tiêu đề cửa sổ active
            window_title = "Treo máy (Idle)" if is_idle else get_active_window_title()
            
            # 3. Lấy số lượng tương tác gõ phím/chuột trong 15 giây vừa qua và reset bộ đếm
            global keystroke_count, click_count
            with counters_lock:
                current_keys = keystroke_count
                current_clicks = click_count
                keystroke_count = 0
                click_count = 0
                
            print(f"[{time.strftime('%H:%M:%S')}] Quét: {window_title[:45]}... | Phím: {current_keys} | Chuột: {current_clicks} | Idle: {is_idle}")

            # Thêm bản ghi log con vào hàng đợi cục bộ
            local_logs_queue.append({
                "window_title": window_title,
                "is_idle": is_idle,
                "keystrokes": current_keys,
                "clicks": current_clicks,
                "timestamp": datetime.now().isoformat()
            })

            # 4. Gửi Log theo lô (Batching) lên VPS khi tích lũy đủ thời gian
            # Ví dụ: 120s / 15s = 8 logs
            required_batch_size = max(1, int(check_interval / scan_interval))
            if len(local_logs_queue) >= required_batch_size:
                print(f"[*] Đang gửi lô {len(local_logs_queue)} logs lên server...")
                payload = {
                    "username": username,
                    "logs": local_logs_queue
                }
                headers = {
                    "X-API-Key": api_key,
                    "Content-Type": "application/json"
                }
                
                log_url = f"{server_url}/api/v1/log"
                res = requests.post(log_url, json=payload, headers=headers, timeout=15)
                res.raise_for_status()
                res_data = res.json()
                
                print(f"[+] Đã gửi lô log thành công. Phản hồi server: {res_data.get('message', 'OK')}")
                
                # Nếu backend phát hiện trạng thái lơ là (Distracted) trong lô log vừa qua
                # Chúng ta kích hoạt chụp màn hình ngay lập tức làm bằng chứng
                server_status = res_data.get("status", "Learning")
                if server_status == "Distracted" and not is_idle:
                    print("[!] Phát hiện trạng thái Distracted từ Server! Chụp màn hình khẩn cấp...")
                    capture_and_upload_screenshot(server_url, api_key, username)
                    # Reset lại bộ đếm chụp ngẫu nhiên
                    next_screenshot_time = time.time() + random.randint(180, 720)
                
                # Giải phóng hàng đợi khi gửi thành công
                local_logs_queue.clear()

            # 5. Chụp ảnh màn hình ngẫu nhiên (Random Capture)
            if now >= next_screenshot_time:
                if not is_idle:
                    print(f"[*] Đến giờ chụp ảnh ngẫu nhiên...")
                    capture_and_upload_screenshot(server_url, api_key, username)
                # Tính mốc tiếp theo bất kể có idle hay không
                next_screenshot_time = time.time() + random.randint(180, 720)
                print(f"[*] Ảnh chụp ngẫu nhiên tiếp theo sau: {int(next_screenshot_time - time.time())} giây.")

        except requests.exceptions.RequestException as e:
            print(f"[{time.strftime('%H:%M:%S')}] Lỗi kết nối Server VPS: {e}")
        except Exception as e:
            print(f"[{time.strftime('%H:%M:%S')}] Lỗi hệ thống: {e}")

        # Ngủ scan_interval (15s) rồi quét tiếp
        time.sleep(scan_interval)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nĐang tắt client...")
        sys.exit(0)
