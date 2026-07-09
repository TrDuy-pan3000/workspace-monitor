FROM python:3.11-slim

WORKDIR /app

# Copy requirements và cài đặt thư viện
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy toàn bộ code backend
COPY . .

# Mở cổng 8000
EXPOSE 8000

# Khởi tạo db (nếu chưa có) và chạy uvicorn
CMD ["sh", "-c", "python database.py && uvicorn main:app --host 0.0.0.0 --port 8000"]
