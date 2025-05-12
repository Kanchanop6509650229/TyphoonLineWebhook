"""
แพ็คเกจหลักของแชทบอท 'ใจดี'
รวมโมดูลทั้งหมดสำหรับแอปพลิเคชันแชทบอท
"""
import os
import logging

__version__ = '1.0.0'

# ตั้งค่าการบันทึกข้อมูล
logging.basicConfig(
    level=getattr(logging, os.getenv('LOG_LEVEL', 'INFO')),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('app.log'),
        logging.StreamHandler()
    ]
)

# นำเข้าส่วนประกอบหลักเพื่อให้ใช้งานได้ง่าย
try:
    from .app_main import app, init_scheduler
    from .async_api import AsyncTogetherClient
    from .chat_history_db import ChatHistoryDB
    from .token_counter import TokenCounter
    from .utils import safe_api_call, safe_db_operation
    from .config import load_config
    from .database_manager import DatabaseManager

    # ส่งออกส่วนประกอบที่จำเป็นสำหรับการใช้งานจากภายนอก
    __all__ = [
        'app',
        'init_scheduler',
        'AsyncTogetherClient',
        'ChatHistoryDB',
        'TokenCounter',
        'safe_api_call',
        'safe_db_operation',
        'load_config',
        'DatabaseManager'
    ]

except ImportError as e:
    logging.error(f"เกิดข้อผิดพลาดในการนำเข้าโมดูล: {str(e)}")
    # ส่งออกเฉพาะเวอร์ชันในกรณีที่มีข้อผิดพลาดในการนำเข้า
    __all__ = ['__version__']