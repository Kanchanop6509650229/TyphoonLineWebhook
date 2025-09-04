"""
แชทบอท 'ใจดี' - แอปพลิเคชันหลัก
โค้ดหลักสำหรับการจัดการข้อความจาก LINE API และการตอบกลับด้วย xAI Grok API
"""
import os
import json
import logging
from logging.handlers import RotatingFileHandler
import requests
import time
import threading
import asyncio
import re
from datetime import datetime, timedelta
from flask import Flask, request, abort, jsonify
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage, FollowEvent
import redis
from random import choice
import signal
import atexit
from waitress import serve
from apscheduler.schedulers.background import BackgroundScheduler

# นำเข้าโมดูลภายในโปรเจค
from .middleware.rate_limiter import init_limiter
from .config import load_config, SYSTEM_MESSAGES, GENERATION_CONFIG, SUMMARY_GENERATION_CONFIG, TOKEN_THRESHOLD
from .utils import safe_db_operation, safe_api_call, clean_ai_response, check_hospital_inquiry, get_hospital_information_message, handle_grok_api_error
from .llm import grok_client
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
    generate_contextual_followup_message
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
from .error_handling import (
    ChatbotError,
    ErrorCategory,
    ErrorSeverity,
    get_error_handler
)
import traceback
from typing import Optional, List, Dict, Tuple
from enum import Enum

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

# Legacy error types for backward compatibility - will be migrated to new system
class ErrorType(Enum):
    """Legacy error types - use ErrorCategory instead"""
    CONTEXT_LOAD_ERROR = "context_load_error"
    TOKEN_MANAGEMENT_ERROR = "token_management_error"
    AI_API_ERROR = "ai_api_error"
    MESSAGE_SEND_ERROR = "message_send_error"
    DATABASE_ERROR = "database_error"
    UNKNOWN_ERROR = "unknown_error"

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

    # ใช้ Grok client ผ่านโมดูลรวมศูนย์ app/llm/grok_client.py

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

    # สร้าง DatabaseManager ด้วยการตั้งค่าที่เหมาะสม with retry logic for container startup
    max_db_retries = 5
    db_retry_count = 0
    db_manager = None
    
    while db_retry_count < max_db_retries:
        try:
            db_manager = DatabaseManager(db_config, pool_size=32)
            break  # Success, exit retry loop
        except Exception as e:
            db_retry_count += 1
            if db_retry_count >= max_db_retries:
                logging.critical(f"เกิดข้อผิดพลาดในการเริ่มต้นแอพพลิเคชัน: {str(e)}")
                logging.critical(f"เกิดข้อผิดพลาดร้ายแรงในการเริ่มต้นแอพพลิเคชัน: {str(e)}")
                raise
            else:
                wait_time = 5 * db_retry_count
                logging.warning(f"Database initialization failed (attempt {db_retry_count}/{max_db_retries}), retrying in {wait_time} seconds: {str(e)}")
                time.sleep(wait_time)
    
    # Ensure db_manager is not None before proceeding
    if db_manager is None:
        raise RuntimeError("Failed to initialize database manager after all retries")

    # เสร็จสิ้นการตรวจสอบและเริ่มต้นฐานข้อมูล
    initialize_database(db_config)
    logging.info("เสร็จสิ้นการเริ่มต้นและตรวจสอบฐานข้อมูล")
    
    # Apply database optimizations
    try:
        from .database_optimization import optimize_database
        optimization_result = optimize_database(db_config)
        if optimization_result:
            logging.info("การปรับปรุงประสิทธิภาพฐานข้อมูลสำเร็จ")
        else:
            logging.warning("การปรับปรุงประสิทธิภาพฐานข้อมูลเสร็จสิ้นแต่มีปัญหาบางส่วน")
    except Exception as e:
        logging.error(f"ไม่สามารถรันการปรับปรุงฐานข้อมูลได้: {str(e)}")

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

# Health check endpoint
@app.route('/health', methods=['GET'])
def health_check():
    """
    Health check endpoint for monitoring
    """
    try:
        health_status = {
            'status': 'healthy',
            'timestamp': datetime.now().isoformat(),
            'services': {
                'database': 'unknown',
                'redis': 'unknown',
                'xai_api': 'unknown'
            }
        }
        
        # Check database
        try:
            if db_manager and db_manager.check_connection():
                health_status['services']['database'] = 'healthy'
            else:
                health_status['services']['database'] = 'unhealthy'
                health_status['status'] = 'degraded'
        except Exception as e:
            health_status['services']['database'] = f'error: {str(e)[:50]}'
            health_status['status'] = 'degraded'
        
        # Check Redis
        try:
            redis_client.ping()
            health_status['services']['redis'] = 'healthy'
        except Exception as e:
            health_status['services']['redis'] = f'error: {str(e)[:50]}'
            health_status['status'] = 'degraded'
        
        # Check xAI API (simple check)
        try:
            # This is a lightweight check - we don't actually call the API
            if config.XAI_API_KEY:
                health_status['services']['xai_api'] = 'configured'
            else:
                health_status['services']['xai_api'] = 'not_configured'
        except Exception as e:
            health_status['services']['xai_api'] = f'error: {str(e)[:50]}'
        
        # Determine overall status code
        if health_status['status'] == 'healthy':
            return jsonify(health_status), 200
        else:
            return jsonify(health_status), 503
            
    except Exception as e:
        return jsonify({
            'status': 'error',
            'error': str(e),
            'timestamp': datetime.now().isoformat()
        }), 500

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

        text = grok_client.send_chat(
            messages=[
                SYSTEM_MESSAGES,
                {"role": "user", "content": summary_prompt}
            ],
            model=config.XAI_MODEL,
            **SUMMARY_GENERATION_CONFIG,
        )

        return text
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
            # ใช้ role พิเศษสำหรับการสรุปที่ไม่แสดงให้ผู้ใช้เห็น
            optimized_history.append({"role": "system_summary", "content": f"สรุปการสนทนาก่อนหน้า: {combined_summary}"})

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
def filter_messages_for_api(messages):
    """
    กรองข้อความที่มี role เป็น 'system_summary' ออกจากการส่งไปยัง API
    แต่ยังคงไว้ในระบบเพื่อให้ AI เข้าใจบริบท
    
    Args:
        messages (list): รายการข้อความ
        
    Returns:
        list: ข้อความที่กรองแล้ว
    """
    filtered_messages = []
    summary_content = ""
    
    for message in messages:
        if message.get('role') == 'system_summary':
            # เก็บเนื้อหาสรุปแต่ไม่ส่งไปยัง API
            summary_content += message.get('content', '') + "\n\n"
        else:
            filtered_messages.append(message)
    
    # ถ้ามีการสรุป ให้รวมเข้ากับ system message เพื่อให้ AI เข้าใจบริบท
    if summary_content.strip():
        # ค้นหา system message ที่มีอยู่แล้ว
        system_msg_found = False
        for i, msg in enumerate(filtered_messages):
            if msg.get('role') == 'system':
                # เพิ่มการสรุปเข้าใน system message ที่มีอยู่
                filtered_messages[i] = {
                    'role': 'system',
                    'content': msg.get('content', '') + "\n\nข้อมูลสำคัญเพิ่มเติม (สำหรับ AI เท่านั้น):\n" + summary_content.strip()
                }
                system_msg_found = True
                break
        
        # ถ้าไม่มี system message ให้เพิ่มใหม่
        if not system_msg_found:
            filtered_messages.insert(0, {
                'role': 'system',
                'content': "ข้อมูลสำคัญเพิ่มเติม (สำหรับ AI เท่านั้น):\n" + summary_content.strip()
            })
    
    return filtered_messages

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

        text = grok_client.send_chat(
            messages=[
                SYSTEM_MESSAGES,
                {"role": "user", "content": summary_prompt}
            ],
            model=config.XAI_MODEL,
            **SUMMARY_GENERATION_CONFIG,
        )

        return text
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
        text = grok_client.send_chat(
            messages=[
                SYSTEM_MESSAGES,
                {"role": "user", "content": topic_prompt}
            ],
            model=config.XAI_MODEL,
            temperature=0.2,
            max_tokens=800,
        )

        return text
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
    """ยืนยันการลงทะเบียนด้วยรหัสยืนยันและโหลดบริบทผู้ใช้"""
    try:
        # ตรวจสอบว่ารหัสมีอยู่และยังไม่หมดอายุ
        query = 'SELECT code, form_data FROM registration_codes WHERE code = %s AND status = %s'
        result = db_manager.execute_query(query, (code, 'pending'), dictionary=True)
        
        if not result:
            return False, "รหัสยืนยันไม่ถูกต้องหรือหมดอายุแล้ว"
        
        # ดึงข้อมูล form และสรุป
        form_data_json = result[0].get('form_data', '{}')
        form_data = json.loads(form_data_json) if form_data_json else {}
        
        # อัพเดทรหัสให้เชื่อมกับผู้ใช้และสถานะเป็น verified
        update_query = 'UPDATE registration_codes SET user_id = %s, status = %s, verified_at = %s WHERE code = %s'
        db_manager.execute_and_commit(update_query, (user_id, 'verified', datetime.now(), code))
        
        # บันทึกบริบทเริ่มต้นของผู้ใช้
        if form_data and 'ai_summary' in form_data:
            save_user_initial_context(user_id, form_data['ai_summary'])
            
        # ส่งข้อความต้อนรับพร้อมบริบท
        welcome_message = create_personalized_welcome_message(form_data)
        
        return True, welcome_message
        
    except Exception as e:
        logging.error(f"เกิดข้อผิดพลาดในการลงทะเบียน: {str(e)}")
        return False, "เกิดข้อผิดพลาดในการลงทะเบียน กรุณาลองอีกครั้ง"
    
def save_user_initial_context(user_id, ai_summary):
    """บันทึกบริบทเริ่มต้นของผู้ใช้ใน Redis"""
    try:
        # บันทึกบริบทใน Redis โดยไม่มีเวลาหมดอายุ
        context_key = f"user_context:{user_id}"
        redis_client.set(context_key, ai_summary)
        
        # บันทึกเวลาที่สร้างบริบท
        redis_client.set(f"context_created:{user_id}", datetime.now().timestamp())
        
        logging.info(f"บันทึกบริบทเริ่มต้นสำหรับผู้ใช้: {user_id}")
        
    except Exception as e:
        logging.error(f"เกิดข้อผิดพลาดในการบันทึกบริบท: {str(e)}")


def get_user_context(user_id):
    """ดึงบริบทของผู้ใช้จาก Redis"""
    try:
        context_key = f"user_context:{user_id}"
        context = redis_client.get(context_key)
        
        if context:
            if isinstance(context, bytes):
                context = context.decode('utf-8')
            return context
            
        # ถ้าไม่มีบริบทใน Redis ลองดึงจากฐานข้อมูล
        query = '''
            SELECT form_data 
            FROM registration_codes 
            WHERE user_id = %s AND status = 'verified'
            ORDER BY verified_at DESC
            LIMIT 1
        '''
        result = db_manager.execute_query(query, (user_id,))
        
        if result and result[0][0]:
            form_data = json.loads(result[0][0])
            if 'ai_summary' in form_data:
                # บันทึกกลับใน Redis สำหรับการใช้ครั้งถัดไป
                save_user_initial_context(user_id, form_data['ai_summary'])
                return form_data['ai_summary']
                
        return None
        
    except Exception as e:
        logging.error(f"เกิดข้อผิดพลาดในการดึงบริบทผู้ใช้: {str(e)}")
        return None


def create_personalized_welcome_message(form_data):
    """สร้างข้อความต้อนรับแบบเฉพาะบุคคลตามข้อมูลจาก form"""
    base_message = "✅ ลงทะเบียนเรียบร้อยแล้ว! ยินดีต้อนรับสู่แชทบอทน้องใจดีค่ะ\n\n"
    
    if not form_data or 'ai_summary' not in form_data:
        return base_message + "ใจดีพร้อมเป็นเพื่อนคุยและช่วยเหลือคุณในเส้นทางการเลิกสารเสพติด มีอะไรอยากคุยเป็นพิเศษไหมคะ?"
    
    # ถ้ามีข้อมูลจาก form ให้สร้างข้อความเฉพาะบุคคล
    personalized_message = base_message
    
    # ตรวจสอบระดับความเสี่ยง
    if 'full_data' in form_data and 'riskAssessment' in form_data['full_data']:
        risk_level = form_data['full_data']['riskAssessment'].get('overallRisk', 'medium')
        
        if risk_level == 'high':
            personalized_message += "ใจดีเข้าใจว่าคุณอาจกำลังเผชิญกับความท้าทายที่สำคัญ "
            personalized_message += "พร้อมที่จะเป็นกำลังใจและช่วยเหลือคุณทุกขั้นตอนนะคะ\n\n"
        elif risk_level == 'medium':
            personalized_message += "ใจดีดีใจที่คุณตัดสินใจขอความช่วยเหลือ "
            personalized_message += "เราจะผ่านเรื่องนี้ไปด้วยกันนะคะ\n\n"
        else:
            personalized_message += "ขอชื่นชมที่คุณให้ความสำคัญกับสุขภาพของตัวเอง "
            personalized_message += "ใจดีพร้อมสนับสนุนคุณค่ะ\n\n"
    
    personalized_message += "จากข้อมูลที่คุณให้มา ใจดีพร้อมที่จะช่วยเหลือคุณแบบเฉพาะบุคคล "
    personalized_message += "คุณสามารถพูดคุยเรื่องใดก็ได้ที่คุณสบายใจ หรือถามคำถามที่อยากรู้ได้เลยค่ะ\n\n"
    personalized_message += "💚 พิมพ์ /help เพื่อดูคำสั่งทั้งหมด"
    
    return personalized_message

def send_registration_message(user_id):
    """ส่งข้อความแนะนำการลงทะเบียน"""
    register_message = (
        "สวัสดีค่ะ! ยินดีต้อนรับสู่แชทบอท 'ใจดี'\n\n"
        "เพื่อเริ่มใช้งาน คุณจำเป็นต้องลงทะเบียนก่อน โดยทำตามขั้นตอนดังนี้:\n\n"
        "1. กรอกแบบฟอร์มที่ลิงก์นี้: https://forms.gle/gVE6WN7W5thHR1kZ9\n"
        "2. หลังกรอกเสร็จ คุณจะได้รับรหัสยืนยัน 6 หลัก\n"
        "3. นำรหัสมาพิมพ์ที่นี่ด้วยคำสั่ง \"/verify รหัส\" เช่น \"/verify 123456\"\n\n"
        "หากมีข้อสงสัย พิมพ์ /help เพื่อดูคำแนะนำ\n\n"
        "📧 ติดต่อสอบถาม:\n"
        "• ปัญหาทางเทคนิค: pahnkcn@gmail.com\n"
        "• คำถามเกี่ยวกับการวิจัย: Std6548097@pcm.ac.th"
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
            # แปลง bytes เป็ string ถ้าจำเป็น
            if isinstance(user_id, bytes):
                user_id = user_id.decode('utf-8')

            # สร้างข้อความติดตามที่เป็นไปตามบริบทของการสนทนา
            follow_up_message = generate_contextual_followup_message(user_id, db, config)
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
    """ส่งคำตอบสุดท้ายหลังประมวลผลเสร็จ

    แบ่งคำตอบออกเป็นหลายข้อความเมื่อมีสัญลักษณ์หัวข้อหรือบรรทัดว่างสองบรรทัด
    เพื่อให้แต่ละหัวข้อแสดงเป็นบับเบิลแยกบน LINE
    หากมีมากกว่า 5 ข้อความ จะส่งเป็นหลายครั้ง
    """
    try:
        # แยกข้อความด้วยตัวแบ่งหัวข้อ (•) หรือบรรทัดว่างอย่างน้อย 2 บรรทัด
        segments = [
            seg.strip() for seg in re.split(r"\n{2,}|•", bot_response) if seg.strip()
        ]
        
        # ตรวจสอบจำนวน segments
        if not segments:
            # ถ้าไม่มี segments ให้ส่งข้อความเดิม
            messages = [TextSendMessage(text=bot_response)]
            line_bot_api.push_message(user_id, messages)
        elif len(segments) <= 5:
            # จำนวน segments อยู่ในขอบเขตที่อนุญาต (1-5)
            messages = [TextSendMessage(text=segment) for segment in segments]
            line_bot_api.push_message(user_id, messages)
        else:
            # ถ้าเกิน 5 segments ให้แบ่งส่งเป็นหลายครั้ง
            for i in range(0, len(segments), 5):
                batch = segments[i:i+5]
                messages = [TextSendMessage(text=segment) for segment in batch]
                line_bot_api.push_message(user_id, messages)
                # หน่วงเวลาเล็กน้อยระหว่างการส่งแต่ละครั้ง เพื่อไม่ให้ส่งพร้อมกัน
                if i + 5 < len(segments):
                    time.sleep(0.5)
        
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

@safe_api_call
def summarize_form_data(form_data):
    """
    สรุปข้อมูลจาก Google Form โดยใช้ xAI Grok
    
    Args:
        form_data (dict): ข้อมูลจาก Google Form
        
    Returns:
        str: ข้อความสรุปข้อมูลผู้ใช้
    """
    try:
        # สร้าง prompt สำหรับการสรุป
        prompt = """
จากข้อมูลแบบประเมินต่อไปนี้ กรุณาสรุปข้อมูลสำคัญของผู้ใช้ในรูปแบบที่จะช่วยให้แชทบอทเข้าใจบริบทและให้คำปรึกษาได้อย่างเหมาะสม:

ข้อมูลการตอบแบบสอบถาม:
"""
        
        # เพิ่มคำถาม-คำตอบทั้งหมด
        for item in form_data.get('responses', []):
            prompt += f"\nคำถาม: {item['question']}\nคำตอบ: {item['answer']}\n"
        
        # เพิ่มข้อมูล ASSIST scores
        if 'assistScores' in form_data:
            prompt += "\n\nผลการประเมิน ASSIST:\n"
            for substance, score in form_data['assistScores'].items():
                prompt += f"- {substance}: {score} คะแนน\n"
        
        # เพิ่มการประเมินความเสี่ยง
        if 'riskAssessment' in form_data:
            risk_data = form_data['riskAssessment']
            prompt += f"\n\nระดับความเสี่ยงโดยรวม: {risk_data.get('overallRisk', 'ไม่ระบุ')}\n"
        
        prompt += """
กรุณาสรุปข้อมูลในหัวข้อต่อไปนี้:
1. ประวัติการใช้สารเสพติด (ชนิด ความถี่ ระยะเวลา)
2. ระดับความเสี่ยงและปัญหาที่พบ
3. แรงจูงใจและเป้าหมายในการเลิก
4. ปัจจัยสนับสนุนและอุปสรรค
5. ข้อมูลสำคัญอื่นๆ ที่ควรทราบ

โปรดสรุปให้กระชับ ชัดเจน และเป็นประโยชน์ต่อการให้คำปรึกษา
"""
        
        # เรียก xAI Grok API
        summary = grok_client.send_chat(
            messages=[
                {
                    "role": "system",
                    "content": "คุณคือผู้เชี่ยวชาญด้านการบำบัดสารเสพติด ช่วยสรุปข้อมูลผู้ใช้อย่างเป็นมืออาชีพ",
                },
                {"role": "user", "content": prompt},
            ],
            model=config.XAI_MODEL,
            temperature=0.3,
            max_tokens=1000,
        )
        return clean_ai_response(summary)
        
    except Exception as e:
        logging.error(f"เกิดข้อผิดพลาดในการสรุปข้อมูล form: {str(e)}")
        # ถ้าสรุปไม่ได้ ให้สร้างสรุปพื้นฐาน
        return create_basic_summary(form_data)


def create_basic_summary(form_data):
    """สร้างสรุปพื้นฐานถ้า AI ไม่สามารถสรุปได้"""
    summary = "ข้อมูลพื้นฐานจากแบบประเมิน:\n\n"
    
    if 'assistScores' in form_data:
        summary += "สารเสพติดที่ใช้:\n"
        for substance, score in form_data['assistScores'].items():
            risk_level = get_risk_level_from_score(substance, score)
            summary += f"- {substance}: {score} คะแนน ({risk_level})\n"
    
    if 'riskAssessment' in form_data:
        risk = form_data['riskAssessment'].get('overallRisk', 'ไม่ระบุ')
        summary += f"\nระดับความเสี่ยงโดยรวม: {risk}\n"
    
    return summary


def get_risk_level_from_score(substance, score):
    """คำนวณระดับความเสี่ยงจากคะแนน"""
    if substance == 'เครื่องดื่มแอลกอฮอล์':
        if score <= 10:
            return 'ความเสี่ยงต่ำ'
        elif score <= 26:
            return 'ความเสี่ยงปานกลาง'
        else:
            return 'ความเสี่ยงสูง'
    else:
        if score <= 3:
            return 'ความเสี่ยงต่ำ'
        elif score <= 26:
            return 'ความเสี่ยงปานกลาง'
        else:
            return 'ความเสี่ยงสูง'

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

    # ตรวจสอบคำสอบถามเกี่ยวกับสถานพยาบาล
    if check_hospital_inquiry(user_message):
        hospital_response = get_hospital_information_message()
        send_final_response(user_id, hospital_response)
        return

    # ประมวลผลกับ AI และส่งการตอบกลับ
    process_ai_response_with_context(user_id, user_message, start_time, animation_success)

def process_ai_response_with_context(user_id: str, user_message: str, start_time: float, animation_success: bool):
    """
    สร้างการตอบกลับ AI โดยใช้บริบทจาก form พร้อมการจัดการข้อผิดพลาดที่ดีขึ้น
    """
    # ตัวแปรสำหรับเก็บสถานะและข้อมูลสำคัญ
    user_context = None
    messages = []
    bot_response = None
    error_occurred = False
    fallback_response = None
    
    try:
        # 1. ดึงบริบทผู้ใช้ (ไม่ critical - สามารถทำงานต่อได้แม้ไม่มีบริบท)
        try:
            user_context = get_user_context(user_id)
            if user_context:
                logging.info(f"โหลดบริบทผู้ใช้สำเร็จ: {user_id}")
        except Exception as e:
            logging.warning(f"ไม่สามารถโหลดบริบทผู้ใช้ {user_id}: {str(e)}")
            # ไม่ throw error - ให้ทำงานต่อแบบไม่มีบริบท
            user_context = None
        
        # 2. จัดการประวัติการสนทนาและโทเค็น
        try:
            messages = prepare_conversation_messages(user_id, user_context)
        except TokenThresholdExceeded:
            # ถ้าโทเค็นเกิน ใช้การจัดการแบบพิเศษ
            logging.info(f"โทเค็นเกินขีดจำกัดสำหรับผู้ใช้ {user_id}, ใช้การจัดการแบบไฮบริด")
            try:
                messages = hybrid_context_management(user_id, TOKEN_THRESHOLD)
                # เพิ่มบริบทกลับเข้าไปถ้ามี
                if user_context:
                    add_context_to_messages(messages, user_context)
            except Exception as hybrid_error:
                logging.error(f"การจัดการแบบไฮบริดล้มเหลว: {str(hybrid_error)}")
                # Fallback: ใช้เซสชันว่าง
                messages = create_minimal_session(user_context)
        except Exception as e:
            logging.error(f"เกิดข้อผิดพลาดในการเตรียมข้อความ: {str(e)}")
            messages = create_minimal_session(user_context)
        
        # 3. เพิ่มข้อความของผู้ใช้
        messages.append({"role": "user", "content": user_message})
        
        # 4. เรียก AI API พร้อม retry mechanism
        bot_response = None
        max_retries = 3
        retry_count = 0
        
        while retry_count < max_retries and bot_response is None:
            try:
                response_text = generate_ai_response_with_timeout(messages, timeout=30)
                
                if not response_text:
                    raise ValueError("Empty AI response")
                
                bot_response = clean_ai_response(response_text)
                
                if not bot_response or len(bot_response.strip()) == 0:
                    raise ValueError("Empty response from AI")
                    
                break  # สำเร็จ
                
            except requests.exceptions.Timeout:
                retry_count += 1
                if retry_count < max_retries:
                    logging.warning(f"AI API timeout (attempt {retry_count}/{max_retries})")
                    time.sleep(2 ** retry_count)  # Exponential backoff
                else:
                    raise create_legacy_chatbot_error(
                        ErrorType.AI_API_ERROR,
                        "AI API timeout after all retries",
                        e
                    )
                    
            except RateLimitError as e:
                # จัดการ rate limit แบบพิเศษ
                wait_time = e.retry_after if hasattr(e, 'retry_after') else 60
                logging.warning(f"Rate limited, waiting {wait_time} seconds")
                
                # ส่งข้อความแจ้งผู้ใช้
                send_rate_limit_notification(user_id, wait_time)
                
                # รอแล้วลองใหม่
                time.sleep(wait_time)
                retry_count += 1
                
            except Exception as e:
                logging.error(f"AI API error (attempt {retry_count + 1}): {str(e)}")
                retry_count += 1
                if retry_count >= max_retries:
                    raise create_legacy_chatbot_error(
                        ErrorType.AI_API_ERROR,
                        f"AI API error after {max_retries} attempts",
                        e
                    )
        
        # 5. ถ้ายังไม่มี response ให้ใช้ fallback
        if not bot_response:
            bot_response = generate_fallback_response(user_message, user_context)
            fallback_response = bot_response  # บันทึกว่าใช้ fallback
        
        # 6. เพิ่มข้อความตอบกลับลงในประวัติ
        messages.append({"role": "assistant", "content": bot_response})
        
        # 7. ประมวลผลและบันทึกข้อมูล (ใช้ transaction-like approach)
        try:
            process_conversation_data_safely(user_id, user_message, bot_response, messages)
        except Exception as e:
            logging.error(f"เกิดข้อผิดพลาดในการบันทึกข้อมูล: {str(e)}")
            # ไม่ให้ error นี้ทำให้ผู้ใช้ไม่ได้รับคำตอบ
            error_occurred = True
        
        # 8. จัดการจังหวะเวลา
        handle_response_timing(start_time, animation_success)
        
        # 9. ส่งการตอบกลับ
        try:
            success = send_final_response(user_id, bot_response)
            if not success:
                raise create_legacy_chatbot_error(
                    ErrorType.MESSAGE_SEND_ERROR,
                    "Failed to send response to user"
                )
                
            # ถ้าใช้ fallback หรือมี error แจ้งให้ผู้ใช้ทราบ
            if fallback_response or error_occurred:
                send_system_notification(user_id, fallback_response, error_occurred)
                
        except Exception as e:
            logging.critical(f"ไม่สามารถส่งข้อความให้ผู้ใช้ {user_id}: {str(e)}")
            # นี่คือ critical error - ผู้ใช้จะไม่ได้รับการตอบกลับเลย
            notify_admin_critical_error(user_id, user_message, str(e))
            
        # 10. บันทึกเวลาประมวลผล
        total_time = time.time() - start_time
        logging.info(f"เวลาประมวลผลทั้งหมดสำหรับผู้ใช้ {user_id}: {total_time:.2f} วินาที")
        
        # 11. บันทึก metrics
        record_processing_metrics(user_id, total_time, fallback_response is not None, error_occurred)
        
    except ChatbotError as e:
        # จัดการ custom errors
        handle_chatbot_error(e, user_id, user_message)
        
    except Exception as e:
        # จัดการ unexpected errors
        logging.critical(f"Unexpected error in process_ai_response: {str(e)}", exc_info=True)
        handle_unexpected_error(e, user_id, user_message)


def prepare_conversation_messages(user_id: str, user_context: Optional[str]) -> List[Dict[str, str]]:
    """เตรียมข้อความสำหรับการสนทนา พร้อมจัดการข้อผิดพลาด"""
    try:
        session_token_count = get_session_token_count(user_id)
        logging.info(f"จำนวนโทเค็นปัจจุบัน: {session_token_count} (ผู้ใช้: {user_id})")
        
        if session_token_count > TOKEN_THRESHOLD:
            raise TokenThresholdExceeded(f"Token count {session_token_count} exceeds threshold")
        
        # ดึงประวัติการสนทนา
        messages = get_chat_session(user_id) or []
        
        # เพิ่มประวัติจากฐานข้อมูลถ้าจำเป็น
        try:
            optimized_history = db.get_user_history(user_id, max_tokens=10000)
            if optimized_history:
                prepare_conversation_context(messages, optimized_history)
        except Exception as e:
            logging.warning(f"ไม่สามารถโหลดประวัติจากฐานข้อมูล: {str(e)}")
        
        # เพิ่มบริบทถ้ามี
        if user_context:
            add_context_to_messages(messages, user_context)
            
        return messages
        
    except Exception as e:
        logging.error(f"Error in prepare_conversation_messages: {str(e)}")
        raise


def add_context_to_messages(messages: List[Dict[str, str]], user_context: str):
    """เพิ่มบริบทผู้ใช้ลงในข้อความ"""
    # ตรวจสอบว่ายังไม่มีบริบทอยู่แล้ว
    if not any(msg.get('content', '').startswith('บริบทผู้ใช้จากแบบประเมิน:') for msg in messages):
        context_message = {
            "role": "system",
            "content": f"บริบทผู้ใช้จากแบบประเมิน:\n{user_context}\n\nใช้ข้อมูลนี้เพื่อให้คำปรึกษาที่เหมาะสมกับสถานการณ์ของผู้ใช้"
        }
        # แทรกหลัง system message หลัก
        if messages and messages[0].get('role') == 'system':
            messages.insert(1, context_message)
        else:
            messages.insert(0, context_message)


def create_minimal_session(user_context: Optional[str]) -> List[Dict[str, str]]:
    """สร้างเซสชันขั้นต่ำเมื่อไม่สามารถโหลดประวัติได้"""
    messages = [SYSTEM_MESSAGES]
    
    if user_context:
        add_context_to_messages(messages, user_context)
        
    return messages


def generate_ai_response_with_timeout(messages: List[Dict[str, str]], timeout: int = 30) -> str:
    """เรียก xAI Grok API พร้อม timeout และคืนข้อความตอบกลับ"""
    import concurrent.futures

    filtered_messages = filter_messages_for_api(messages)

    def _call() -> str:
        return grok_client.send_chat(
            messages=[SYSTEM_MESSAGES] + filtered_messages,
            model=config.XAI_MODEL,
            **GENERATION_CONFIG,
        )

    with concurrent.futures.ThreadPoolExecutor() as executor:
        future = executor.submit(_call)
        try:
            return future.result(timeout=timeout)
        except concurrent.futures.TimeoutError:
            raise requests.exceptions.Timeout(f"AI API timeout after {timeout} seconds")


def generate_fallback_response(user_message: str, user_context: Optional[str]) -> str:
    """สร้างคำตอบสำรองเมื่อ AI API ไม่ทำงาน"""
    # ตรวจสอบประเภทของคำถาม
    message_lower = user_message.lower()
    
    # คำตอบสำหรับกรณีฉุกเฉิน
    if any(word in message_lower for word in ['ฆ่าตัวตาย', 'ทำร้ายตัวเอง', 'อยากตาย']):
        return (
            "ใจดีเข้าใจว่าคุณกำลังผ่านช่วงเวลาที่ยากลำบาก\n\n"
            "⚠️ กรุณาติดต่อสายด่วนสุขภาพจิต 1323 ทันที\n"
            "หรือโทร 1669 หากต้องการความช่วยเหลือฉุกเฉิน\n\n"
            "คุณไม่ได้อยู่คนเดียว มีคนพร้อมช่วยเหลือคุณตลอด 24 ชั่วโมง"
        )
    
    # คำตอบทั่วไป
    return (
        "ขออภัยค่ะ ระบบกำลังประสบปัญหาชั่วคราว\n\n"
        "ใจดียังคงอยู่ที่นี่และพร้อมรับฟังคุณ "
        "กรุณาลองพูดคุยกับใจดีอีกครั้งในอีกสักครู่นะคะ\n\n"
        "หากต้องการความช่วยเหลือเร่งด่วน:\n"
        "📞 สายด่วนยาเสพติด: 1165\n"
        "📞 สายด่วนสุขภาพจิต: 1323"
    )


def process_conversation_data_safely(user_id: str, user_message: str, bot_response: str, messages: List[Dict[str, str]]):
    """บันทึกข้อมูลการสนทนาแบบปลอดภัย"""
    try:
        # ใช้ transaction-like approach
        temp_data = {
            'user_id': user_id,
            'user_message': user_message,
            'bot_response': bot_response,
            'timestamp': datetime.now(),
            'messages': messages.copy()
        }
        
        # บันทึกลง Redis ก่อน (fast, ถ้าล้มเหลวยังมีข้อมูลใน memory)
        try:
            save_chat_session(user_id, messages)
        except Exception as e:
            logging.error(f"Failed to save to Redis: {str(e)}")
            # เก็บใน queue สำหรับ retry ภายหลัง
            queue_for_retry('redis_save', temp_data)
        
        # บันทึกลงฐานข้อมูล
        try:
            message_token_count = token_counter.count_tokens(user_message + bot_response)
            risk_level, keywords = assess_risk(user_message)
            is_important = is_important_message(user_message, bot_response)
            
            db.save_conversation(
                user_id=user_id,
                user_message=user_message,
                bot_response=bot_response,
                token_count=message_token_count,
                important=is_important
            )
            
            save_progress_data(user_id, risk_level, keywords)
            
        except Exception as e:
            logging.error(f"Failed to save to database: {str(e)}")
            queue_for_retry('db_save', temp_data)
        
        # กำหนดการติดตาม
        try:
            schedule_follow_up(user_id, None)
        except Exception as e:
            logging.error(f"Failed to schedule follow-up: {str(e)}")
            # ไม่ critical - ไม่ต้อง retry
            
    except Exception as e:
        logging.error(f"Unexpected error in process_conversation_data_safely: {str(e)}")
        raise


def send_system_notification(user_id: str, used_fallback: bool, had_error: bool):
    """ส่งการแจ้งเตือนระบบให้ผู้ใช้"""
    if used_fallback:
        message = "💡 หมายเหตุ: ระบบใช้การตอบกลับสำรองเนื่องจากมีปัญหาชั่วคราว"
    elif had_error:
        message = "⚠️ มีข้อผิดพลาดบางอย่างในการบันทึกข้อมูล แต่การสนทนายังดำเนินต่อได้"
    else:
        return
        
    # ส่งแบบ delayed เพื่อไม่รบกวน flow หลัก
    def send_delayed():
        time.sleep(2)
        try:
            line_bot_api.push_message(user_id, TextSendMessage(text=message))
        except:
            pass  # ไม่ต้องทำอะไรถ้าส่งไม่ได้
            
    threading.Thread(target=send_delayed, daemon=True).start()


# Helper function to create legacy-compatible ChatbotError
def create_legacy_chatbot_error(error_type: ErrorType, message: str, original_error: Optional[Exception] = None):
    """Create ChatbotError compatible with legacy system"""
    # Create a simple object that mimics the old ChatbotError for backward compatibility
    class LegacyChatbotError(Exception):
        def __init__(self, error_type: ErrorType, message: str, original_error: Optional[Exception] = None):
            self.error_type = error_type
            self.message = message
            self.original_error = original_error
            super().__init__(self.message)
    
    return LegacyChatbotError(error_type, message, original_error)


def handle_chatbot_error(error: ChatbotError, user_id: str, user_message: str):
    """จัดการข้อผิดพลาดที่คาดการณ์ได้ - compatible with both legacy and new error systems"""
    
    # Handle both old and new ChatbotError formats
    if hasattr(error, 'error_type'):  # Legacy format
        logging.error(f"ChatbotError [Legacy-{error.error_type.value}]: {error.message}")
        
        # ส่งข้อความที่เหมาะสมตามประเภทข้อผิดพลาด
        error_messages = {
            ErrorType.AI_API_ERROR: "ขออภัยค่ะ ระบบ AI กำลังมีปัญหา กรุณาลองใหม่อีกครั้ง",
            ErrorType.TOKEN_MANAGEMENT_ERROR: "กำลังจัดระเบียบข้อมูล กรุณารอสักครู่",
            ErrorType.DATABASE_ERROR: "มีปัญหาในการบันทึกข้อมูล แต่เรายังคุยกันต่อได้ค่ะ",
            ErrorType.MESSAGE_SEND_ERROR: "ไม่สามารถส่งข้อความได้ กรุณาตรวจสอบการเชื่อมต่อ"
        }
        
        message = error_messages.get(
            error.error_type, 
            "ขออภัยค่ะ เกิดข้อผิดพลาด กรุณาลองใหม่"
        )
    else:  # New enhanced format
        logging.error(f"ChatbotError [{error.category.value}-{error.severity.value}]: {error.message}")
        # Use the built-in user_message from the new error system
        message = error.user_message or "ขออภัยค่ะ เกิดข้อผิดพลาด กรุณาลองใหม่"
    
    try:
        send_final_response(user_id, message)
    except:
        # ถ้าส่งไม่ได้จริงๆ บันทึก log
        logging.critical(f"Cannot send error message to user {user_id}")


def handle_unexpected_error(error: Exception, user_id: str, user_message: str):
    """จัดการข้อผิดพลาดที่ไม่คาดคิด"""
    error_id = f"ERR_{datetime.now().strftime('%Y%m%d%H%M%S')}_{user_id[:8]}"
    
    logging.critical(
        f"Unexpected error {error_id}:\n"
        f"User: {user_id}\n"
        f"Message: {user_message}\n"
        f"Error: {str(error)}\n"
        f"Traceback: {traceback.format_exc()}"
    )
    
    # บันทึกข้อผิดพลาดสำหรับการวิเคราะห์
    save_error_for_analysis(error_id, user_id, user_message, error)
    
    # ส่งข้อความให้ผู้ใช้
    try:
        message = (
            "ขออภัยค่ะ เกิดข้อผิดพลาดที่ไม่คาดคิด\n"
            f"รหัสข้อผิดพลาด: {error_id}\n\n"
            "กรุณาลองใหม่อีกครั้ง หรือติดต่อผู้ดูแลระบบ"
        )
        send_final_response(user_id, message)
    except:
        pass


def queue_for_retry(operation_type: str, data: dict):
    """เก็บข้อมูลไว้สำหรับ retry ภายหลัง"""
    try:
        retry_key = f"retry_queue:{operation_type}"
        redis_client.lpush(retry_key, json.dumps({
            'data': data,
            'timestamp': datetime.now().isoformat(),
            'attempts': 0
        }))
        # ตั้ง expiry 24 ชั่วโมง
        redis_client.expire(retry_key, 86400)
    except:
        # ถ้า Redis ไม่ทำงาน บันทึกลงไฟล์
        with open(f"retry_{operation_type}_{datetime.now().strftime('%Y%m%d')}.log", 'a') as f:
            f.write(json.dumps(data) + '\n')


def record_processing_metrics(user_id: str, processing_time: float, used_fallback: bool, had_error: bool):
    """บันทึก metrics สำหรับการ monitoring"""
    try:
        metrics = {
            'user_id': user_id,
            'processing_time': processing_time,
            'used_fallback': used_fallback,
            'had_error': had_error,
            'timestamp': datetime.now().isoformat()
        }
        
        # บันทึกลง Redis สำหรับ real-time monitoring
        redis_client.lpush('processing_metrics', json.dumps(metrics))
        redis_client.ltrim('processing_metrics', 0, 9999)  # เก็บแค่ 10,000 รายการล่าสุด
        
        # Update aggregated metrics
        if processing_time > 10:  # Slow response
            redis_client.incr('metrics:slow_responses')
        if used_fallback:
            redis_client.incr('metrics:fallback_used')
        if had_error:
            redis_client.incr('metrics:errors_occurred')
            
    except:
        pass  # Metrics เป็น nice-to-have, ไม่ให้กระทบ main flow


def notify_admin_critical_error(user_id: str, user_message: str, error: str):
    """แจ้งเตือน admin เมื่อมี critical error"""
    # ใช้วิธีที่เหมาะสมกับระบบ เช่น
    # - ส่งอีเมล
    # - ส่ง LINE notify
    # - บันทึกลง monitoring system
    pass


def save_error_for_analysis(error_id: str, user_id: str, user_message: str, error: Exception):
    """บันทึกข้อผิดพลาดสำหรับการวิเคราะห์"""
    try:
        error_data = {
            'error_id': error_id,
            'user_id': user_id,
            'user_message': user_message[:500],  # จำกัดความยาว
            'error_type': type(error).__name__,
            'error_message': str(error),
            'traceback': traceback.format_exc(),
            'timestamp': datetime.now().isoformat()
        }
        
        # บันทึกลง Redis
        redis_client.hset(f"errors:{datetime.now().strftime('%Y%m%d')}", error_id, json.dumps(error_data))
        redis_client.expire(f"errors:{datetime.now().strftime('%Y%m%d')}", 604800)  # 7 วัน
        
    except:
        # ถ้าบันทึกไม่ได้ ก็ไม่ต้องทำอะไร
        pass


# Custom Exceptions
class TokenThresholdExceeded(Exception):
    """เกิดขึ้นเมื่อโทเค็นเกินขีดจำกัด"""
    pass

class RateLimitError(Exception):
    """เกิดขึ้นเมื่อถูก rate limit"""
    def __init__(self, message: str, retry_after: int = 60):
        self.retry_after = retry_after
        super().__init__(message)


def send_rate_limit_notification(user_id: str, wait_time: int):
    """แจ้งผู้ใช้เมื่อถูก rate limit"""
    try:
        message = (
            f"⏳ ขออภัยค่ะ ระบบกำลังประมวลผลหนัก\n"
            f"กรุณารอประมาณ {wait_time} วินาที แล้วลองใหม่อีกครั้ง\n\n"
            "ใจดีจะรีบกลับมาคุยกับคุณโดยเร็วที่สุดนะคะ 💚"
        )
        line_bot_api.push_message(user_id, TextSendMessage(text=message))
    except:
        pass  # ถ้าส่งไม่ได้ก็ไม่เป็นไร


def prepare_conversation_context(messages, optimized_history):
    """เตรียมบริบทการสนทนาโดยใช้ประวัติ"""
    # ตรวจสอบว่า optimized_history เป็น None หรือไม่
    if optimized_history is None:
        # ถ้าเป็น None ให้ใช้ list ว่าง
        optimized_history = []

    if len(optimized_history) > 5:
        summary = summarize_conversation_history(optimized_history[5:])
        if summary:
            # ใช้ role พิเศษสำหรับการสรุปที่ไม่แสดงให้ผู้ใช้เห็น
            messages.append({"role": "system_summary", "content": f"สรุปการสนทนาก่อนหน้า: {summary}"})

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
        hybrid_context_management(user_id, TOKEN_THRESHOLD)
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
            "สวัสดีค่ะ 👋 ฉันคือน้องใจดี ผู้ช่วยดูแลและให้คำปรึกษาสำหรับผู้ที่ต้องการเลิกใช้สารเสพติด"
            "💬 ฉันสามารถช่วยคุณได้ดังนี้:\n"
            "- พูดคุยและให้กำลังใจในการเลิกใช้สารเสพติด\n"
            "- ให้ข้อมูลเกี่ยวกับผลกระทบของสารเสพติดต่อร่างกายและจิตใจ\n"
            "- แนะนำเทคนิคจัดการความอยากและความเครียด\n"
            "📋 คำสั่งที่ใช้ได้:\n"
            "🔑 /verify [รหัส] - ยืนยันการลงทะเบียนด้วยรหัสที่ได้จาก Google Form\n"
            "📝 /register - ขอข้อมูลการลงทะเบียนและลิงก์กรอกแบบฟอร์ม\n"
            "📊 /status - ดูสถิติการใช้งานและข้อมูลเซสชัน\n"
            "📈 /progress - ดูรายงานความก้าวหน้าของคุณ\n"
            "🔄 /optimize - ปรับปรุงประวัติการสนทนาให้มีประสิทธิภาพ\n"
            "📈 /tokens - ตรวจสอบการใช้งานโทเค็นในเซสชันปัจจุบัน\n"
            "📋 /context - ดูบริบทของคุณจากแบบประเมินที่กรอกไว้\n"
            "🔔 /followup - ตรวจสอบกำหนดการติดตามของคุณ\n"
            "🚨 /emergency - ดูข้อมูลติดต่อฉุกเฉินและสายด่วน\n"
            "❓ /help - แสดงเมนูช่วยเหลือนี้\n\n"
            "💡 ตัวอย่างคำถามที่สามารถถามฉันได้:\n"
            "- \"ช่วยประเมินการใช้สารเสพติดของฉันหน่อย\"\n"
            "- \"ผลกระทบของยาบ้าต่อร่างกายมีอะไรบ้าง\"\n"
            "- \"มีเทคนิคจัดการความอยากยาอย่างไร\"\n"
            "- \"ฉันควรทำอย่างไรเมื่อรู้สึกอยากกลับไปใช้สารอีก\"\n\n"
            "📧 ติดต่อทีมงาน:\n"
            "หากพบข้อผิดพลาด (บัค) หรือมีข้อเสนอแนะ สามารถติดต่อได้ที่:\n"
            "🔧 ผู้พัฒนาระบบ: pahnkcn@gmail.com\n"
            "📖 ผู้วิจัย: Std6548097@pcm.ac.th\n\n"
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
            "1. กรอกแบบฟอร์มที่ลิงก์นี้: https://forms.gle/r5MGki6QFtBer2PM8\n"
            "2. หลังกรอกเสร็จ คุณจะได้รับรหัสยืนยัน 6 หลัก\n"
            "3. นำรหัสมาพิมพ์ที่นี่ด้วยคำสั่ง \"/verify รหัส\" เช่น \"/verify 123456\"\n\n"
            "หากมีปัญหาในการลงทะเบียน คุณสามารถติดต่อเจ้าหน้าที่ได้ที่ support@example.com"
        )
        
    elif command == '/context':
        # คำสั่งใหม่: ดูบริบทจากแบบประเมิน
        context = get_user_context(user_id)
        
        if context:
            response_text = (
                "📋 บริบทของคุณจากแบบประเมิน:\n\n"
                f"{context}\n\n"
                "ใจดีใช้ข้อมูลนี้เพื่อให้คำปรึกษาที่เหมาะสมกับคุณมากที่สุดค่ะ"
            )
        else:
            response_text = (
                "ไม่พบข้อมูลบริบทจากแบบประเมิน\n"
                "อาจเป็นเพราะคุณลงทะเบียนก่อนที่ระบบจะมีฟีเจอร์นี้"
            )
        
        send_final_response(user_id, response_text)
        return

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
def generate_ai_response(messages) -> str:
    """สร้างการตอบกลับด้วย AI โดยมีการจัดการข้อผิดพลาด (xAI Grok)"""
    try:
        filtered_messages = filter_messages_for_api(messages)
        text = grok_client.send_chat(
            messages=[SYSTEM_MESSAGES] + filtered_messages,
            model=config.XAI_MODEL,
            **GENERATION_CONFIG,
        )
        if not text:
            logging.error("ได้รับการตอบกลับที่ไม่ถูกต้องจาก xAI Grok API")
            raise ValueError("Invalid response from xAI Grok API")
        return text
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
    """API endpoint รับรหัสยืนยันและข้อมูล form จาก Google Apps Script"""
    
    # ตรวจสอบการรับรอง API key
    api_key = request.json.get('api_key', '')
    if api_key != os.getenv('FORM_WEBHOOK_KEY', 'your_secret_key_here'):
        return jsonify({"success": False, "error": "Unauthorized"}), 401
    
    # รับข้อมูลจาก request
    code = request.json.get('code', '')
    full_form_data = request.json.get('full_form_data', {})
    
    if not code or not code.isdigit() or len(code) != 6:
        return jsonify({"success": False, "error": "Invalid verification code"}), 400
    
    try:
        # ตรวจสอบว่ารหัสมีอยู่แล้วหรือไม่
        check_query = 'SELECT code FROM registration_codes WHERE code = %s'
        result = db_manager.execute_query(check_query, (code,))
        
        if result and result[0]:
            return jsonify({"success": False, "error": "Code already exists"}), 409
        
        # สรุปข้อมูลด้วย AI
        ai_summary = ""
        if full_form_data:
            logging.info(f"กำลังสรุปข้อมูล form สำหรับรหัส: {code}")
            ai_summary = summarize_form_data(full_form_data)
        
        # เตรียมข้อมูลสำหรับบันทึก
        form_data_json = {
            "full_data": full_form_data,
            "ai_summary": ai_summary,
            "processed_at": datetime.now().isoformat()
        }
        
        # บันทึกรหัสใหม่พร้อมข้อมูล form และสรุป
        insert_query = '''
            INSERT INTO registration_codes 
            (code, created_at, status, form_data) 
            VALUES (%s, %s, %s, %s)
        '''
        db_manager.execute_and_commit(
            insert_query, 
            (code, datetime.now(), 'pending', json.dumps(form_data_json))
        )
        
        logging.info(f"บันทึกรหัสยืนยันและข้อมูล form สำเร็จ: {code}")
        return jsonify({
            "success": True, 
            "message": "Verification code and form data saved successfully",
            "summary_created": bool(ai_summary)
        }), 201
        
    except Exception as e:
        logging.error(f"เกิดข้อผิดพลาดในการบันทึกรหัสยืนยัน: {str(e)}")
        return jsonify({"success": False, "error": str(e)}), 500

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

def check_grok_api_health():
    """ตรวจสอบการเชื่อมต่อ xAI Grok API"""
    try:
        _ = grok_client.send_chat(
            messages=[{"role": "user", "content": "ping"}],
            model=config.XAI_MODEL,
            max_tokens=1,
        )
        return True
    except Exception as e:
        logging.debug(f"xAI Grok API health check failed: {str(e)}")
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

    # ปิดการเชื่อมต่อ xAI Grok API (ไม่มีการเชื่อมต่อถาวรในปัจจุบัน)
    try:
        logging.info("ปิดการเชื่อมต่อ xAI Grok API เรียบร้อย")
    except Exception as e:
        logging.error(f"เกิดข้อผิดพลาดในการปิดการเชื่อมต่อ xAI Grok API: {str(e)}")

    logging.info("ปิดแอปพลิเคชันเรียบร้อย")
    exit(0)

signal.signal(signal.SIGTERM, handle_shutdown)
signal.signal(signal.SIGINT, handle_shutdown)

if __name__ == "__main__":
    # เริ่มต้นตัวกำหนดการก่อนเริ่มเซิร์ฟเวอร์
    init_scheduler()
    # เริ่มเซิร์ฟเวอร์
    serve(app, host='0.0.0.0', port=5000)
