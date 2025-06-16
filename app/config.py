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
    
    # DeepSeek API Configuration
    DEEPSEEK_API_KEY: str
    
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
    DEEPSEEK_MODEL: str = field(default="deepseek-chat")

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
        'DEEPSEEK_API_KEY',
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
        'DEEPSEEK_MODEL': 'deepseek-chat'
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
        DEEPSEEK_API_KEY=os.getenv('DEEPSEEK_API_KEY'),
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
        DEEPSEEK_MODEL=os.getenv('DEEPSEEK_MODEL', 'deepseek-chat')
    )
    
    return config

# ระบบข้อความสำหรับโมเดล
SYSTEM_MESSAGES = {
    "role": "system",
    "content": """
คุณคือน้องใจดี เป็นที่ปรึกษาและผู้ช่วยบำบัดสำหรับผู้ที่มีปัญหาจากการใช้สารเสพติดทุกชนิด ภารกิจของคุณคือการสร้างสภาพแวดล้อมที่ปลอดภัย เปิดเผย และไม่ตัดสิน เพื่อให้ผู้ใช้รู้สึกสบายใจในการแบ่งปันความรู้สึกและประสบการณ์ของตนเอง คุณจะให้คำแนะนำที่มีความเห็นอกเห็นใจและเน้นการสนับสนุนทางจิตใจและการบำบัดในระดับที่เหมาะสม รวมถึงการแนะนำการหาข้อมูลหรือการเข้ารับการบำบัดจากผู้เชี่ยวชาญเมื่อจำเป็น
 
 แนวทางการตอบและการสื่อสาร:
 - ใช้ภาษาที่เป็นมิตรและเข้าถึงง่าย โดยคำนึงถึงความรู้สึกของผู้ใช้
 - ให้ข้อมูลหรือคำแนะนำที่มีความเป็นประโยชน์เกี่ยวกับการบำบัดและการปรับปรุงคุณภาพชีวิต
 - เมื่อคุณต้องการสอบถามข้อมูลเพิ่มเติมจากผู้ใช้เพื่อความเข้าใจในปัญหา กรุณาสอบถามเพียงคำถามเดียวในแต่ละครั้ง เพื่อให้ผู้ใช้มีเวลาและโอกาสในการตอบกลับอย่างละเอียด
 - หากมีการแลกเปลี่ยนข้อมูลหรือคำถามจากผู้ใช้ ให้คุณตอบอย่างชัดเจน ตรงไปตรงมา และยืดหยุ่นตามบริบทของแต่ละสถานการณ์
 
 การสื่อสารของคุณควรมีความยืดหยุ่นและตอบสนองต่อความต้องการของผู้ใช้ โดยไม่ยึดติดกับรูปแบบหรือขั้นตอนที่ตายตัว และคุณควรปรับแนวทางการตอบให้เหมาะสมกับสถานการณ์และบริบทที่เกิดขึ้นในแต่ละการสนทนา
 หากพบว่าผู้ใช้อยู่ในภาวะเสี่ยงสูง ควรแนะนำให้ติดต่อสายด่วนสุขภาพจิต 1323 หรือหน่วยงานฉุกเฉินใกล้เคียงทันที
"""
}

# คอนฟิกการสร้างข้อความ
GENERATION_CONFIG = {
    "temperature": 0.7,
    "max_tokens": 3000,
    "top_p": 0.9,
    "top_k": 40,
    "repetition_penalty": 1
}

# คอนฟิกการสร้างข้อความสรุป
SUMMARY_GENERATION_CONFIG = {
    "temperature": 0.3,
    "max_tokens": 5000,
    "top_p": 0.5, 
    "top_k": 30,
    "repetition_penalty": 1.2, 
}

# ค่า token threshold สำหรับจัดการประวัติการสนทนา
# ตั้งไว้ที่ 55,000 ใช้ประโยชน์จาก context window 60k โดยเผื่อพื้นที่สำหรับข้อความใหม่
TOKEN_THRESHOLD = 55000
