"""Session management utilities for the Jai Dee chatbot."""
import json
import logging
from datetime import datetime
from typing import List, Dict, Tuple

redis_client = None
line_bot_api = None
token_counter = None
SESSION_TIMEOUT = 604800

def init_session_manager(redis_instance, line_api, token_counter_instance, session_timeout: int = 604800):
    """Initialize session manager dependencies."""
    global redis_client, line_bot_api, token_counter, SESSION_TIMEOUT
    redis_client = redis_instance
    line_bot_api = line_api
    token_counter = token_counter_instance
    SESSION_TIMEOUT = session_timeout


def get_chat_session(user_id: str) -> List[Dict[str, str]]:
    """Retrieve chat session history from Redis."""
    try:
        history = redis_client.get(f"chat_session:{user_id}")
        if history:
            loaded_history = json.loads(history)
            return [
                {"role": msg_data["role"], "content": msg_data["content"]}
                for msg_data in loaded_history
            ]
        return []
    except Exception as e:  # redis.RedisError or others
        logging.error(f"Redis error in get_chat_session: {str(e)}")
        return []


def save_chat_session(user_id: str, messages: List[Dict[str, str]]) -> None:
    """Save chat session history to Redis."""
    try:
        max_messages = 100
        serialized_history = [
            {"role": msg["role"], "content": msg["content"]}
            for msg in messages[-max_messages:]
        ]
        redis_client.setex(
            f"chat_session:{user_id}",
            3600 * 24,
            json.dumps(serialized_history),
        )
        token_count = token_counter.count_message_tokens(serialized_history)
        redis_client.setex(
            f"session_tokens:{user_id}",
            3600 * 24,
            str(token_count),
        )
        logging.debug(
            f"บันทึกเซสชัน: {len(serialized_history)} ข้อความ, {token_count} โทเค็น สำหรับผู้ใช้ {user_id}"
        )
    except Exception as e:
        logging.error(f"Redis error in save_chat_session: {str(e)}")


def check_session_timeout(user_id: str) -> bool:
    """Check whether the session has timed out."""
    try:
        last_activity = redis_client.get(f"last_activity:{user_id}")
        if last_activity:
            if isinstance(last_activity, bytes):
                last_activity = last_activity.decode("utf-8")
            last_activity_time = float(last_activity)
            if (datetime.now().timestamp() - last_activity_time) > SESSION_TIMEOUT:
                redis_client.delete(f"chat_session:{user_id}")
                return True
        return False
    except Exception as e:
        logging.error(
            f"เกิดข้อผิดพลาดในการตรวจสอบ session timeout สำหรับผู้ใช้ {user_id}: {str(e)}"
        )
        return False


def update_last_activity(user_id: str) -> None:
    """Update last activity timestamp and send timeout warnings."""
    try:
        current_time = datetime.now().timestamp()
        last_activity = redis_client.get(f"last_activity:{user_id}")
        warning_sent = redis_client.get(f"timeout_warning:{user_id}")

        if isinstance(last_activity, bytes):
            last_activity = last_activity.decode("utf-8")
        if isinstance(warning_sent, bytes):
            warning_sent = warning_sent.decode("utf-8")

        if last_activity:
            time_passed = current_time - float(last_activity)
            if time_passed > (SESSION_TIMEOUT - 86400) and not warning_sent:
                warning_message = (
                    "⚠️ เซสชันของคุณจะหมดอายุในอีก 1 วัน\n"
                    "หากต้องการคุยต่อ กรุณาพิมพ์ข้อความใดๆ เพื่อต่ออายุเซสชัน"
                )
                line_bot_api.push_message(user_id, TextSendMessage(text=warning_message))
                redis_client.setex(
                    f"timeout_warning:{user_id}",
                    86400,
                    "1",
                )
                logging.info(f"ส่งการแจ้งเตือนหมดเวลาเซสชันไปยังผู้ใช้: {user_id}")

        redis_client.setex(
            f"last_activity:{user_id}",
            SESSION_TIMEOUT,
            str(current_time),
        )
    except Exception as e:
        logging.error(
            f"เกิดข้อผิดพลาดในการอัพเดทเวลาใช้งานล่าสุดสำหรับผู้ใช้ {user_id}: {str(e)}"
        )


def get_session_token_count(user_id: str) -> int:
    """Calculate token usage for the current session."""
    try:
        cached_count = redis_client.get(f"session_tokens:{user_id}")
        if cached_count:
            return int(cached_count)
        session_data = redis_client.get(f"chat_session:{user_id}")
        if not session_data:
            return 0
        messages = json.loads(session_data)
        token_count = token_counter.count_message_tokens(messages)
        redis_client.setex(
            f"session_tokens:{user_id}",
            3600 * 24,
            str(token_count),
        )
        return token_count
    except Exception as e:
        logging.error(f"เกิดข้อผิดพลาดในการคำนวณโทเค็นของเซสชัน: {str(e)}")
        return 0


def is_important_message(user_message: str, bot_response: str) -> bool:
    """Determine if a message pair is important."""
    important_keywords = [
        'ฆ่าตัวตาย', 'ทำร้ายตัวเอง', 'อยากตาย',
        'overdose', 'เกินขนาด', 'ก้าวร้าว',
        'ซึมเศร้า', 'วิตกกังวล', 'ความทรงจำ',
        'ไม่มีความสุข', 'ทรมาน', 'เครียด',
        'เลิก', 'หยุด', 'อดทน', 'ยา', 'เสพ',
        'บำบัด', 'กลับไปเสพ', 'อาการ', 'ถอนยา'
    ]
    combined_text = (user_message + " " + bot_response).lower()
    for keyword in important_keywords:
        if keyword.lower() in combined_text:
            return True
    if len(user_message) > 300 or len(bot_response) > 500:
        return True
    return False


def hybrid_context_management(user_id: str, token_threshold: int) -> List[Dict[str, str]]:
    """Manage conversation history to fit within the context window."""
    try:
        current_history = get_chat_session(user_id)
        if not current_history:
            return []
        current_tokens = get_session_token_count(user_id)
        if current_tokens < token_threshold:
            return current_history
        logging.info(
            f"เซสชันใกล้เต็ม context window ({current_tokens} tokens) สำหรับผู้ใช้ {user_id}, กำลังจัดการประวัติ..."
        )
        keep_recent = 30
        if len(current_history) <= keep_recent * 2:
            return current_history
        recent_messages = current_history[-keep_recent*2:]
        older_messages = current_history[:-keep_recent*2]
        if older_messages:
            important_pairs = []
            normal_pairs = []
            for i in range(0, len(older_messages), 2):
                if i+1 < len(older_messages):
                    user_msg = older_messages[i].get("content", "")
                    bot_resp = older_messages[i+1].get("content", "")
                    if is_important_message(user_msg, bot_resp):
                        important_pairs.append((user_msg, bot_resp))
                    else:
                        normal_pairs.append((user_msg, bot_resp))
            important_messages = []
            for user_msg, bot_resp in important_pairs:
                important_messages.append({"role": "user", "content": user_msg})
                important_messages.append({"role": "assistant", "content": bot_resp})
            formatted_normal = []
            for i, (user_msg, bot_resp) in enumerate(normal_pairs):
                formatted_normal.append((i, user_msg, bot_resp))
            summary = ""
            if formatted_normal:
                from .app_main import summarize_conversation_history
                summary = summarize_conversation_history(formatted_normal)
            new_history = []
            if summary:
                new_history.append({"role": "assistant", "content": f"สรุปการสนทนาก่อนหน้า: {summary}"})
            new_history.extend(important_messages)
            new_history.extend(recent_messages)
            save_chat_session(user_id, new_history)
            return new_history
        return current_history
    except Exception as e:
        logging.error(f"เกิดข้อผิดพลาดในการจัดการประวัติ: {str(e)}")
        return current_history
