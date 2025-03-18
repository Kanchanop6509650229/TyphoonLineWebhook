FROM python:3.11-slim

# ติดตั้งเครื่องมือที่จำเป็น
RUN apt-get update && apt-get install -y \
    gcc \
    python3-dev \
    && rm -rf /var/lib/apt/lists/*

# สร้างผู้ใช้ที่ไม่ใช่รูท
RUN groupadd -r appuser && useradd -r -g appuser appuser

# ตั้งค่าไดเรกทอรีทำงาน
WORKDIR /app

# ติดตั้งการพึ่งพาก่อน
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && \
    rm -rf /root/.cache

# คัดลอกโค้ดแอปพลิเคชัน
COPY . .

# ตั้งค่าสิทธิ์ที่เหมาะสม
RUN chown -R appuser:appuser /app && \
    chmod -R 755 /app

# ตั้งค่าตัวแปรสภาพแวดล้อมที่เกี่ยวข้องกับความปลอดภัย
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# สลับไปยังผู้ใช้ที่ไม่ใช่รูท
USER appuser

# คำสั่งเพื่อรันแอปพลิเคชัน
CMD ["python", "wsgi.py"]