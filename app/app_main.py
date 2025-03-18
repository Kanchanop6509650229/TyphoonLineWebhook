"""
แชทบอท 'ใจดี' - แอปพลิเคชันหลัก
โค้ดหลักสำหรับการจัดการข้อความจาก LINE API และการตอบกลับด้วย Together AI
"""
import os
import json
import logging
import requests
import time
import threading
import asyncio
from datetime import datetime, timedelta
from flask import Flask, request, abort, jsonify
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage
from together import Together
import redis
from random import choice
import signal
import atexit
from waitress import serve
from apscheduler.schedulers.background import BackgroundScheduler

# นำเข้าโมดูลภายในโปรเจค
from .middleware.rate_limiter import init_limiter
from .config import load_config, SYSTEM_MESSAGES, GENERATION_CONFIG, SUMMARY_GENERATION_CONFIG
from .utils import safe_db_operation, safe_api_call
from .chat_history_db import ChatHistoryDB
from .token_counter import TokenCounter
from .async_api import AsyncTogetherClient
from .database_init import initialize_database

# สร้างอินสแตนซ์แอป Flask
app = Flask(__name__)

# ตั้งค่าการบันทึกข้อมูล
logging.basicConfig(
    level=getattr(logging, os.getenv('LOG_LEVEL', 'INFO')),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('app.log'),
        logging.StreamHandler()
    ]
)

# โหลดการตั้งค่าและตัวแปรสภาพแวดล้อม
config = load_config()

# เริ่มต้นเซอร์วิสภายนอก
try:
    # เริ่มต้น Redis
    redis_client = redis.Redis(
        host=config.REDIS_HOST,
        port=config.REDIS_PORT,
        db=config.REDIS_DB,
        decode_responses=True,
        socket_timeout=5,
        socket_connect_timeout=5
    )
    redis_client.ping()  # ตรวจสอบการเชื่อมต่อ
    
    # เริ่มต้น Line API
    line_bot_api = LineBotApi(config.LINE_CHANNEL_ACCESS_TOKEN)
    handler = WebhookHandler(config.LINE_CHANNEL_SECRET)
    
    # เริ่มต้น Together client
    together_client = Together(api_key=config.TOGETHER_API_KEY)
    
    # เริ่มต้น Async client สำหรับการประมวลผลเบื้องหลัง
    async_together = AsyncTogetherClient(config.TOGETHER_API_KEY, config.TOGETHER_MODEL)
    threading.Thread(target=lambda: asyncio.run(async_together.setup())).start()
    
    # เริ่มต้นตัวนับโทเค็น
    token_counter = TokenCounter()
    
    # เริ่มต้น MySQL pool และฐานข้อมูล
    from mysql.connector import pooling
    mysql_pool = pooling.MySQLConnectionPool(
        pool_name="chat_pool",
        pool_size=10,
        host=config.MYSQL_HOST,
        user=config.MYSQL_USER,
        password=config.MYSQL_PASSWORD,
        database=config.MYSQL_DB,
        port=config.MYSQL_PORT,
        connect_timeout=10
    )
    
    # เริ่มต้นฐานข้อมูล (สร้างตารางถ้ายังไม่มี)
    initialize_database(mysql_pool)
    logging.info("เสร็จสิ้นการตรวจสอบและเริ่มต้นฐานข้อมูล")
    
    # เริ่มต้นฐานข้อมูล
    db = ChatHistoryDB(mysql_pool)
    
except Exception as e:
    logging.critical(f"เกิดข้อผิดพลาดในการเริ่มต้นแอปพลิเคชัน: {str(e)}")
    raise

# เริ่มต้น rate limiter
limiter = init_limiter(app)

# ค่าคงที่ส่วนของการแอพลิเคชัน
FOLLOW_UP_INTERVALS = [1, 3, 7, 14, 30]  # จำนวนวันในการติดตาม
SESSION_TIMEOUT = 604800  # 7 วัน (7 * 24 * 60 * 60 วินาที)
MESSAGE_LOCK_TIMEOUT = 30  # ระยะเวลาล็อค (วินาที)
PROCESSING_MESSAGES = [
    "⌛ กำลังคิดอยู่ค่ะ...",
    "🤔 กำลังประมวลผลข้อความของคุณ...",
    "📝 กำลังเรียบเรียงคำตอบ...",
    "🔄 รอสักครู่นะคะ..."
]

# คำที่บ่งชี้ความเสี่ยง
RISK_KEYWORDS = {
    'high_risk': [
        'ฆ่าตัวตาย', 'ทำร้ายตัวเอง', 'อยากตาย',
        'เกินขนาด', 'overdose', 'od',
        'เลือดออก', 'ชัก', 'หมดสติ'
    ],
    'medium_risk': [
        'นอนไม่หลับ', 'เครียด', 'กังวล',
        'ซึมเศร้า', 'เหงา', 'ท้อแท้'
    ]
}

# ฟังก์ชันเกี่ยวกับการดำเนินการเซสชัน
def get_chat_session(user_id):
    """ดึงหรือสร้างเซสชันการแชทจาก Redis"""
    try:
        history = redis_client.get(f"chat_session:{user_id}")
        if history:
            loaded_history = json.loads(history)
            return [
                {"role": msg_data["role"], "content": msg_data["content"]}
                for msg_data in loaded_history
            ]
        return []
    except redis.RedisError as e:
        logging.error(f"Redis error in get_chat_session: {str(e)}")
        return []

def save_chat_session(user_id, messages):
    """บันทึกเซสชันการแชทไปยัง Redis"""
    try:
        serialized_history = [
            {"role": msg["role"], "content": msg["content"]}
            for msg in messages[-10:]  # เก็บเฉพาะ 10 ข้อความล่าสุด
        ]
        
        redis_client.setex(
            f"chat_session:{user_id}", 
            3600 * 24,  # หมดอายุหลังจาก 24 ชั่วโมง
            json.dumps(serialized_history)
        )
    except redis.RedisError as e:
        logging.error(f"Redis error in save_chat_session: {str(e)}")

def check_session_timeout(user_id):
    """ตรวจสอบ timeout ของเซสชัน"""
    try:
        last_activity = redis_client.get(f"last_activity:{user_id}")
        if last_activity:
            # แปลงจาก bytes เป็น string ถ้าจำเป็น
            if isinstance(last_activity, bytes):
                last_activity = last_activity.decode('utf-8')
                
            last_activity_time = float(last_activity)
            if (datetime.now().timestamp() - last_activity_time) > SESSION_TIMEOUT:
                # ล้างเซสชัน
                redis_client.delete(f"chat_session:{user_id}")
                return True
        return False
    except Exception as e:
        logging.error(f"เกิดข้อผิดพลาดในการตรวจสอบ session timeout สำหรับผู้ใช้ {user_id}: {str(e)}")
        return False

def update_last_activity(user_id):
    """อัพเดทเวลาการใช้งานล่าสุด และตรวจสอบการแจ้งเตือน"""
    try:
        current_time = datetime.now().timestamp()
        last_activity = redis_client.get(f"last_activity:{user_id}")
        warning_sent = redis_client.get(f"timeout_warning:{user_id}")
        
        # แปลงจาก bytes เป็น string ถ้าจำเป็น
        if isinstance(last_activity, bytes):
            last_activity = last_activity.decode('utf-8')
        if isinstance(warning_sent, bytes):
            warning_sent = warning_sent.decode('utf-8')
        
        if last_activity:
            time_passed = current_time - float(last_activity)
            # ถ้าเวลาผ่านไป 6 วัน (1 วันก่อนหมด session) และยังไม่เคยส่งการแจ้งเตือน
            if time_passed > (SESSION_TIMEOUT - 86400) and not warning_sent:  # 86400 = 1 วัน
                warning_message = (
                    "⚠️ เซสชันของคุณจะหมดอายุในอีก 1 วัน\n"
                    "หากต้องการคุยต่อ กรุณาพิมพ์ข้อความใดๆ เพื่อต่ออายุเซสชัน"
                )
                line_bot_api.push_message(
                    user_id,
                    TextSendMessage(text=warning_message)
                )
                # ตั้งค่าว่าได้ส่งการแจ้งเตือนแล้ว
                redis_client.setex(
                    f"timeout_warning:{user_id}",
                    86400,  # หมดอายุใน 1 วัน
                    "1"
                )
                logging.info(f"ส่งการแจ้งเตือนหมดเวลาเซสชันไปยังผู้ใช้: {user_id}")
        
        # อัพเดทเวลาใช้งานล่าสุด
        redis_client.setex(
            f"last_activity:{user_id}",
            SESSION_TIMEOUT,
            str(current_time)
        )
        
    except Exception as e:
        logging.error(f"เกิดข้อผิดพลาดในการอัพเดทเวลาใช้งานล่าสุดสำหรับผู้ใช้ {user_id}: {str(e)}")

# ฟังก์ชันที่เกี่ยวข้องกับความเสี่ยงและความก้าวหน้า
def assess_risk(message):
    """ประเมินความเสี่ยงจากข้อความ"""
    message = message.lower()
    risk_level = 'low'
    matched_keywords = []
    
    for keyword in RISK_KEYWORDS['high_risk']:
        if keyword in message:
            risk_level = 'high'
            matched_keywords.append(keyword)
    
    if risk_level == 'low':
        for keyword in RISK_KEYWORDS['medium_risk']:
            if keyword in message:
                risk_level = 'medium'
                matched_keywords.append(keyword)
    
    return risk_level, matched_keywords

def save_progress_data(user_id, risk_level, keywords):
    """บันทึกข้อมูลความก้าวหน้า"""
    try:
        progress_data = {
            'timestamp': datetime.now().isoformat(),
            'risk_level': risk_level,
            'keywords': keywords
        }
        redis_client.lpush(f"progress:{user_id}", json.dumps(progress_data))
        redis_client.ltrim(f"progress:{user_id}", 0, 99)  # เก็บแค่ 100 รายการล่าสุด
    except Exception as e:
        logging.error(f"เกิดข้อผิดพลาดในการบันทึกความก้าวหน้า: {str(e)}")

def generate_progress_report(user_id):
    """สร้างรายงานความก้าวหน้า"""
    try:
        progress_data = redis_client.lrange(f"progress:{user_id}", 0, -1)
        if not progress_data:
            return "ยังไม่มีข้อมูลความก้าวหน้า"

        data = [json.loads(item) for item in progress_data]
        
        # วิเคราะห์แนวโน้มความเสี่ยง
        risk_trends = {
            'high': sum(1 for d in data if d['risk_level'] == 'high'),
            'medium': sum(1 for d in data if d['risk_level'] == 'medium'),
            'low': sum(1 for d in data if d['risk_level'] == 'low')
        }
        
        report = (
            "📊 รายงานความก้าวหน้า\n\n"
            f"📅 ช่วงเวลา: {data[-1]['timestamp'][:10]} ถึง {data[0]['timestamp'][:10]}\n"
            f"📈 การประเมินความเสี่ยง:\n"
            f"▫️ ความเสี่ยงสูง: {risk_trends['high']} ครั้ง\n"
            f"▫️ ความเสี่ยงปานกลาง: {risk_trends['medium']} ครั้ง\n"
            f"▫️ ความเสี่ยงต่ำ: {risk_trends['low']} ครั้ง\n"
        )
        return report
    except Exception as e:
        logging.error(f"เกิดข้อผิดพลาดในการสร้างรายงานความก้าวหน้า: {str(e)}")
        return "ไม่สามารถสร้างรายงานได้"

# ฟังก์ชันที่เกี่ยวข้องกับการล็อคข้อความ
def is_user_locked(user_id):
    """ตรวจสอบว่าผู้ใช้ถูกล็อคอยู่หรือไม่"""
    return redis_client.exists(f"message_lock:{user_id}")

def lock_user(user_id):
    """ล็อคผู้ใช้"""
    redis_client.setex(f"message_lock:{user_id}", MESSAGE_LOCK_TIMEOUT, "1")

def unlock_user(user_id):
    """ปลดล็อคผู้ใช้"""
    redis_client.delete(f"message_lock:{user_id}")

# ฟังก์ชันเกี่ยวกับการติดตามผู้ใช้
def schedule_follow_up(user_id, interaction_date):
    """จัดการการติดตามผู้ใช้"""
    try:
        current_date = datetime.now()
        for days in FOLLOW_UP_INTERVALS:
            follow_up_date = interaction_date + timedelta(days=days)
            if follow_up_date > current_date:
                redis_client.zadd(
                    'follow_up_queue',
                    {user_id: follow_up_date.timestamp()}
                )
                break
    except Exception as e:
        logging.error(f"เกิดข้อผิดพลาดในการกำหนดการติดตามผล: {str(e)}")

def check_and_send_follow_ups():
    """ตรวจสอบและส่งการติดตามที่ถึงกำหนด"""
    logging.info("กำลังรันการตรวจสอบการติดตามผลตามกำหนดเวลา")
    try:
        current_time = datetime.now().timestamp()
        # ดึงรายการติดตามที่ถึงกำหนด
        due_follow_ups = redis_client.zrangebyscore(
            'follow_up_queue',
            0,
            current_time
        )
        
        for user_id in due_follow_ups:
            # แปลง bytes เป็น string ถ้าจำเป็น
            if isinstance(user_id, bytes):
                user_id = user_id.decode('utf-8')
                
            follow_up_message = (
                "สวัสดีค่ะ ใจดีมาติดตามผลการเลิกใช้สารเสพติดของคุณ\n"
                "คุณสามารถเล่าให้ฟังได้ว่าช่วงที่ผ่านมาเป็นอย่างไรบ้าง?"
            )
            try:
                line_bot_api.push_message(
                    user_id,
                    TextSendMessage(text=follow_up_message)
                )
                # ลบรายการติดตามที่ส่งแล้ว
                redis_client.zrem('follow_up_queue', user_id)
                # บันทึกการติดตามลงในฐานข้อมูล
                db.update_follow_up_status(user_id, 'sent', datetime.now())
                logging.info(f"ส่งการติดตามไปยังผู้ใช้: {user_id}")
            except Exception as e:
                logging.error(f"เกิดข้อผิดพลาดในการส่งการติดตามไปยัง {user_id}: {str(e)}")
                
    except Exception as e:
        logging.error(f"เกิดข้อผิดพลาดใน check_and_send_follow_ups: {str(e)}")

# ฟังก์ชันที่เกี่ยวข้องกับการแสดงสถานะการประมวลผล
def send_processing_status(user_id, reply_token):
    """ส่งข้อความแจ้งสถานะกำลังประมวลผล"""
    try:
        # ส่งข้อความว่ากำลังประมวลผลทันที
        processing_message = choice(PROCESSING_MESSAGES)
        line_bot_api.reply_message(
            reply_token,
            TextSendMessage(text=processing_message)
        )
        return True
    except Exception as e:
        logging.error(f"เกิดข้อผิดพลาดในการส่งสถานะประมวลผล: {str(e)}")
        return False

def send_final_response(user_id, bot_response):
    """ส่งคำตอบสุดท้ายหลังประมวลผลเสร็จ"""
    try:
        line_bot_api.push_message(
            user_id,
            TextSendMessage(text=bot_response)
        )
        return True
    except Exception as e:
        logging.error(f"เกิดข้อผิดพลาดในการส่งคำตอบสุดท้าย: {str(e)}")
        return False

def start_loading_animation(user_id, duration=60):
    """แสดงภาพเคลื่อนไหวการโหลดของ LINE ให้กับผู้ใช้
    
    Args:
        user_id (str): LINE user ID
        duration (int): ระยะเวลาเป็นวินาที (ต้องอยู่ในช่วง 5-60 และเป็นจำนวนเท่าของ 5)
    
    Returns:
        bool: True หากสำเร็จ, False หากไม่สำเร็จ
    """
    try:
        # ใช้ 60 วินาทีเสมอ (ระยะเวลาสูงสุดที่อนุญาตโดย LINE API)
        duration = 60
        
        # ดึงโทเค็นการเข้าถึงจากตัวแปรสภาพแวดล้อม
        access_token = config.LINE_CHANNEL_ACCESS_TOKEN
        
        # สร้างคำขอ
        url = 'https://api.line.me/v2/bot/chat/loading/start'
        headers = {
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {access_token}'
        }
        payload = {
            'chatId': user_id,
            'loadingSeconds': duration
        }
        
        # ส่งคำขอ
        response = requests.post(url, headers=headers, json=payload)
        
        # ตรวจสอบการตอบกลับ - ทั้ง 200 และ 202 ถือว่าสำเร็จ
        # 202 หมายถึง "Accepted" ใน HTTP ซึ่งเหมาะสำหรับการดำเนินการแบบอะซิงโครนัส
        if response.status_code in [200, 202]:
            logging.info(f"เริ่มภาพเคลื่อนไหวการโหลดสำหรับผู้ใช้ {user_id} เป็นเวลา {duration} วินาที (สถานะ: {response.status_code})")
            return True, duration
        else:
            logging.error(f"ไม่สามารถเริ่มภาพเคลื่อนไหวการโหลด: {response.status_code} {response.text}")
            return False, 0
    except Exception as e:
        logging.error(f"เกิดข้อผิดพลาดในการเริ่มภาพเคลื่อนไหวการโหลด: {str(e)}")
        return False, 0

# ฟังก์ชันหลักสำหรับสรุปประวัติการสนทนา
@safe_api_call
def summarize_conversation_history(history):
    """สรุปประวัติการสนทนาให้กระชับ"""
    if not history:
        return ""
        
    try:
        summary_prompt = "นี่คือประวัติการสนทนา โปรดสรุปประเด็นสำคัญในประวัติการสนทนานี้:\n"
        for _, msg, resp in history:
            summary_prompt += f"\nผู้ใช้: {msg}\nบอท: {resp}\n"
        
        response = together_client.chat.completions.create(
            model=config.TOGETHER_MODEL,
            messages=[
                SYSTEM_MESSAGES,
                {"role": "user", "content": summary_prompt}
            ],
            **SUMMARY_GENERATION_CONFIG
        )
        
        return response.choices[0].message.content
    except Exception as e:
        logging.error(f"เกิดข้อผิดพลาดใน summarize_conversation_history: {str(e)}")
        return ""

# ฟังก์ชันสำหรับการจัดการคำสั่งกับการแสดงสถานะประมวลผล
def handle_command_with_processing(user_id, command):
    """จัดการคำสั่งพร้อมแสดงสถานะประมวลผล"""
    # สำหรับคำสั่ง ใช้ภาพเคลื่อนไหวสั้นกว่า (10 วินาที) เนื่องจากคำสั่งประมวลผลเร็วกว่า
    animation_success, _ = start_loading_animation(user_id, duration=10)
    
    response_text = None
    
    if command == '/reset':
        db.clear_user_history(user_id)
        redis_client.delete(f"chat_session:{user_id}")
        response_text = (
            "🔄 ล้างประวัติการสนทนาเรียบร้อยแล้วค่ะ\n\n"
            "เราสามารถเริ่มต้นการสนทนาใหม่ได้ทันที\n"
            "คุณต้องการพูดคุยเกี่ยวกับเรื่องอะไรดีคะ?"
        )
        
    elif command == '/help':
        response_text = (
            "สวัสดีค่ะ 👋 ฉันคือน้องใจดี ผู้ช่วยดูแลและให้คำปรึกษาสำหรับผู้ที่ต้องการเลิกใช้สารเสพติด\n\n"
            "💬 ฉันสามารถช่วยคุณได้ดังนี้:\n"
            "- พูดคุยและให้กำลังใจในการเลิกใช้สารเสพติด\n"
            "- ให้ข้อมูลเกี่ยวกับผลกระทบของสารเสพติดต่อร่างกายและจิตใจ\n"
            "- แนะนำเทคนิคจัดการความอยากและความเครียด\n"
            "- เชื่อมต่อกับบริการช่วยเหลือในกรณีฉุกเฉิน\n\n"
            "📋 คำสั่งที่ใช้ได้:\n"
            "📊 /status - ดูสถิติการใช้งานและข้อมูลเซสชัน\n"
            "📈 /progress - ดูรายงานความก้าวหน้าของคุณ\n"
            "🚨 /emergency - ดูข้อมูลติดต่อฉุกเฉินและสายด่วน\n"
            "📩 /feedback - ส่งความคิดเห็นเพื่อพัฒนาระบบ\n"
            "❓ /help - แสดงเมนูช่วยเหลือนี้\n\n"
            "💡 ตัวอย่างคำถามที่สามารถถามฉันได้:\n"
            "- \"ช่วยประเมินการใช้สารเสพติดของฉันหน่อย\"\n"
            "- \"ผลกระทบของยาบ้าต่อร่างกายมีอะไรบ้าง\"\n"
            "- \"มีเทคนิคจัดการความอยากยาอย่างไร\"\n"
            "- \"ฉันควรทำอย่างไรเมื่อรู้สึกอยากกลับไปใช้สารอีก\"\n\n"
            "เริ่มพูดคุยกับฉันได้เลยนะคะ ฉันพร้อมรับฟังและช่วยเหลือคุณ 💚"
        )
    
    elif command == '/status':
        status_data = {
        'history_count': db.get_user_history_count(user_id),
        'important_count': db.get_important_message_count(user_id),
        'last_interaction': db.get_last_interaction(user_id),
        'current_session': redis_client.exists(f"chat_session:{user_id}") == 1,
        'total_tokens': db.get_total_tokens(user_id) or 0,
        'session_tokens': 0,
        }

        # คำนวณโทเค็นในเซสชัน
        session_data = redis_client.get(f"chat_session:{user_id}")
        if session_data:
            history = json.loads(session_data)
            total_session_text = ""
            for msg in history:
                if msg['role'] in ['user', 'assistant']:
                    total_session_text += msg.get('content', '')
            status_data['session_tokens'] = token_counter.count_tokens(total_session_text)

        # อัพเดทข้อความสถานะพร้อมตัวเลขสำคัญ
        response_text = (
            "📊 สถิติการสนทนาของคุณ\n"
            f"▫️ จำนวนการสนทนาที่บันทึก: {status_data['history_count']} ครั้ง\n"
            f"▫️ ประเด็นสำคัญที่พูดคุย: {status_data['important_count']} รายการ\n"
            f"▫️ สนทนาล่าสุดเมื่อ: {status_data['last_interaction']}\n"
            f"▫️ สถานะเซสชันปัจจุบัน: {'🟢 กำลังสนทนาอยู่' if status_data['current_session'] else '🔴 ยังไม่เริ่มสนทนา'}\n\n"
            f"▫️ จำนวน Token ทั้งหมด: {status_data['total_tokens']:,} tokens\n"
            f"▫️ จำนวน Token ในเซสชันปัจจุบัน: {status_data['session_tokens']:,} tokens\n\n"
            "💚 น้องใจดีพร้อมให้คำปรึกษาและสนับสนุนคุณตลอดเส้นทางการเลิกสารเสพติด\n"
            "💬 มีคำถามหรือต้องการความช่วยเหลือ เพียงพิมพ์บอกฉันได้เลยค่ะ"
        )

    elif command == '/emergency':
        response_text = (
            "🚨 บริการช่วยเหลือฉุกเฉิน 🚨\n\n"
            "หากคุณหรือคนใกล้ตัวกำลังประสบปัญหาต่อไปนี้:\n"
            "- ใช้สารเสพติดเกินขนาด (Overdose)\n"
            "- มีอาการชัก เลือดออก หมดสติ\n"
            "- มีความคิดทำร้ายตัวเอง\n"
            "- มีอาการถอนยารุนแรง\n\n"
            "📞 ติดต่อขอความช่วยเหลือด่วนได้ที่:\n"
            "🔸 สายด่วนกรมควบคุมโรค: 1422\n"
            "🔸 ศูนย์ปรึกษาปัญหายาเสพติด: 1165\n"
            "🔸 หน่วยกู้ชีพฉุกเฉิน: 1669\n"
            "🔸 สายด่วนสุขภาพจิต: 1323\n\n"
            "🌐 เว็บไซต์ช่วยเหลือ:\n"
            "https://www.pmnidat.go.th\n\n"
            "💚 การขอความช่วยเหลือคือก้าวแรกของการดูแลตัวเอง"
        )
    
    elif command == '/feedback':
        response_text = (
            "🌟 ความคิดเห็นของคุณมีคุณค่าต่อการพัฒนา\n\n"
            "น้องใจดีต้องการพัฒนาให้ดียิ่งขึ้นสำหรับทุกคน\n"
            "โปรดแสดงความคิดเห็นผ่านแบบฟอร์มนี้:\n"
            "https://forms.gle/7K2y21gomWHGcWpq9\n\n"
            "🙏 ขอบคุณที่ช่วยพัฒนาน้องใจดีให้ดีขึ้น"
        )
    
    elif command == '/progress':
        report = generate_progress_report(user_id)
        response_text = report if report else (
            "📊 รายงานความก้าวหน้า\n\n"
            "ยังไม่มีข้อมูลความก้าวหน้าเพียงพอสำหรับการวิเคราะห์\n\n"
            "เมื่อเราพูดคุยกันมากขึ้น น้องใจดีจะสามารถติดตามและวิเคราะห์ความก้าวหน้าของคุณได้"
        )
    
    else:
        response_text = "คำสั่งไม่ถูกต้อง ลองพิมพ์ /help เพื่อดูคำสั่งทั้งหมด"

    if response_text:
        send_final_response(user_id, response_text)

# ฟังก์ชันสำหรับการจัดการข้อความที่ถูกล็อค
def handle_locked_user(user_id):
    """จัดการกรณีผู้ใช้ถูกล็อค"""
    wait_notice_sent = redis_client.exists(f"wait_notice:{user_id}")
    
    if not wait_notice_sent:
        line_bot_api.push_message(
            user_id,
            TextSendMessage(text="กรุณารอระบบประมวลผลข้อความก่อนหน้าให้เสร็จสิ้นก่อนค่ะ")
        )
        redis_client.setex(f"wait_notice:{user_id}", 10, "1")

# ฟังก์ชันสำหรับประมวลผลข้อความของผู้ใช้
def process_user_message(user_id, user_message, reply_token):
    """ประมวลผลข้อความผู้ใช้พร้อมภาพเคลื่อนไหวและการจัดการเซสชัน"""
    start_time = time.time()
    redis_client.delete(f"wait_notice:{user_id}")
    
    # เริ่มภาพเคลื่อนไหวการโหลด
    animation_success, _ = start_loading_animation(user_id)
    
    # ตรวจสอบการหมดเวลาเซสชัน
    if check_session_timeout(user_id):
        send_session_timeout_message(user_id)
        return
        
    # อัพเดทกิจกรรมล่าสุดของผู้ใช้
    update_last_activity(user_id)
    
    # ตรวจสอบและจัดการคำสั่ง
    if user_message.startswith('/'):
        handle_command_with_processing(user_id, user_message)
        return
        
    # ประมวลผลกับ AI และส่งการตอบกลับ
    process_ai_response(user_id, user_message, start_time, animation_success)

def send_session_timeout_message(user_id):
    """ส่งข้อความเซสชันหมดอายุ"""
    welcome_back = (
        "สวัสดีค่ะ ยินดีต้อนรับกลับมา 👋\n\n"
        "เซสชันก่อนหน้าของเราหมดอายุแล้ว เราสามารถเริ่มการสนทนาใหม่ได้ทันที\n\n"
        "💡 ต้องการดูประวัติการสนทนาก่อนหน้า พิมพ์: /status\n"
        "💡 ต้องการดูรายงานความก้าวหน้า พิมพ์: /progress\n"
        "💡 ต้องการคำแนะนำเพิ่มเติม พิมพ์: /help\n\n"
        "คุณต้องการพูดคุยเกี่ยวกับเรื่องอะไรดีคะวันนี้?"
    )
    send_final_response(user_id, welcome_back)

def prepare_conversation_context(messages, optimized_history):
    """เตรียมบริบทการสนทนาโดยใช้ประวัติ"""
    # ตรวจสอบว่า optimized_history เป็น None หรือไม่
    if optimized_history is None:
        # ถ้าเป็น None ให้ใช้ list ว่าง
        optimized_history = []
        
    if len(optimized_history) > 5:
        summary = summarize_conversation_history(optimized_history[5:])
        if summary:
            messages.append({"role": "assistant", "content": f"สรุปการสนทนาก่อนหน้า: {summary}"})

@safe_api_call
def generate_ai_response(messages):
    """สร้างการตอบกลับด้วย AI โดยมีการจัดการข้อผิดพลาด"""
    return together_client.chat.completions.create(
        model=config.TOGETHER_MODEL,
        messages=[SYSTEM_MESSAGES] + messages,
        **GENERATION_CONFIG
    )

def process_conversation_data(user_id, user_message, bot_response, messages):
    """ประมวลผลและบันทึกข้อมูลการสนทนา"""
    # นับโทเค็นสำหรับการสนทนา
    token_count = token_counter.count_tokens(user_message + bot_response)

    # ประเมินความเสี่ยง
    risk_level, keywords = assess_risk(user_message)
    save_progress_data(user_id, risk_level, keywords)

    # บันทึกการสนทนาและกำหนดการติดตาม
    save_chat_session(user_id, messages)
    db.save_conversation(
        user_id=user_id,
        user_message=user_message,
        bot_response=bot_response,
        token_count=token_count
    )
    schedule_follow_up(user_id, datetime.now())
    
    # ส่งการแจ้งเตือนถ้าพบความเสี่ยงสูง
    if risk_level == 'high':
        emergency_message = (
            "⚠️ น้องใจดีกังวลว่าคุณอาจกำลังเผชิญกับภาวะเสี่ยง\n\n"
            "ขอแนะนำให้ติดต่อผู้เชี่ยวชาญเพื่อรับความช่วยเหลือโดยเร็วที่สุด:\n"
            "📞 สายด่วนสุขภาพจิต: 1323\n"
            "📞 สายด่วนยาเสพติด: 1165\n"
            "📞 หน่วยกู้ชีพฉุกเฉิน: 1669\n\n"
            "คุณไม่จำเป็นต้องเผชิญกับสิ่งนี้เพียงลำพัง การขอความช่วยเหลือคือความกล้าหาญ"
        )
        send_final_response(user_id, emergency_message)

def handle_response_timing(start_time, animation_success):
    """จัดการเวลาในการตอบสนองเพื่อประสบการณ์ผู้ใช้ที่ดีขึ้น"""
    # คำนวณเวลาที่ผ่านไป
    elapsed_time = time.time() - start_time
    
    # ถ้าเรามีการเคลื่อนไหวที่สำเร็จและการตอบสนอง API กลับมาอย่างรวดเร็ว
    # เพิ่มการหน่วงเวลาเล็กน้อยเพื่อให้แน่ใจว่าผู้ใช้เห็นภาพเคลื่อนไหวเป็นระยะเวลาที่เหมาะสม
    # แต่ไม่นานเกินไปที่จะทำให้เกิดความหงุดหงิด (ขั้นต่ำ 5 วินาที สูงสุด 15 วินาที)
    if animation_success and elapsed_time < 5:
        # เพิ่มการหน่วงเวลาเล็กน้อยเพื่อให้แน่ใจว่าการเคลื่อนไหวจะถูกมองเห็นเป็นเวลาอย่างน้อย 5 วินาที
        time.sleep(5 - elapsed_time)

def process_ai_response(user_id, user_message, start_time, animation_success):
    """สร้างการตอบกลับ AI และจัดการผลลัพธ์"""
    try:
        # ดึงเซสชันการแชทและประวัติ
        messages = get_chat_session(user_id)
        
        # ประมวลผลประวัติและสร้างการตอบกลับ
        optimized_history = db.get_user_history(user_id, max_tokens=10000)
        prepare_conversation_context(messages, optimized_history)
        
        # เพิ่มข้อความของผู้ใช้
        messages.append({"role": "user", "content": user_message})
        
        # รับการตอบกลับจาก Together
        response = generate_ai_response(messages)
        bot_response = response.choices[0].message.content
        messages.append({"role": "assistant", "content": bot_response})

        # ประมวลผลข้อมูลการตอบกลับ
        process_conversation_data(user_id, user_message, bot_response, messages)
        
        # จัดการจังหวะเวลาสำหรับ UX ที่ดีขึ้น
        handle_response_timing(start_time, animation_success)
        
        # ส่งการตอบกลับสุดท้าย
        send_final_response(user_id, bot_response)
        
        # บันทึกเวลาประมวลผลทั้งหมด
        logging.info(f"เวลาในการประมวลผลทั้งหมดสำหรับผู้ใช้ {user_id}: {time.time() - start_time:.2f} วินาที")
        
    except Exception as e:
        logging.error(f"เกิดข้อผิดพลาดในการประมวลผล AI: {str(e)}", exc_info=True)
        send_final_response(user_id, "ขออภัยค่ะ เกิดข้อผิดพลาดในการประมวลผล")

# เส้นทาง Flask
@app.route("/callback", methods=['POST'])
@limiter.limit("10/minute")
def callback():
    # รับค่า X-Line-Signature header
    signature = request.headers['X-Line-Signature']

    # รับเนื้อหาคำขอเป็นข้อความ
    body = request.get_data(as_text=True)
    app.logger.info("Request body: " + body)

    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)

    return 'OK'

@app.route("/health", methods=['GET'])
@limiter.exempt  # ไม่ต้องจำกัดการตรวจสอบสุขภาพ
def health_check():
    """จุดสิ้นสุดการตรวจสอบสุขภาพสำหรับการตรวจสอบ"""
    health_status = {
        "status": "ok",
        "services": {
            "redis": check_redis_health(),
            "mysql": check_mysql_health(),
            "line_api": check_line_api_health(),
            "together_api": check_together_api_health()
        },
        "uptime": get_uptime(),
        "version": "1.0.0",
        "memory_usage": get_memory_usage()
    }
    
    # ถ้าบริการใดไม่ทำงาน ให้ส่งคืน 503
    if not all(health_status["services"].values()):
        return jsonify(health_status), 503
        
    return jsonify(health_status)

def check_redis_health():
    """ตรวจสอบการเชื่อมต่อ Redis"""
    try:
        return redis_client.ping()
    except Exception:
        return False

def check_mysql_health():
    """ตรวจสอบการเชื่อมต่อ MySQL"""
    try:
        conn = mysql_pool.get_connection()
        conn.close()
        return True
    except Exception:
        return False

def check_line_api_health():
    """ตรวจสอบการเชื่อมต่อ LINE API"""
    try:
        # ตรวจสอบแบบพื้นฐานว่า API พร้อมใช้งาน
        bot_info = line_bot_api.get_bot_info()
        return bool(bot_info.display_name)
    except Exception:
        return False

def check_together_api_health():
    """ตรวจสอบการเชื่อมต่อ Together API"""
    try:
        # Make a minimal API call to check connectivity
        response = together_client.chat.completions.create(
            model=config.TOGETHER_MODEL,
            messages=[{"role": "user", "content": "ping"}],
            max_tokens=1
        )
        return True
    except Exception:
        return False

def get_uptime():
    """ดึงเวลาการทำงานของแอปพลิเคชัน"""
    try:
        with open('/proc/uptime', 'r') as f:
            uptime_seconds = float(f.readline().split()[0])
        
        days, remainder = divmod(uptime_seconds, 86400)
        hours, remainder = divmod(remainder, 3600)
        minutes, seconds = divmod(remainder, 60)
        
        if days > 0:
            return f"{int(days)}d {int(hours)}h {int(minutes)}m"
        elif hours > 0:
            return f"{int(hours)}h {int(minutes)}m {int(seconds)}s"
        else:
            return f"{int(minutes)}m {int(seconds)}s"
    except Exception:
        return "unknown"

def get_memory_usage():
    """ดึงข้อมูลการใช้หน่วยความจำ"""
    try:
        # ใช้ /proc/self/status แทน psutil
        with open('/proc/self/status', 'r') as f:
            for line in f:
                if 'VmRSS:' in line:
                    # แปลงจาก kB เป็น MB
                    memory_kb = int(line.split()[1])
                    return f"{memory_kb / 1024:.2f} MB"
        return "unknown"
    except Exception:
        return "unknown"

# ตัวจัดการเหตุการณ์ 
@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    user_id = event.source.user_id
    user_message = event.message.text

    # ตรวจสอบการล็อค
    if is_user_locked(user_id):
        handle_locked_user(user_id)
        return

    # ล็อคผู้ใช้และประมวลผลข้อความ
    lock_user(user_id)
    try:
        process_user_message(user_id, user_message, event.reply_token)
    finally:
        unlock_user(user_id)

# เริ่มต้นตัวกำหนดการ
scheduler = BackgroundScheduler()

# เพิ่มงานตัวกำหนดการ
def init_scheduler():
    scheduler.add_job(check_and_send_follow_ups, 'interval', minutes=30)
    scheduler.start()
    logging.info("ตัวกำหนดการเริ่มต้นแล้ว ตรวจสอบการติดตามทุก 30 นาที")
    
    # การจัดการการปิดอย่างถูกต้อง
    atexit.register(lambda: scheduler.shutdown())
    
# ตัวจัดการการปิดอย่างสง่างาม
def handle_shutdown(sig, frame):
    logging.info("กำลังปิดแอปพลิเคชัน...")
    scheduler.shutdown()
    # ปิดการเชื่อมต่อ redis
    redis_client.close()
    exit(0)

signal.signal(signal.SIGTERM, handle_shutdown)
signal.signal(signal.SIGINT, handle_shutdown)

if __name__ == "__main__":
    # เริ่มต้นตัวกำหนดการก่อนเริ่มเซิร์ฟเวอร์
    init_scheduler()
    # เริ่มเซิร์ฟเวอร์
    serve(app, host='0.0.0.0', port=5000)