"""Risk assessment utilities for the Jai Dee chatbot."""
import json
import logging
from datetime import datetime
from typing import Dict, Tuple, List

redis_client = None

# คำสำคัญที่ใช้ประเมินความเสี่ยงจากข้อความของผู้ใช้
# เพิ่มคำที่เกี่ยวข้องกับการใช้สารเสพติดและอาการซึมเศร้าเพิ่มเติม
RISK_KEYWORDS = {
    "high_risk": [
        "ฆ่าตัวตาย", "ทำร้ายตัวเอง", "อยากตาย", "อยากจบชีวิต",
        "เกินขนาด", "overdose", "od",
        "เลือดออก", "ชัก", "หมดสติ"
    ],
    "medium_risk": [
        "นอนไม่หลับ", "เครียด", "กังวล", "วิตกกังวล",
        "ซึมเศร้า", "เหงา", "ท้อแท้", "สิ้นหวัง",
        "อยากใช้ยา", "อยากเสพ", "อยากกลับไปเสพ"
    ],
}

# จำนวนคำความเสี่ยงระดับปานกลางที่จะยกระดับเป็นความเสี่ยงสูง
MEDIUM_RISK_THRESHOLD = 2


def init_risk_assessment(redis_instance) -> None:
    """Initialize Redis client for risk assessment."""
    global redis_client
    redis_client = redis_instance


def assess_risk(message: str) -> Tuple[str, List[str]]:
    """Assess risk level from message.

    ระดับความเสี่ยงจะถูกยกระดับเป็น "high" หากพบคำความเสี่ยงระดับสูง
    หรือพบคำความเสี่ยงระดับปานกลางหลายคำในข้อความเดียวกัน
    """
    message = message.lower()
    matched_keywords: List[str] = []

    # ตรวจหาคำความเสี่ยงสูง
    for keyword in RISK_KEYWORDS["high_risk"]:
        if keyword in message:
            matched_keywords.append(keyword)

    if matched_keywords:
        return "high", matched_keywords

    # ตรวจหาคำความเสี่ยงปานกลาง
    medium_matches = [kw for kw in RISK_KEYWORDS["medium_risk"] if kw in message]
    matched_keywords.extend(medium_matches)

    if len(medium_matches) >= MEDIUM_RISK_THRESHOLD:
        return "high", matched_keywords
    elif medium_matches:
        return "medium", matched_keywords

    return "low", matched_keywords


def save_progress_data(user_id: str, risk_level: str, keywords: List[str]) -> None:
    """Save user progress data to Redis."""
    try:
        progress_data = {
            'timestamp': datetime.now().isoformat(),
            'risk_level': risk_level,
            'keywords': keywords
        }
        redis_client.lpush(f"progress:{user_id}", json.dumps(progress_data))
        redis_client.ltrim(f"progress:{user_id}", 0, 99)
    except Exception as e:
        logging.error(f"เกิดข้อผิดพลาดในการบันทึกความก้าวหน้า: {str(e)}")


def generate_progress_report(user_id: str) -> str:
    """Generate a progress report for the user."""
    try:
        progress_data = redis_client.lrange(f"progress:{user_id}", 0, -1)
        if not progress_data:
            return "ยังไม่มีข้อมูลความก้าวหน้า"

        data = [json.loads(item) for item in progress_data]
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
