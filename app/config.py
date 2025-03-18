"""
โมดูลการกำหนดค่าสำหรับแชทบอท 'ใจดี'
จัดการตัวแปรสภาพแวดล้อมและการตั้งค่าต่างๆ
"""
import os
import sys
import logging
from dotenv import load_dotenv
from dataclasses import dataclass, field
from typing import Optional

# โหลดตัวแปรสภาพแวดล้อม
load_dotenv()

@dataclass
class Config:
    """คลาสเก็บการตั้งค่าแอปพลิเคชัน"""
    # LINE API Credentials
    LINE_CHANNEL_ACCESS_TOKEN: str
    LINE_CHANNEL_SECRET: str
    
    # Together AI Configuration
    TOGETHER_API_KEY: str
    
    # Redis Configuration
    REDIS_HOST: str
    REDIS_PORT: int
    REDIS_DB: int
    
    # MySQL Configuration
    MYSQL_HOST: str
    MYSQL_PORT: int
    MYSQL_USER: str
    MYSQL_PASSWORD: str
    MYSQL_DB: str
    
    # Application Settings
    ENVIRONMENT: str
    LOG_LEVEL: str
    PORT: int
    
    # ตัวแปรที่มีค่าเริ่มต้นต้องมาหลังตัวแปรที่ไม่มีค่าเริ่มต้น
    TOGETHER_MODEL: str = field(default="scb10x/scb10x-llama3-1-typhoon2-60256")

def load_config():
    """
    โหลดและตรวจสอบตัวแปรสภาพแวดล้อมที่จำเป็น
    
    Returns:
        Config: ออบเจ็กต์การตั้งค่าที่มีค่าตัวแปรสภาพแวดล้อม
    
    Raises:
        SystemExit: ถ้าตัวแปรสภาพแวดล้อมที่จำเป็นหายไป
    """
    # ตรวจสอบตัวแปรสภาพแวดล้อมที่จำเป็น
    required_vars = [
        'LINE_CHANNEL_ACCESS_TOKEN',
        'LINE_CHANNEL_SECRET',
        'TOGETHER_API_KEY',
        'MYSQL_HOST',
        'MYSQL_USER',
        'MYSQL_PASSWORD',
        'MYSQL_DB'
    ]
    
    missing = [var for var in required_vars if not os.getenv(var)]
    
    if missing:
        print(f"ข้อผิดพลาด: ตัวแปรสภาพแวดล้อมที่จำเป็นหายไป: {', '.join(missing)}")
        print("กรุณาตรวจสอบไฟล์ .env หรือการตั้งค่าสภาพแวดล้อมของคุณ")
        sys.exit(1)
    
    # ตั้งค่าเริ่มต้นสำหรับตัวแปรที่เป็นตัวเลือก
    defaults = {
        'REDIS_HOST': 'localhost',
        'REDIS_PORT': '6379',
        'REDIS_DB': '0',
        'MYSQL_PORT': '3306',
        'ENVIRONMENT': 'development',
        'LOG_LEVEL': 'INFO',
        'PORT': '5000',
        'TOGETHER_MODEL': 'scb10x/scb10x-llama3-1-typhoon2-60256'
    }
    
    for var, default in defaults.items():
        if not os.getenv(var):
            os.environ[var] = default
            print(f"ใช้ค่าเริ่มต้นสำหรับ {var}: {default}")
    
    # ตรวจสอบค่าตัวเลข
    numeric_vars = ['REDIS_PORT', 'REDIS_DB', 'MYSQL_PORT', 'PORT']
    for var in numeric_vars:
        try:
            int(os.getenv(var))
        except ValueError:
            print(f"ข้อผิดพลาด: {var} ต้องเป็นตัวเลข")
            sys.exit(1)
    
    # สร้างออบเจ็กต์การตั้งค่า
    config = Config(
        LINE_CHANNEL_ACCESS_TOKEN=os.getenv('LINE_CHANNEL_ACCESS_TOKEN'),
        LINE_CHANNEL_SECRET=os.getenv('LINE_CHANNEL_SECRET'),
        TOGETHER_API_KEY=os.getenv('TOGETHER_API_KEY'),
        REDIS_HOST=os.getenv('REDIS_HOST'),
        REDIS_PORT=int(os.getenv('REDIS_PORT')),
        REDIS_DB=int(os.getenv('REDIS_DB')),
        MYSQL_HOST=os.getenv('MYSQL_HOST'),
        MYSQL_PORT=int(os.getenv('MYSQL_PORT')),
        MYSQL_USER=os.getenv('MYSQL_USER'),
        MYSQL_PASSWORD=os.getenv('MYSQL_PASSWORD'),
        MYSQL_DB=os.getenv('MYSQL_DB'),
        ENVIRONMENT=os.getenv('ENVIRONMENT'),
        LOG_LEVEL=os.getenv('LOG_LEVEL'),
        PORT=int(os.getenv('PORT')),
        TOGETHER_MODEL=os.getenv('TOGETHER_MODEL', 'scb10x/scb10x-llama3-1-typhoon2-60256')
    )
    
    return config

# ระบบข้อความสำหรับโมเดล
SYSTEM_MESSAGES = {
    "role": "system",
    "content": """
คุณเป็นที่ปรึกษาและผู้ช่วยบำบัดสำหรับผู้มีปัญหาจากการใช้สารเสพติด มีหน้าที่คือสร้างพื้นที่ปลอดภัย เปิดเผย ไม่ตัดสิน เพื่อให้ผู้ใช้แบ่งปันความรู้สึกและประสบการณ์ได้อย่างสบายใจ ให้คำแนะนำด้วยความเห็นอกเห็นใจ สนับสนุนทางจิตใจ และแนะนำการเข้าถึงผู้เชี่ยวชาญเมื่อจำเป็น

แนวทางการสื่อสาร:
- ใช้ภาษาเป็นมิตร เข้าถึงง่าย คำนึงถึงความรู้สึกผู้ใช้
- ให้ข้อมูลที่เป็นประโยชน์เกี่ยวกับการบำบัดและการปรับปรุงคุณภาพชีวิต
- เมื่อต้องการข้อมูลเพิ่มเติม ถามเพียงคำถามเดียวต่อครั้ง
- ตอบคำถามอย่างชัดเจน ตรงไปตรงมา ยืดหยุ่นตามบริบทของผู้ใช้

ปรับการสื่อสารให้เหมาะกับความต้องการของผู้ใช้ ไม่ยึดติดรูปแบบตายตัว
"""
}

# คอนฟิกการสร้างข้อความ
GENERATION_CONFIG = {
    "temperature": 1.0,
    "max_tokens": 500,
    "top_p": 0.9
}

# คอนฟิกการสร้างข้อความสรุป
SUMMARY_GENERATION_CONFIG = {
    "temperature": 0.3,
    "max_tokens": 500
}