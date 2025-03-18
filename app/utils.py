"""
โมดูลยูทิลิตี้สำหรับแชทบอท 'ใจดี'
รวมฟังก์ชันช่วยเหลือและเดโครเรเตอร์ต่างๆ
"""
import functools
import logging
import traceback
import time
from typing import Callable, Any, TypeVar, cast, Dict

# ตัวแปรประเภทสำหรับฟังก์ชัน
F = TypeVar('F', bound=Callable[..., Any])

def safe_db_operation(func: F) -> F:
    """
    เดโครเรเตอร์สำหรับการดำเนินการฐานข้อมูลแบบปลอดภัย
    
    Args:
        func: ฟังก์ชันฐานข้อมูลที่ต้องการห่อหุ้ม
        
    Returns:
        F: ฟังก์ชันที่ห่อหุ้มแล้ว
    """
    @functools.wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return func(*args, **kwargs)
        except Exception as e:
            logging.error(f"เกิดข้อผิดพลาดของฐานข้อมูลใน {func.__name__}: {str(e)}")
            logging.debug(traceback.format_exc())
            
            # คืนค่าเริ่มต้นที่เหมาะสมตามชื่อฟังก์ชัน
            if func.__name__.startswith('get_'):
                # สำหรับฟังก์ชันการดึงข้อมูล
                if 'count' in func.__name__:
                    return 0
                return None
            # สำหรับฟังก์ชันการบันทึกหรือการอัพเดท
            return False
    return cast(F, wrapper)

def safe_api_call(func: F) -> F:
    """
    เดโครเรเตอร์สำหรับการเรียก API แบบปลอดภัยพร้อมลอจิกการลองใหม่
    
    Args:
        func: ฟังก์ชันเรียก API ที่ต้องการห่อหุ้ม
        
    Returns:
        F: ฟังก์ชันที่ห่อหุ้มแล้ว
    """
    @functools.wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        max_retries = 3
        retry_count = 0
        last_error = None
        
        while retry_count < max_retries:
            try:
                return func(*args, **kwargs)
            except Exception as e:
                last_error = e
                retry_count += 1
                wait_time = 2 ** retry_count  # Exponential backoff
                logging.warning(f"การเรียก API ล้มเหลว (ความพยายามที่ {retry_count}/{max_retries}): {str(e)}")
                
                if retry_count >= max_retries:
                    logging.error(f"การเรียก API ล้มเหลวทั้งหมด: {str(e)}")
                    # คืนค่า None หรือคืนค่าข้อผิดพลาด ขึ้นอยู่กับความต้องการ
                    if kwargs.get('raise_error', False):
                        raise last_error
                    return None
                    
                # รอก่อนที่จะลองใหม่
                time.sleep(wait_time)
    return cast(F, wrapper)

def format_timestamp(timestamp: float, format_str: str = '%Y-%m-%d %H:%M:%S') -> str:
    """
    จัดรูปแบบของเวลาในรูปแบบที่อ่านได้
    
    Args:
        timestamp (float): เวลาในรูปแบบ UNIX timestamp
        format_str (str): รูปแบบการแสดงผล
        
    Returns:
        str: สตริงของเวลาที่จัดรูปแบบแล้ว
    """
    import datetime
    return datetime.datetime.fromtimestamp(timestamp).strftime(format_str)

def validate_line_user_id(user_id: str) -> bool:
    """
    ตรวจสอบว่า LINE user ID มีรูปแบบที่ถูกต้อง
    
    Args:
        user_id (str): LINE user ID ที่ต้องการตรวจสอบ
        
    Returns:
        bool: True ถ้ารูปแบบถูกต้อง, False ถ้าไม่ถูกต้อง
    """
    import re
    # LINE user IDs มีรูปแบบตัวอักษร/ตัวเลข 33 ตัว
    return bool(re.match(r'^U[a-f0-9]{32}$', user_id))

def sanitize_input(text: str) -> str:
    """
    ทำความสะอาดข้อความอินพุตเพื่อความปลอดภัย
    
    Args:
        text (str): ข้อความอินพุต
        
    Returns:
        str: ข้อความที่ทำความสะอาดแล้ว
    """
    # ลบอักขระที่อาจเป็นอันตรายหรือใช้สำหรับการฉีด (injection)
    import re
    # ลบอักขระพิเศษที่อาจใช้สำหรับการฉีด SQL หรือถูกใช้ในการโจมตี
    cleaned = re.sub(r'[;<>&$()]', '', text)
    # จำกัดความยาว
    return cleaned[:1000]

def mask_sensitive_data(logs: Dict[str, Any]) -> Dict[str, Any]:
    """
    ปกปิดข้อมูลที่ละเอียดอ่อนในบันทึก
    
    Args:
        logs (Dict[str, Any]): ข้อมูลบันทึกต้นฉบับ
        
    Returns:
        Dict[str, Any]: ข้อมูลบันทึกที่ปกปิดแล้ว
    """
    masked_logs = logs.copy()
    sensitive_fields = ['api_key', 'password', 'secret', 'token', 'access_token']
    
    # ฟังก์ชันเพื่อปกปิดค่าในฟิลด์ที่ละเอียดอ่อน
    def mask_value(key: str, value: Any) -> Any:
        if isinstance(value, dict):
            return {k: mask_value(k, v) for k, v in value.items()}
        elif isinstance(value, list):
            return [mask_value('item', item) for item in value]
        elif isinstance(value, str) and any(field in key.lower() for field in sensitive_fields):
            if len(value) > 8:
                return value[:4] + '*' * (len(value) - 8) + value[-4:]
            else:
                return '*' * len(value)
        return value
    
    return {k: mask_value(k, v) for k, v in masked_logs.items()}

def calculate_message_priority(message: str) -> int:
    """
    คำนวณความสำคัญของข้อความตามเนื้อหา
    
    Args:
        message (str): ข้อความที่ต้องการประเมิน
        
    Returns:
        int: คะแนนความสำคัญ (1-10)
    """
    priority = 5  # ค่าความสำคัญเริ่มต้นปานกลาง
    
    # คำที่บ่งชี้ความสำคัญสูง
    high_priority_words = [
        'ฆ่าตัวตาย', 'ทำร้ายตัวเอง', 'อยากตาย', 'overdose', 'od', 'ฉุกเฉิน', 
        'ช่วยด่วน', 'เลือดออก', 'หายใจไม่ออก', 'โคม่า', 'ชัก'
    ]
    
    # คำที่บ่งชี้ความสำคัญปานกลาง
    medium_priority_words = [
        'เครียด', 'ซึมเศร้า', 'กังวล', 'ไม่อยากมีชีวิตอยู่', 'ทรมาน',
        'เสพติด', 'กลับไปเสพ', 'อยากเสพ', 'cravings'
    ]
    
    # ตรวจสอบคำสำคัญ
    message_lower = message.lower()
    
    # เพิ่มความสำคัญตามคำที่พบ
    for word in high_priority_words:
        if word in message_lower:
            priority += 3
    
    for word in medium_priority_words:
        if word in message_lower:
            priority += 1
    
    # จำกัดค่าสูงสุดที่ 10
    return min(priority, 10)