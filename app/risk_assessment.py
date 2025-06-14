"""Risk assessment utilities for the Jai Dee chatbot."""
import json
import os
import json
import logging
from datetime import datetime
from typing import Dict, Tuple, List

redis_client = None

# Default keywords can be overridden by loading from a JSON file
# via the `RISK_KEYWORDS_PATH` environment variable.

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


def load_risk_keywords(path: str) -> None:
    """Load risk keywords from a JSON file."""
    global RISK_KEYWORDS
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict):
                RISK_KEYWORDS = data
                logging.info(f"Loaded risk keywords from {path}")
    except Exception as e:
        logging.error(f"Could not load risk keywords from {path}: {str(e)}")


def init_risk_assessment(redis_instance) -> None:
    """Initialize Redis client and load custom risk keywords."""
    global redis_client
    redis_client = redis_instance

    keywords_path = os.getenv("RISK_KEYWORDS_PATH")
    if keywords_path:
        load_risk_keywords(keywords_path)


def assess_risk(message: str) -> Tuple[str, List[str]]:
    """Assess risk level from message."""
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
