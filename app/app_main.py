"""
แชทบอท 'ใจดี' - แอปพลิเคชันหลัก
โค้ดหลักสำหรับการจัดการข้อความจาก LINE API และการตอบกลับด้วย DeepSeek API
"""
import os
import json
import logging
from logging.handlers import RotatingFileHandler
import requests
import time
import threading
import asyncio
from datetime import datetime, timedelta
from flask import Flask, request, abort, jsonify
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage, FollowEvent
from openai import OpenAI
import redis
from random import choice
import signal
import atexit
from waitress import serve
from apscheduler.schedulers.background import BackgroundScheduler

# นำเข้าโมดูลภายในโปรเจค
from .middleware.rate_limiter import init_limiter
from .config import load_config, SYSTEM_MESSAGES, GENERATION_CONFIG, SUMMARY_GENERATION_CONFIG, TOKEN_THRESHOLD
from .utils import safe_db_operation, safe_api_call, clean_ai_response, handle_deepseek_api_error
from .chat_history_db import ChatHistoryDB
from .token_counter import TokenCounter
from .session_manager import (
    init_session_manager,
    get_chat_session,
    save_chat_session,
    check_session_timeout,
    update_last_activity,
    hybrid_context_management,
    is_important_message,
    get_session_token_count,
)
from .risk_assessment import (
    init_risk_assessment,
    assess_risk,
    save_progress_data,
    generate_progress_report,
)
from .async_api import AsyncDeepseekClient
from .database_init import initialize_database
from .database_manager import DatabaseManager

SESSION_TIMEOUT = 604800

# สร้างอินสแตนซ์แอป Flask
app = Flask(__name__)

# ตั้งค่าการบันทึกข้อมูลและหมุนไฟล์เมื่อขนาดเกิน 5MB
os.makedirs('logs', exist_ok=True)
logging.basicConfig(
    level=getattr(logging, os.getenv('LOG_LEVEL', 'INFO')),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        RotatingFileHandler('logs/app.log', maxBytes=5 * 1024 * 1024, backupCount=3),
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

    # เริ่มต้น DeepSeek client
    deepseek_client = OpenAI(api_key=config.DEEPSEEK_API_KEY, base_url="https://api.deepseek.com")

    # เริ่มต้น Async client สำหรับการประมวลผลเบื้องหลัง
    async_deepseek = AsyncDeepseekClient(config.DEEPSEEK_API_KEY, config.DEEPSEEK_MODEL)
    threading.Thread(target=lambda: asyncio.run(async_deepseek.setup())).start()

    # เริ่มต้นตัวนับโทเค็นที่ปรับปรุงแล้ว
    token_counter = TokenCounter(cache_size=5000)

    # เริ่มต้น DatabaseManager สำหรับการจัดการฐานข้อมูล
    db_config = {
        'MYSQL_HOST': config.MYSQL_HOST,
        'MYSQL_PORT': config.MYSQL_PORT,
        'MYSQL_USER': config.MYSQL_USER,
        'MYSQL_PASSWORD': config.MYSQL_PASSWORD,
        'MYSQL_DB': config.MYSQL_DB
    }

    # สร้าง DatabaseManager ด้วยการตั้งค่าที่เหมาะสม
    db_manager = DatabaseManager(db_config, pool_size=20)

    # เริ่มต้นฐานข้อมูล (สร้างตารางถ้ายังไม่มี)
    initialize_database(db_config)
    logging.info("เสร็จสิ้นการตรวจสอบและเริ่มต้นฐานข้อมูล")

    # เริ่มต้น ChatHistoryDB ด้วย DatabaseManager
    db = ChatHistoryDB(db_manager)

    # ตั้งค่าโมดูลจัดการเซสชันและประเมินความเสี่ยง
    init_session_manager(redis_client, line_bot_api, token_counter, SESSION_TIMEOUT)
    init_risk_assessment(redis_client)

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

def chunk_conversation_history(history, chunk_size=10):
    """
    แบ่งประวัติการสนทนาเป็นส่วนๆ (chunks) เพื่อการสรุปที่มีประสิทธิภาพ

    Args:
        history (list): ประวัติการสนทนา [(id, user_msg, bot_resp), ...]
        chunk_size (int): ขนาดของแต่ละส่วน

    Returns:
        list: รายการของส่วนประวัติการสนทนา
    """
    return [history[i:i + chunk_size] for i in range(0, len(history), chunk_size)]

@safe_api_call
def summarize_conversation_chunk(chunk):
    """
    สรุปส่วนของประวัติการสนทนา

    Args:
        chunk (list): ส่วนของประวัติการสนทนา [(id, user_msg, bot_resp), ...]

    Returns:
        str: ข้อความสรุป
    """
    if not chunk:
        return ""

    try:
        summary_prompt = "นี่คือส่วนของประวัติการสนทนา โปรดสรุปประเด็นสำคัญในส่วนนี้โดยย่อ:\n"
        for _, msg, resp in chunk:
            summary_prompt += f"\nผู้ใช้: {msg}\nบอท: {resp}\n"

        response = deepseek_client.chat.completions.create(
            model=config.DEEPSEEK_MODEL,
            messages=[
                SYSTEM_MESSAGES,
                {"role": "user", "content": summary_prompt}
            ],
            **SUMMARY_GENERATION_CONFIG
        )

        return response.choices[0].message.content
    except Exception as e:
        logging.error(f"เกิดข้อผิดพลาดใน summarize_conversation_chunk: {str(e)}")
        return ""

def process_and_optimize_history(user_id, max_tokens=85000):
    """
    ประมวลผลและปรับปรุงประวัติการสนทนาให้เหมาะสมที่สุด
    รวมการสรุปเป็นชั้นๆ และการจัดลำดับความสำคัญ

    Args:
        user_id (str): LINE User ID
        max_tokens (int): จำนวนโทเค็นสูงสุดที่ต้องการใช้

    Returns:
        list: ประวัติการสนทนาที่ปรับปรุงแล้ว
    """
    try:
        # 1. ตรวจสอบโทเค็นในเซสชันปัจจุบัน
        session_tokens = get_session_token_count(user_id)
        if session_tokens < max_tokens:
            # ถ้ายังอยู่ในเกณฑ์ ส่งคืนประวัติทั้งหมด
            return get_chat_session(user_id)

        # 2. ดึงประวัติจากฐานข้อมูลและเซสชัน
        db_history = db.get_user_history(user_id, max_tokens=max_tokens)
        session_history = get_chat_session(user_id)

        # 3. ระบุข้อความสำคัญ
        important_messages = []

        # แยกข้อความสำคัญจาก session history
        for i in range(0, len(session_history), 2):
            if i+1 < len(session_history):
                user_msg = session_history[i].get("content", "")
                bot_resp = session_history[i+1].get("content", "")

                if is_important_message(user_msg, bot_resp):
                    important_messages.append({"role": "user", "content": user_msg})
                    important_messages.append({"role": "assistant", "content": bot_resp})

        # 4. เก็บข้อความล่าสุด
        recent_count = min(20, len(session_history) // 2)  # จำนวนการโต้ตอบล่าสุด (ไม่เกิน 20)
        recent_messages = session_history[-recent_count*2:]  # *2 เพราะแต่ละการโต้ตอบมี 2 ข้อความ

        # 5. สรุปข้อความที่เหลือจาก db_history
        # แบ่งเป็นส่วนๆ เพื่อประสิทธิภาพในการสรุป
        chunks = chunk_conversation_history(db_history, chunk_size=10)
        summaries = []

        for chunk in chunks:
            summary = summarize_conversation_chunk(chunk)
            if summary:
                summaries.append(summary)

        # 6. รวมประวัติทั้งหมด
        optimized_history = []

        # เพิ่มสรุปทั้งหมด
        if summaries:
            combined_summary = "\n\n".join(summaries)
            optimized_history.append({"role": "assistant", "content": f"สรุปการสนทนาก่อนหน้า: {combined_summary}"})

        # เพิ่มข้อความสำคัญ
        optimized_history.extend(important_messages)

        # เพิ่มข้อความล่าสุด
        for msg in recent_messages:
            # ตรวจสอบว่าไม่ซ้ำกับข้อความสำคัญ
            if msg not in important_messages:
                optimized_history.append(msg)

        # 7. บันทึกประวัติที่ปรับปรุงแล้ว
        save_chat_session(user_id, optimized_history)

        return optimized_history

    except Exception as e:
        logging.error(f"เกิดข้อผิดพลาดในการปรับปรุงประวัติ: {str(e)}")
        return get_chat_session(user_id)  # ส่งคืนประวัติปกติในกรณีที่มีข้อผิดพลาด

@safe_api_call
def summarize_conversation_history(history):
    """
    สรุปประวัติการสนทนาให้กระชับ โดยมีการจัดการขนาด

    Args:
        history (list): รายการประวัติการสนทนา [(id, user_msg, bot_resp), ...]

    Returns:
        str: ข้อความสรุป
    """
    if not history:
        return ""

    try:
        # แบ่งประวัติเป็นส่วนๆ หากมีขนาดใหญ่
        if len(history) > 20:
            # แบ่งเป็นชิ้นและสรุปแต่ละชิ้น
            chunks = chunk_conversation_history(history, chunk_size=10)
            summaries = []

            for chunk in chunks:
                chunk_summary = summarize_conversation_chunk(chunk)
                if chunk_summary:
                    summaries.append(chunk_summary)

            # รวมสรุปทั้งหมด
            if summaries:
                combined_summary = "\n".join([f"• {summary}" for summary in summaries])
                return combined_summary

        # หากมีขนาดเล็ก ใช้วิธีสรุปแบบปกติ
        summary_prompt = "นี่คือประวัติการสนทนา โปรดสรุปประเด็นสำคัญในประวัติการสนทนานี้:\n"
        for _, msg, resp in history:
            summary_prompt += f"\nผู้ใช้: {msg}\nบอท: {resp}\n"

        response = deepseek_client.chat.completions.create(
            model=config.DEEPSEEK_MODEL,
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

@safe_api_call
def summarize_by_topic(history):
    """
    สรุปประวัติการสนทนาแบ่งตามหัวข้อ
    เหมาะสำหรับการสนทนาที่มีหลายหัวข้อคละกัน

    Args:
        history (list): รายการประวัติการสนทนา [(id, user_msg, bot_resp), ...]

    Returns:
        str: ข้อความสรุปแบ่งตามหัวข้อ
    """
    if not history:
        return ""

    try:
        # สร้างข้อความเพื่อให้ AI แบ่งหัวข้อและสรุป
        topic_prompt = """
นี่คือประวัติการสนทนาระหว่างผู้ใช้และบอทเกี่ยวกับการเลิกสารเสพติด:

{conversation}

โปรดวิเคราะห์และแบ่งแยกหัวข้อสำคัญต่างๆ ในการสนทนานี้ พร้อมทั้งสรุปแต่ละหัวข้อ ตามรูปแบบนี้:
1. [ชื่อหัวข้อ 1]: [สรุปสั้นๆ]
2. [ชื่อหัวข้อ 2]: [สรุปสั้นๆ]
...

แต่ละหัวข้อควรครอบคลุมประเด็นสำคัญที่พูดถึงโดยมีใจความชัดเจน กระชับ และเก็บรายละเอียดสำคัญไว้
"""

        # สร้างเนื้อหาการสนทนาสำหรับใส่ใน prompt
        conversation_text = ""
        for _, msg, resp in history:
            conversation_text += f"ผู้ใช้: {msg}\nบอท: {resp}\n\n"

        # นำเนื้อหาการสนทนาใส่ใน prompt
        topic_prompt = topic_prompt.format(conversation=conversation_text)

        # ส่งไปให้ AI ประมวลผล
        response = deepseek_client.chat.completions.create(
            model=config.DEEPSEEK_MODEL,
            messages=[
                SYSTEM_MESSAGES,
                {"role": "user", "content": topic_prompt}
            ],
            temperature=0.2,  # ลดความสร้างสรรค์เพื่อให้ได้ผลลัพธ์ที่เป็นระเบียบ
            max_tokens=800
        )

        return response.choices[0].message.content
    except Exception as e:
        logging.error(f"เกิดข้อผิดพลาดใน summarize_by_topic: {str(e)}")
        return ""


def is_user_registered(user_id):
    """ตรวจสอบว่าผู้ใช้ลงทะเบียนแล้วหรือไม่"""
    try:
        query = 'SELECT EXISTS(SELECT 1 FROM registration_codes WHERE user_id = %s AND status = %s)'
        result = db_manager.execute_query(query, (user_id, 'verified'))
        return bool(result[0][0]) if result else False
    except Exception as e:
        logging.error(f"Error checking user registration: {str(e)}")
        return False

def register_user_with_code(user_id, code):
    """ยืนยันการลงทะเบียนด้วยรหัสยืนยัน"""
    try:
        # ตรวจสอบว่ารหัสมีอยู่และยังไม่หมดอายุ
        query = 'SELECT code FROM registration_codes WHERE code = %s AND status = %s'
        result = db_manager.execute_query(query, (code, 'pending'))

        if not result:
            return False, "รหัสยืนยันไม่ถูกต้องหรือหมดอายุแล้ว"

        # อัพเดทรหัสให้เชื่อมกับผู้ใช้และสถานะเป็น verified
        update_query = 'UPDATE registration_codes SET user_id = %s, status = %s, verified_at = %s WHERE code = %s'
        db_manager.execute_and_commit(update_query, (user_id, 'verified', datetime.now(), code))

        return True, "ลงทะเบียนเรียบร้อยแล้ว! คุณสามารถใช้งานแชทบอทได้ทันที"
    except Exception as e:
        logging.error(f"เกิดข้อผิดพลาดในการลงทะเบียน: {str(e)}")
        return False, "เกิดข้อผิดพลาดในการลงทะเบียน กรุณาลองอีกครั้ง"

def send_registration_message(user_id):
    """ส่งข้อความแนะนำการลงทะเบียน"""
    register_message = (
        "สวัสดีค่ะ! ยินดีต้อนรับสู่แชทบอท 'ใจดี'\n\n"
        "เพื่อเริ่มใช้งาน คุณจำเป็นต้องลงทะเบียนก่อน โดยทำตามขั้นตอนดังนี้:\n\n"
        "1. กรอกแบบฟอร์มที่ลิงก์นี้: https://forms.gle/gVE6WN7W5thHR1kZ9\n"
        "2. หลังกรอกเสร็จ คุณจะได้รับรหัสยืนยัน 6 หลัก\n"
        "3. นำรหัสมาพิมพ์ที่นี่ด้วยคำสั่ง \"/verify รหัส\" เช่น \"/verify 123456\"\n\n"
        "หากมีข้อสงสัย พิมพ์ /help เพื่อดูคำแนะนำ"
    )

    line_bot_api.push_message(
        user_id,
        TextSendMessage(text=register_message)
    )

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
def schedule_follow_up(user_id, interaction_date=None):
    """
    จัดการการติดตามผู้ใช้ โดยอ้างอิงจากข้อความแรกสุด
    ไม่รีเซ็ตเวลาหลังจากส่งข้อความใหม่

    Args:
        user_id (str): LINE User ID
        interaction_date (datetime, optional): วันที่ปฏิสัมพันธ์ (ถ้าไม่ระบุจะหาจากฐานข้อมูล)
    """
    try:
        # หาวันที่ของข้อความแรกสุด (ถ้าไม่ได้ระบุมา)
        if interaction_date is None:
            # ตรวจสอบว่ามีการเก็บเวลาเริ่มต้นไว้ใน Redis หรือไม่
            first_interaction_time = redis_client.get(f"first_interaction:{user_id}")

            if first_interaction_time:
                try:
                    # แปลงจาก string หรือ bytes เป็น float และจาก float เป็น datetime
                    if isinstance(first_interaction_time, bytes):
                        first_interaction_time = first_interaction_time.decode('utf-8')
                    interaction_date = datetime.fromtimestamp(float(first_interaction_time))
                except (ValueError, TypeError) as e:
                    logging.warning(f"ข้อมูลเวลาเริ่มต้นใน Redis ไม่ถูกต้อง: {str(e)}")
                    interaction_date = None

            # ถ้ายังไม่มีเวลาเริ่มต้นที่ถูกต้อง ให้ดึงจากฐานข้อมูล
            if interaction_date is None:
                try:
                    # ใช้ DatabaseManager เพื่อดึงข้อมูล
                    query = 'SELECT MIN(timestamp) FROM conversations WHERE user_id = %s'
                    result = db_manager.execute_query(query, (user_id,))
                    first_timestamp = result[0][0] if result and result[0] else None

                    if first_timestamp:
                        interaction_date = first_timestamp
                        # เก็บเวลาเริ่มต้นลง Redis เพื่อใช้อ้างอิงในอนาคต (ไม่มีเวลาหมดอายุ)
                        redis_client.set(
                            f"first_interaction:{user_id}",
                            interaction_date.timestamp()
                        )
                    else:
                        # ถ้าไม่มีข้อมูลในฐานข้อมูล ใช้เวลาปัจจุบัน
                        interaction_date = datetime.now()
                        # เก็บเวลาเริ่มต้นลง Redis
                        redis_client.set(
                            f"first_interaction:{user_id}",
                            interaction_date.timestamp()
                        )
                except Exception as db_error:
                    logging.error(f"เกิดข้อผิดพลาดในการดึงข้อมูลจากฐานข้อมูล: {str(db_error)}")
                    interaction_date = datetime.now()

        # ตรวจสอบว่า interaction_date เป็นประเภท datetime
        if not isinstance(interaction_date, datetime):
            logging.warning(f"ค่า interaction_date ไม่ใช่ประเภท datetime ใช้เวลาปัจจุบันแทน")
            interaction_date = datetime.now()

        # บันทึกข้อมูลวันที่เริ่มต้นลงใน Redis (ถ้ายังไม่มี)
        redis_client.setnx(f"first_interaction:{user_id}", interaction_date.timestamp())

        # ถ้ามีการกำหนดการติดตามไว้แล้วและยังไม่ถึงกำหนด ให้ใช้อันเดิม
        existing_ts = redis_client.zscore('follow_up_queue', user_id)
        if existing_ts:
            try:
                existing_dt = datetime.fromtimestamp(float(existing_ts))
                if existing_dt > datetime.now():
                    logging.info(
                        f"มีการกำหนดการติดตามไว้แล้วสำหรับผู้ใช้ {user_id} ในวันที่ {existing_dt.strftime('%Y-%m-%d')}"
                    )
                    return
            except (ValueError, TypeError) as e:
                logging.warning(f"ข้อมูลกำหนดการติดตามไม่ถูกต้อง: {str(e)}")

        # ดึงข้อมูลการติดตามล่าสุด (ถ้ามี)
        last_follow_up = redis_client.get(f"last_follow_up:{user_id}")
        next_follow_idx = 0

        if last_follow_up:
            # แปลงจาก bytes เป็น string ถ้าจำเป็น
            if isinstance(last_follow_up, bytes):
                last_follow_up = last_follow_up.decode('utf-8')

            # หาดัชนีถัดไปใน FOLLOW_UP_INTERVALS
            try:
                last_idx = FOLLOW_UP_INTERVALS.index(int(last_follow_up))
                next_follow_idx = last_idx + 1
                # ถ้าเกินขอบเขต ให้ใช้วันสุดท้าย
                if next_follow_idx >= len(FOLLOW_UP_INTERVALS):
                    next_follow_idx = len(FOLLOW_UP_INTERVALS) - 1
            except (ValueError, IndexError):
                # ถ้าไม่พบค่าใน FOLLOW_UP_INTERVALS หรือเกิดข้อผิดพลาด ให้เริ่มจาก 0
                next_follow_idx = 0

        # กำหนดการติดตามตามช่วงเวลาที่กำหนด
        current_date = datetime.now()
        scheduled = False

        # ลูปเริ่มจากดัชนีที่คำนวณได้ (ไม่ใช่ตั้งแต่ดัชนี 0 เสมอ)
        for i in range(next_follow_idx, len(FOLLOW_UP_INTERVALS)):
            days = FOLLOW_UP_INTERVALS[i]
            follow_up_date = interaction_date + timedelta(days=days)

            # กำหนดการติดตามสำหรับวันที่ในอนาคตเท่านั้น
            if follow_up_date > current_date:
                redis_client.zadd(
                    'follow_up_queue',
                    {user_id: follow_up_date.timestamp()}
                )
                # บันทึกว่าการติดตามล่าสุดคือวันที่เท่าไร
                redis_client.set(f"last_follow_up:{user_id}", str(days))

                logging.info(f"กำหนดการติดตามผู้ใช้ {user_id} ในวันที่ {follow_up_date.strftime('%Y-%m-%d')} (+{days} วัน จากวันแรก)")
                scheduled = True
                break

        if not scheduled:
            logging.info(f"ไม่ได้กำหนดการติดตามสำหรับผู้ใช้ {user_id} เนื่องจากไม่มีวันที่ในอนาคตที่เข้าเกณฑ์")

    except Exception as e:
        logging.error(f"เกิดข้อผิดพลาดในการกำหนดการติดตามผล: {str(e)}")

def get_follow_up_status(user_id):
    """คืนค่าข้อมูลกำหนดการติดตามของผู้ใช้"""
    try:
        timestamp = redis_client.zscore('follow_up_queue', user_id)
        if timestamp:
            next_dt = datetime.fromtimestamp(float(timestamp))
            date_text = next_dt.strftime("%d/%m/%Y %H:%M")
            delta = next_dt - datetime.now()
            if delta.total_seconds() < 0:
                delta = timedelta(0)
            days = delta.days
            hours, rem = divmod(delta.seconds, 3600)
            minutes = rem // 60
            time_text = f"อีก {days} วัน {hours} ชั่วโมง {minutes} นาที"
        else:
            time_text = "ยังไม่ได้กำหนดการติดตามครั้งถัดไป"
            date_text = "-"

        last_follow = redis_client.get(f"last_follow_up:{user_id}")
        start_idx = 0
        if last_follow:
            try:
                start_idx = FOLLOW_UP_INTERVALS.index(int(last_follow)) + 1
            except ValueError:
                start_idx = 0
        remaining = FOLLOW_UP_INTERVALS[start_idx:]
        remaining_text = ",".join(str(d) for d in remaining) if remaining else "หมดแล้ว"

        return (
            f"📆 กำหนดการติดตามครั้งถัดไป: {date_text}\n"
            f"⏰ การติดตามครั้งถัดไปจะเริ่มใน {time_text}\n"
            f"📅 รอบติดตามที่เหลือ: {remaining_text}"
        )
    except Exception as e:
        logging.error(f"เกิดข้อผิดพลาดในการดึงสถานะการติดตามผล: {str(e)}")
        return "ไม่สามารถดึงข้อมูลการติดตามได้ในขณะนี้"

def check_and_send_follow_ups():
    """ตรวจสอบและส่งการติดตามที่ถึงกำหนด พร้อมกำหนดการติดตามครั้งถัดไป"""
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

                # กำหนดการติดตามครั้งถัดไปโดยอัตโนมัติ
                # ส่งค่า None เพื่อให้ใช้วันที่เริ่มต้นจาก Redis
                schedule_follow_up(user_id, None)

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

def process_conversation_data(user_id, user_message, bot_response, messages):
    """
    ประมวลผลและบันทึกข้อมูลการสนทนา พร้อมกับตรวจสอบความเสี่ยง

    Args:
        user_id (str): LINE User ID
        user_message (str): ข้อความของผู้ใช้
        bot_response (str): การตอบกลับของบอท
        messages (list): ข้อความทั้งหมดในเซสชัน
    """
    # นับโทเค็นสำหรับการสนทนาคู่นี้
    message_token_count = token_counter.count_tokens(user_message + bot_response)

    # ประเมินความเสี่ยง
    risk_level, keywords = assess_risk(user_message)
    save_progress_data(user_id, risk_level, keywords)

    # ตรวจสอบว่าข้อความนี้สำคัญหรือไม่
    is_important = is_important_message(user_message, bot_response)

    # บันทึกการสนทนาและกำหนดการติดตาม
    save_chat_session(user_id, messages)
    db.save_conversation(
        user_id=user_id,
        user_message=user_message,
        bot_response=bot_response,
        token_count=message_token_count,  # บันทึกเฉพาะโทเค็นของข้อความคู่นี้
        important=is_important
    )

    # กำหนดการติดตามโดยยึดวันแรกที่ผู้ใช้เริ่มสนทนา
    # ถ้ามีการกำหนดการติดตามค้างอยู่จะไม่ถูกปรับใหม่
    schedule_follow_up(user_id, None)

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

    # ตรวจสอบโทเค็นและแจ้งเตือนถ้าเข้าใกล้ขีดจำกัด
    session_token_count = get_session_token_count(user_id)
    token_threshold_warning = TOKEN_THRESHOLD * 0.70  # แจ้งเตือนที่ 70% ของขีดจำกัด

    if session_token_count > token_threshold_warning and not redis_client.exists(f"token_warning:{user_id}"):
        # ส่งการแจ้งเตือนเรื่องโทเค็น
        warning_message = (
            "📊 ข้อควรทราบ: ประวัติการสนทนาของเรากำลังเติบโต ระบบอาจจะต้องสรุปบางส่วน"
            "ในการสนทนาต่อไปเพื่อรักษาประสิทธิภาพ\n\n"
            f"• โทเค็นในเซสชันปัจจุบัน: {session_token_count:,} จาก {TOKEN_THRESHOLD:,} ({(session_token_count/TOKEN_THRESHOLD*100):.1f}%)\n"
            "• คุณสามารถใช้คำสั่ง /optimize เพื่อปรับปรุงประวัติการสนทนาได้ทุกเมื่อ"
        )

        # ตั้งค่าเวลาหมดอายุของการแจ้งเตือน (30 นาที)
        redis_client.setex(f"token_warning:{user_id}", 1800, "1")

        # ส่งข้อความแจ้งเตือนหลังจากการตอบกลับปกติเล็กน้อย
        def send_delayed_warning():
            time.sleep(3)  # รอ 3 วินาทีหลังจากส่งการตอบกลับปกติ
            send_final_response(user_id, warning_message)

        # เริ่ม thread ใหม่เพื่อส่งการแจ้งเตือนแบบหน่วงเวลา
        warning_thread = threading.Thread(target=send_delayed_warning)
        warning_thread.daemon = True
        warning_thread.start()

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

def process_ai_response(user_id, user_message, start_time, animation_success):
    """สร้างการตอบกลับ AI และจัดการผลลัพธ์"""
    try:
        # ตรวจสอบและปรับประวัติการสนทนาโดยใช้การจัดการแบบไฮบริด
        session_token_count = get_session_token_count(user_id)
        logging.info(f"จำนวนโทเค็นปัจจุบันในเซสชัน: {session_token_count} (ผู้ใช้: {user_id})")

        # ถ้าโทเค็นเกินขีดจำกัด ใช้ระบบไฮบริด
        if session_token_count > TOKEN_THRESHOLD:
            logging.info(f"จำนวนโทเค็นเกินขีดจำกัด ({session_token_count} > {TOKEN_THRESHOLD}), กำลังใช้ระบบจัดการประวัติแบบไฮบริด")
            messages = hybrid_context_management(user_id)
        else:
            # ดึงเซสชันการแชทและประวัติปกติ
            messages = get_chat_session(user_id)

            # ประมวลผลประวัติและสร้างการตอบกลับ
            optimized_history = db.get_user_history(user_id, max_tokens=10000)
            prepare_conversation_context(messages, optimized_history)

        # เพิ่มข้อความของผู้ใช้
        messages.append({"role": "user", "content": user_message})

        # รับการตอบกลับจาก DeepSeek พร้อมการจัดการข้อผิดพลาด
        try:
            response = generate_ai_response(messages)

            # ตรวจสอบการตอบกลับ
            if not response or not hasattr(response, 'choices') or not response.choices:
                raise ValueError("ได้รับการตอบกลับที่ไม่ถูกต้องจาก AI API")

            # ดึงและทำความสะอาดข้อความตอบกลับ
            original_bot_response = response.choices[0].message.content

            # ขั้นตอนการทำความสะอาด response
            bot_response = clean_ai_response(original_bot_response)

            # บันทึก log หากมีการทำความสะอาด
            if original_bot_response != bot_response:
                logging.info(f"ทำความสะอาด response จาก DeepSeek AI สำหรับผู้ใช้: {user_id}")

        except Exception as api_error:
            # จัดการกับข้อผิดพลาดการเรียก API
            logging.error(f"เกิดข้อผิดพลาดในการเรียก DeepSeek API: {str(api_error)}")
            bot_response = handle_deepseek_api_error(api_error, user_id, user_message)

        # เพิ่มข้อความตอบกลับลงในประวัติการสนทนา
        messages.append({"role": "assistant", "content": bot_response})

        # ประมวลผลข้อมูลการตอบกลับ
        process_conversation_data(user_id, user_message, bot_response, messages)

        # จัดการจังหวะเวลาสำหรับ UX ที่ดีขึ้น
        handle_response_timing(start_time, animation_success)

        # ส่งการตอบกลับสุดท้าย
        send_final_response(user_id, bot_response)

        # บันทึกเวลาประมวลผลทั้งหมด
        total_time = time.time() - start_time
        logging.info(f"เวลาในการประมวลผลทั้งหมดสำหรับผู้ใช้ {user_id}: {total_time:.2f} วินาที")

    except Exception as e:
        logging.error(f"เกิดข้อผิดพลาดในการประมวลผล AI: {str(e)}", exc_info=True)
        error_message = "ขออภัยค่ะ เกิดข้อผิดพลาดในการประมวลผล กรุณาลองใหม่อีกครั้ง"
        send_final_response(user_id, error_message)


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

def handle_command_with_processing(user_id, command):
    """จัดการคำสั่งพร้อมแสดงสถานะประมวลผล"""

    # ตรวจสอบคำสั่ง verify
    if command.startswith('/verify'):
        # ตรวจสอบว่าผู้ใช้ลงทะเบียนแล้วหรือไม่
        if is_user_registered(user_id):
            send_final_response(
                user_id,
                "✅ คุณได้ลงทะเบียนและยืนยันตัวตนเรียบร้อยแล้ว\n"
                "ไม่จำเป็นต้องยืนยันอีกครั้ง คุณสามารถใช้บริการของน้องใจดีได้ตามปกติ\n\n"
                "พิมพ์ /help เพื่อดูคำสั่งและบริการที่มี"
            )
            return

        # ดำเนินการต่อสำหรับผู้ที่ยังไม่ได้ลงทะเบียน
        parts = command.split()
        if len(parts) != 2:
            send_final_response(user_id, "รูปแบบไม่ถูกต้อง กรุณาพิมพ์ \"/verify\" ตามด้วยรหัส 6 หลัก เช่น \"/verify 123456\"")
            return

        confirmation_code = parts[1].strip()
        success, message = register_user_with_code(user_id, confirmation_code)
        send_final_response(user_id, message)
        return

    animation_success, _ = start_loading_animation(user_id, duration=10)

    response_text = None

    if command == '/reset':
        db.clear_user_history(user_id)
        redis_client.delete(f"chat_session:{user_id}")
        redis_client.delete(f"session_tokens:{user_id}")
        redis_client.zrem('follow_up_queue', user_id)
        redis_client.delete(f"last_follow_up:{user_id}")
        redis_client.delete(f"first_interaction:{user_id}")
        response_text = (
            "🔄 ล้างประวัติการสนทนาเรียบร้อยแล้วค่ะ\n\n"
            "เราสามารถเริ่มต้นการสนทนาใหม่ได้ทันที\n"
            "คุณต้องการพูดคุยเกี่ยวกับเรื่องอะไรดีคะ?"
        )

    elif command == '/optimize':
        # เพิ่มคำสั่งใหม่สำหรับการปรับประวัติการสนทนาโดยตรง
        token_count_before = get_session_token_count(user_id)
        hybrid_context_management(user_id)
        token_count_after = get_session_token_count(user_id)

        response_text = (
            f"🔄 ปรับปรุงประวัติการสนทนาเรียบร้อยแล้วค่ะ\n\n"
            f"จำนวนโทเค็น: {token_count_before} → {token_count_after} ({(token_count_before - token_count_after)} ลดลง)\n\n"
            f"ประวัติการสนทนาสำคัญยังคงถูกเก็บไว้ และบอทยังเข้าใจบริบทการสนทนาของเรา\n"
            f"เราสามารถสนทนาต่อได้ตามปกติค่ะ"
        )

    elif command == '/tokens':
        # เพิ่มคำสั่งสำหรับตรวจสอบจำนวนโทเค็นในเซสชัน
        token_count = get_session_token_count(user_id)
        max_tokens = TOKEN_THRESHOLD
        percentage = (token_count / max_tokens) * 100

        response_text = (
            f"📊 สถิติการใช้โทเค็น\n\n"
            f"โทเค็นในเซสชันปัจจุบัน: {token_count:,}\n"
            f"ขีดจำกัด: {max_tokens:,}\n"
            f"เปอร์เซ็นต์การใช้งาน: {percentage:.1f}%\n\n"
            f"{'⚠️ ใกล้ถึงขีดจำกัด โปรดใช้ /optimize เพื่อปรับปรุงประวัติ' if percentage > 80 else '✅ อยู่ในเกณฑ์ปกติ'}"
        )

    elif command == '/followup':
        response_text = get_follow_up_status(user_id)

    elif command == '/help':
        response_text = (
            "สวัสดีค่ะ 👋 ฉันคือน้องใจดี ผู้ช่วยดูแลและให้คำปรึกษาสำหรับผู้ที่ต้องการเลิกใช้สารเสพติด\n\n"
            "💬 ฉันสามารถช่วยคุณได้ดังนี้:\n"
            "- พูดคุยและให้กำลังใจในการเลิกใช้สารเสพติด\n"
            "- ให้ข้อมูลเกี่ยวกับผลกระทบของสารเสพติดต่อร่างกายและจิตใจ\n"
            "- แนะนำเทคนิคจัดการความอยากและความเครียด\n"
            "- เชื่อมต่อกับบริการช่วยเหลือในกรณีฉุกเฉิน\n\n"
            "📋 คำสั่งที่ใช้ได้:\n"
            "🔑 /verify [รหัส] - ยืนยันการลงทะเบียนด้วยรหัสที่ได้จาก Google Form\n"
            "📝 /register - ขอข้อมูลการลงทะเบียนและลิงก์กรอกแบบฟอร์ม\n"
            "📊 /status - ดูสถิติการใช้งานและข้อมูลเซสชัน\n"
            "📈 /progress - ดูรายงานความก้าวหน้าของคุณ\n"
            "🔄 /optimize - ปรับปรุงประวัติการสนทนาให้มีประสิทธิภาพ\n"
            "📈 /tokens - ตรวจสอบการใช้งานโทเค็นในเซสชันปัจจุบัน\n"
            "🔔 /followup - ตรวจสอบกำหนดการติดตามของคุณ\n"
            "🚨 /emergency - ดูข้อมูลติดต่อฉุกเฉินและสายด่วน\n"
            "🔄 /reset - ล้างประวัติการสนทนาและเริ่มต้นใหม่\n"
            "❓ /help - แสดงเมนูช่วยเหลือนี้\n\n"
            "💡 ตัวอย่างคำถามที่สามารถถามฉันได้:\n"
            "- \"ช่วยประเมินการใช้สารเสพติดของฉันหน่อย\"\n"
            "- \"ผลกระทบของยาบ้าต่อร่างกายมีอะไรบ้าง\"\n"
            "- \"มีเทคนิคจัดการความอยากยาอย่างไร\"\n"
            "- \"ฉันควรทำอย่างไรเมื่อรู้สึกอยากกลับไปใช้สารอีก\"\n\n"
            "เริ่มพูดคุยกับฉันได้เลยนะคะ ฉันพร้อมรับฟังและช่วยเหลือคุณ 💚"
        )

    elif command == '/status':
        history_count = db.get_user_history_count(user_id)
        important_count = db.get_important_message_count(user_id)
        last_interaction = db.get_last_interaction(user_id)
        current_session = redis_client.exists(f"chat_session:{user_id}") == 1
        total_db_tokens = db.get_total_tokens(user_id) or 0
        session_tokens = get_session_token_count(user_id)

        # อัพเดทข้อความสถานะพร้อมตัวเลขสำคัญ และชี้แจงความแตกต่าง
        response_text = (
            "📊 สถิติการสนทนาของคุณ\n"
            f"▫️ จำนวนการสนทนาที่บันทึก: {history_count} ครั้ง\n"
            f"▫️ ประเด็นสำคัญที่พูดคุย: {important_count} รายการ\n"
            f"▫️ สนทนาล่าสุดเมื่อ: {last_interaction}\n"
            f"▫️ สถานะเซสชันปัจจุบัน: {'🟢 กำลังสนทนาอยู่' if current_session else '🔴 ยังไม่เริ่มสนทนา'}\n\n"
            f"📝 สถิติโทเค็น\n"
            f"▫️ โทเค็นในเซสชันปัจจุบัน: {session_tokens:,}\n"
            f"  (รวมทุกข้อความในบริบทปัจจุบัน)\n"
            f"▫️ โทเค็นในฐานข้อมูล: {total_db_tokens:,}\n"
            f"  (ผลรวมของแต่ละข้อความที่บันทึก)\n\n"
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

    elif command == '/progress':
        report = generate_progress_report(user_id)
        response_text = report if report else (
            "📊 รายงานความก้าวหน้า\n\n"
            "ยังไม่มีข้อมูลความก้าวหน้าเพียงพอสำหรับการวิเคราะห์\n\n"
            "เมื่อเราพูดคุยกันมากขึ้น น้องใจดีจะสามารถติดตามและวิเคราะห์ความก้าวหน้าของคุณได้"
        )

    elif command == '/register':
        response_text = (
            "📝 การลงทะเบียนใช้งานน้องใจดี\n\n"
            "เพื่อเริ่มใช้งาน คุณจำเป็นต้องลงทะเบียนก่อน โดยทำตามขั้นตอนดังนี้:\n\n"
            "1. กรอกแบบฟอร์มที่ลิงก์นี้: https://forms.gle/gVE6WN7W5thHR1kZ9\n"
            "2. หลังกรอกเสร็จ คุณจะได้รับรหัสยืนยัน 6 หลัก\n"
            "3. นำรหัสมาพิมพ์ที่นี่ด้วยคำสั่ง \"/verify รหัส\" เช่น \"/verify 123456\"\n\n"
            "หากมีปัญหาในการลงทะเบียน คุณสามารถติดต่อเจ้าหน้าที่ได้ที่ support@example.com"
        )

    else:
        response_text = "คำสั่งไม่ถูกต้อง ลองพิมพ์ /help เพื่อดูคำสั่งทั้งหมด"

    if response_text:
        send_final_response(user_id, response_text)

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

@safe_api_call
def generate_ai_response(messages):
    """สร้างการตอบกลับด้วย AI โดยมีการจัดการข้อผิดพลาด"""
    try:
        response = deepseek_client.chat.completions.create(
            model=config.DEEPSEEK_MODEL,
            messages=[SYSTEM_MESSAGES] + messages,
            **GENERATION_CONFIG
        )

        # ตรวจสอบการตอบกลับเบื้องต้น
        if not response or not hasattr(response, 'choices') or not response.choices:
            logging.error("ได้รับการตอบกลับที่ไม่ถูกต้องจาก DeepSeek API")
            raise ValueError("Invalid response from DeepSeek API")

        return response

    except Exception as e:
        logging.error(f"เกิดข้อผิดพลาดในการสร้างการตอบกลับ AI: {str(e)}")
        raise

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

@app.route("/api/add-verification-code", methods=['POST'])
@limiter.exempt
def add_verification_code():
    """API endpoint รับรหัสยืนยันจาก Google Apps Script"""

    # ตรวจสอบการรับรอง API key
    api_key = request.json.get('api_key', '')
    if api_key != os.getenv('FORM_WEBHOOK_KEY', 'your_secret_key_here'):
        return jsonify({"success": False, "error": "Unauthorized"}), 401

    # รับรหัสยืนยันจาก request
    code = request.json.get('code', '')
    if not code or not code.isdigit() or len(code) != 6:
        return jsonify({"success": False, "error": "Invalid verification code"}), 400

    # บันทึกรหัสลงฐานข้อมูล
    try:
        # ตรวจสอบว่ารหัสมีอยู่แล้วหรือไม่
        check_query = 'SELECT code FROM registration_codes WHERE code = %s'
        result = db_manager.execute_query(check_query, (code,))

        if result and result[0]:
            return jsonify({"success": False, "error": "Code already exists"}), 409

        # บันทึกรหัสใหม่
        insert_query = 'INSERT INTO registration_codes (code, created_at, status) VALUES (%s, %s, %s)'
        db_manager.execute_and_commit(insert_query, (code, datetime.now(), 'pending'))

        logging.info(f"บันทึกรหัสยืนยันใหม่: {code}")
        return jsonify({"success": True, "message": "Verification code added successfully"}), 201

    except Exception as e:
        logging.error(f"เกิดข้อผิดพลาดในการบันทึกรหัสยืนยัน: {str(e)}")
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/admin/sync_tokens/<user_id>", methods=['POST'])
@limiter.exempt  # ยกเว้นการจำกัดอัตรา
def sync_tokens(user_id):
    """
    จุดสิ้นสุดสำหรับผู้ดูแลระบบเพื่อซิงค์โทเค็นระหว่างเซสชันและฐานข้อมูล
    เพิ่มความสอดคล้องของข้อมูลโทเค็น
    """
    if not request.headers.get('X-Admin-Key') == os.getenv('ADMIN_SECRET_KEY'):
        return jsonify({"error": "Unauthorized"}), 401

    try:
        # คำนวณโทเค็นทั้งหมดจากฐานข้อมูล
        total_db_tokens = db.get_total_tokens(user_id) or 0

        # คำนวณโทเค็นในเซสชันปัจจุบัน
        session_tokens = get_session_token_count(user_id)

        # สร้างบันทึกพิเศษในฐานข้อมูลเพื่อปรับปรุงความแตกต่าง
        if session_tokens > total_db_tokens:
            diff = session_tokens - total_db_tokens
            db.save_conversation(
                user_id=user_id,
                user_message="[TOKEN SYNC]",
                bot_response="[ปรับปรุงความสอดคล้องของโทเค็น]",
                token_count=diff,
                important=False
            )
            status = "โทเค็นในฐานข้อมูลเพิ่มขึ้น"
        else:
            status = "ไม่จำเป็นต้องปรับปรุง"

        return jsonify({
            "status": status,
            "before": {
                "session_tokens": session_tokens,
                "db_tokens": total_db_tokens
            },
            "after": {
                "session_tokens": session_tokens,
                "db_tokens": db.get_total_tokens(user_id) or 0
            }
        })
    except Exception as e:
        logging.error(f"เกิดข้อผิดพลาดในการซิงค์โทเค็น: {str(e)}")
        return jsonify({"error": str(e)}), 500

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
            "deepseek_api": check_deepseek_api_health()
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
        return db_manager.check_connection()
    except Exception as e:
        logging.error(f"MySQL health check failed: {str(e)}")
        return False

def check_line_api_health():
    """ตรวจสอบการเชื่อมต่อ LINE API"""
    try:
        # ตรวจสอบแบบพื้นฐานว่า API พร้อมใช้งาน
        bot_info = line_bot_api.get_bot_info()
        return bool(bot_info.display_name)
    except Exception:
        return False

def check_deepseek_api_health():
    """ตรวจสอบการเชื่อมต่อ DeepSeek API"""
    try:
        # Make a minimal API call to check connectivity
        deepseek_client.chat.completions.create(
            model=config.DEEPSEEK_MODEL,
            messages=[{"role": "user", "content": "ping"}],
            max_tokens=1
        )
        return True
    except Exception as e:
        logging.debug(f"DeepSeek API health check failed: {str(e)}")
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

    # ตรวจสอบว่าเป็นการยืนยันรหัสด้วย /verify หรือไม่
    if user_message.lower().startswith("/verify"):
        # ตรวจสอบว่าผู้ใช้ลงทะเบียนแล้วหรือไม่
        if is_user_registered(user_id):
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text="✅ คุณได้ลงทะเบียนและยืนยันตัวตนเรียบร้อยแล้ว\n"
                                    "ไม่จำเป็นต้องยืนยันอีกครั้ง คุณสามารถใช้บริการของน้องใจดีได้ตามปกติ")
            )
            return

        # ดำเนินการต่อสำหรับผู้ที่ยังไม่ได้ลงทะเบียน
        try:
            # แยกรหัสยืนยันออกจากข้อความ
            parts = user_message.split()
            if len(parts) != 2:
                line_bot_api.reply_message(
                    event.reply_token,
                    TextSendMessage(text="รูปแบบไม่ถูกต้อง กรุณาพิมพ์ \"/verify\" ตามด้วยรหัส 6 หลัก เช่น \"/verify 123456\"")
                )
                return

            confirmation_code = parts[1].strip()
            _, message = register_user_with_code(user_id, confirmation_code)

            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text=message)
            )
            return
        except (IndexError, ValueError):
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text="รูปแบบไม่ถูกต้อง กรุณาพิมพ์ \"/verify\" ตามด้วยรหัส 6 หลัก เช่น \"/verify 123456\"")
            )
            return

    # คำสั่งขอลิงก์ลงทะเบียนใหม่
    if user_message.lower() == "/register":
        send_registration_message(user_id)
        return

    # ตรวจสอบการลงทะเบียนก่อนประมวลผลข้อความปกติ
    if not is_user_registered(user_id):
        # ตรวจสอบว่าเคยส่งข้อความลงทะเบียนแล้วหรือไม่
        registration_sent = redis_client.exists(f"registration_sent:{user_id}")

        if not registration_sent:
            send_registration_message(user_id)
            # เก็บสถานะว่าส่งข้อความลงทะเบียนแล้ว (หมดอายุใน 1 วัน)
            redis_client.setex(f"registration_sent:{user_id}", 86400, "1")
        else:
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text="คุณยังไม่ได้ลงทะเบียน กรุณาลงทะเบียนก่อนใช้งาน พิมพ์ /register เพื่อดูวิธีลงทะเบียน")
            )
        return

    # ถ้าลงทะเบียนแล้ว ดำเนินการปกติ
    if is_user_locked(user_id):
        handle_locked_user(user_id)
        return

    # ล็อคผู้ใช้และประมวลผลข้อความ
    lock_user(user_id)
    try:
        process_user_message(user_id, user_message, event.reply_token)
    finally:
        unlock_user(user_id)

@handler.add(FollowEvent)
def handle_follow(event):
    user_id = event.source.user_id

    # ส่งข้อความต้อนรับและขอให้ลงทะเบียน
    welcome_message = (
        "ขอบคุณที่เพิ่มน้องใจดีเป็นเพื่อน! 👋\n\n"
        "น้องใจดีพร้อมเป็นเพื่อนคุยและช่วยเหลือคุณในเรื่องการเลิกสารเสพติด\n\n"
        "👉 ก่อนเริ่มต้นใช้งาน กรุณาลงทะเบียนตามขั้นตอนง่ายๆ"
    )

    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text=welcome_message)
    )

    # ส่งข้อความลงทะเบียนแบบ push message เพื่อให้แน่ใจว่าผู้ใช้ได้รับ
    send_registration_message(user_id)

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
def handle_shutdown(sig=None, frame=None):
    logging.info("กำลังปิดแอปพลิเคชัน...")

    # ปิดตัวกำหนดการ
    try:
        scheduler.shutdown()
        logging.info("ปิดตัวกำหนดการเรียบร้อย")
    except Exception as e:
        logging.error(f"เกิดข้อผิดพลาดในการปิดตัวกำหนดการ: {str(e)}")

    # ปิดการเชื่อมต่อ Redis
    try:
        redis_client.close()
        logging.info("ปิดการเชื่อมต่อ Redis เรียบร้อย")
    except Exception as e:
        logging.error(f"เกิดข้อผิดพลาดในการปิดการเชื่อมต่อ Redis: {str(e)}")

    # ปิดการเชื่อมต่อ DeepSeek API
    try:
        if hasattr(async_deepseek, 'client') and async_deepseek.client:
            asyncio.run(async_deepseek.close())
        logging.info("ปิดการเชื่อมต่อ DeepSeek API เรียบร้อย")
    except Exception as e:
        logging.error(f"เกิดข้อผิดพลาดในการปิดการเชื่อมต่อ DeepSeek API: {str(e)}")

    logging.info("ปิดแอปพลิเคชันเรียบร้อย")
    exit(0)

signal.signal(signal.SIGTERM, handle_shutdown)
signal.signal(signal.SIGINT, handle_shutdown)

if __name__ == "__main__":
    # เริ่มต้นตัวกำหนดการก่อนเริ่มเซิร์ฟเวอร์
    init_scheduler()
    # เริ่มเซิร์ฟเวอร์
    serve(app, host='0.0.0.0', port=5000)
