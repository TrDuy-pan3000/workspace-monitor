import os
import sys
import time
import json
import threading
import requests
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

# Biến toàn cục để theo dõi hoạt động của người dùng
last_activity_time = time.time()
activity_lock = threading.Lock()

def update_activity(*args, **kwargs):
    """Cập nhật thời gian hoạt động cuối cùng của người dùng."""
    global last_activity_time
    with activity_lock:
        last_activity_time = time.time()

# Bắt đầu Listener lắng nghe chuột và phím ở chế độ background
mouse_listener = mouse.Listener(on_move=update_activity, on_click=update_activity, on_scroll=update_activity)
keyboard_listener = keyboard.Listener(on_press=update_activity)

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
    live_screen_interval = config.get("live_screen_interval_seconds", 900)

    print(f"=== OLP AI MONITORING CLIENT STARTED ===")
    print(f"User: {username}")
    print(f"Server URL: {server_url}")
    print(f"Ngưỡng Idle: {idle_threshold} giây")
    print(f"Thời gian kiểm tra: {check_interval} giây")
    print(f"Chụp màn hình định kỳ: {live_screen_interval} giây")
    print("----------------------------------------")

    last_live_screen_time = 0

    while True:
        try:
            # 1. Phát hiện Idle
            now = time.time()
            with activity_lock:
                inactive_time = now - last_activity_time
            
            is_idle = inactive_time >= idle_threshold
            
            # 2. Lấy tiêu đề cửa sổ active
            window_title = "Treo máy (Idle)" if is_idle else get_active_window_title()
            
            print(f"[{time.strftime('%H:%M:%S')}] Active Window: {window_title} | Idle: {is_idle}")

            # 3. Gửi Log lên VPS
            payload = {
                "username": username,
                "window_title": window_title,
                "is_idle": is_idle
            }
            headers = {
                "X-API-Key": api_key,
                "Content-Type": "application/json"
            }
            
            log_url = f"{server_url}/api/v1/log"
            res = requests.post(log_url, json=payload, headers=headers, timeout=10)
            res.raise_for_status()
            res_data = res.json()
            
            server_status = res_data.get("status", "Learning")
            
            # 4. Kiểm tra xem có cần chụp màn hình không
            # - Chụp nếu đến chu kỳ live screen định kỳ
            # - Hoặc chụp ngay lập tức nếu Backend phát hiện người dùng đang lơ là ('Distracted') để làm bằng chứng
            should_capture = (now - last_live_screen_time >= live_screen_interval) or (server_status == "Distracted")
            
            if should_capture and not is_idle:
                capture_and_upload_screenshot(server_url, api_key, username)
                last_live_screen_time = now

        except requests.exceptions.RequestException as e:
            print(f"[{time.strftime('%H:%M:%S')}] Lỗi kết nối Server VPS: {e}")
        except Exception as e:
            print(f"[{time.strftime('%H:%M:%S')}] Lỗi hệ thống: {e}")

        # Ngủ check_interval giây rồi lặp lại
        time.sleep(check_interval)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nĐang tắt client...")
        sys.exit(0)
