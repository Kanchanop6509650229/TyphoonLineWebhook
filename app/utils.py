"""
โมดูลยูทิลิตี้สำหรับแชทบอท 'ใจดี'
รวมฟังก์ชันช่วยเหลือและเดโครเรเตอร์ต่างๆ
"""
import functools
import re
import logging
import traceback
import time
import requests
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
    เดโครเรเตอร์สำหรับการเรียก API แบบปลอดภัยพร้อมลอจิกการลองใหม่ที่ปรับปรุงแล้ว
    
    Args:
        func: ฟังก์ชันเรียก API ที่ต้องการห่อหุ้ม
        
    Returns:
        F: ฟังก์ชันที่ห่อหุ้มแล้ว
    """
    @functools.wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        max_retries = kwargs.pop('max_retries', 3)
        retry_count = 0
        last_error = None
        
        # บันทึก log เริ่มต้นการเรียก API
        func_name = func.__name__
        logging.debug(f"เริ่มเรียก API: {func_name}")
        
        while retry_count < max_retries:
            try:
                start_time = time.time()
                response = func(*args, **kwargs)
                execution_time = time.time() - start_time
                
                # บันทึก log เวลาการทำงาน
                logging.debug(f"API {func_name} ทำงานเสร็จใน {execution_time:.2f} วินาที")
                
                # ตรวจสอบความถูกต้องของ response
                if response is None:
                    raise ValueError(f"API {func_name} ส่งคืนค่า None")
                
                return response
                
            except (requests.exceptions.Timeout, 
                    requests.exceptions.ConnectionError,
                    requests.exceptions.HTTPError) as e:
                # ข้อผิดพลาดเครือข่าย ลองใหม่ได้
                last_error = e
                retry_count += 1
                wait_time = 2 ** retry_count  # Exponential backoff
                
                logging.warning(
                    f"เกิดข้อผิดพลาดเครือข่ายใน {func_name} "
                    f"(ความพยายามที่ {retry_count}/{max_retries}): {str(e)}"
                )
                
                if retry_count < max_retries:
                    logging.info(f"รอ {wait_time} วินาทีก่อนลองอีกครั้ง...")
                    time.sleep(wait_time)
                
            except Exception as e:
                # ข้อผิดพลาดอื่นๆ ที่ไม่ใช่เครือข่าย
                error_msg = str(e)
                logging.error(f"ข้อผิดพลาดใน {func_name}: {error_msg}")
                
                # ตรวจสอบข้อผิดพลาดเฉพาะของ Together API
                if "rate limit" in error_msg.lower():
                    last_error = e
                    retry_count += 1
                    wait_time = 5 * retry_count  # Rate limit ต้องรอนานกว่า
                    
                    if retry_count < max_retries:
                        logging.warning(f"พบข้อจำกัดอัตรา API รอ {wait_time} วินาทีก่อนลองใหม่...")
                        time.sleep(wait_time)
                    continue
                
                # ข้อผิดพลาดอื่นๆ ที่ไม่ใช่ rate limit ไม่ต้องลองใหม่
                if kwargs.get('raise_error', False):
                    raise
                
                # ส่งคืนผลลัพธ์เริ่มต้นที่เหมาะสม
                return kwargs.get('default_value', None)
        
        # หลังจากลองครบตามจำนวนครั้งแล้วยังไม่สำเร็จ
        if retry_count >= max_retries:
            logging.error(f"การเรียก {func_name} ล้มเหลวหลังจากลอง {max_retries} ครั้ง")
            
            if kwargs.get('raise_error', False):
                raise last_error or ValueError(f"Failed after {max_retries} retries")
                
            # ส่งคืนผลลัพธ์เริ่มต้นที่เหมาะสม
            return kwargs.get('default_value', None)
            
    return cast(F, wrapper)

def handle_together_api_error(e, user_id, user_message):
    """
    จัดการกับข้อผิดพลาดเฉพาะของ Together API และส่งข้อความที่เหมาะสมไปยังผู้ใช้
    
    Args:
        e (Exception): ข้อผิดพลาดที่เกิดขึ้น
        user_id (str): LINE user ID
        user_message (str): ข้อความของผู้ใช้
        
    Returns:
        str: ข้อความแจ้งข้อผิดพลาดที่ควรส่งให้ผู้ใช้
    """
    error_str = str(e).lower()
    
    # ข้อผิดพลาดเกี่ยวกับการเกินขีดจำกัดอัตรา (rate limit)
    if "rate limit" in error_str or "too many requests" in error_str:
        logging.warning(f"ผู้ใช้ {user_id} พบข้อจำกัดอัตรา API")
        return (
            "ขออภัยค่ะ ระบบกำลังมีการใช้งานสูง กรุณารอสักครู่และลองอีกครั้ง\n"
            "น้องใจดีจะรีบกลับมาช่วยเหลือคุณโดยเร็วที่สุด"
        )
    
    # ข้อผิดพลาดเกี่ยวกับการเชื่อมต่อ
    elif any(keyword in error_str for keyword in ["timeout", "connection", "network", "socket"]):
        logging.error(f"เกิดปัญหาการเชื่อมต่อสำหรับผู้ใช้ {user_id}: {error_str}")
        return (
            "ขออภัยค่ะ เกิดปัญหาในการเชื่อมต่อกับระบบ\n"
            "กรุณาลองอีกครั้งในอีกสักครู่ หากยังมีปัญหา โปรดติดต่อผู้ดูแลระบบ"
        )
    
    # ข้อผิดพลาดเกี่ยวกับเนื้อหาที่ถูกกรอง (content filtering)
    elif any(keyword in error_str for keyword in ["content filter", "filtered", "moderation"]):
        logging.warning(f"เนื้อหาไม่เหมาะสมจากผู้ใช้ {user_id}: {user_message[:100]}...")
        return (
            "ขออภัยค่ะ ระบบไม่สามารถตอบคำถามนี้ได้เนื่องจากนโยบายการใช้งาน\n"
            "กรุณาสอบถามในหัวข้อที่เกี่ยวกับการเลิกสารเสพติดและการบำบัด"
        )
    
    # ข้อผิดพลาดทั่วไป
    else:
        logging.error(f"ข้อผิดพลาด API ที่ไม่คาดคิดสำหรับผู้ใช้ {user_id}: {error_str}")
        return (
            "ขออภัยค่ะ เกิดข้อผิดพลาดในการประมวลผล\n"
            "น้องใจดีกำลังหาทางแก้ไข กรุณาลองใหม่อีกครั้งในอีกสักครู่"
        )

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

def clean_ai_response(response_text):
    """
    ทำความสะอาด response จาก AI model ก่อนส่งไปให้ผู้ใช้
    
    ลบ markup และข้อความที่ไม่ต้องการต่างๆ เช่น
    - แท็ก <|start_header_id|>assistant<|end_header_id|>
    - คำว่า "assistant" ที่ขึ้นต้นข้อความ
    
    Args:
        response_text (str): ข้อความตอบกลับจาก AI model
        
    Returns:
        str: ข้อความที่ทำความสะอาดแล้ว
    """
    if not response_text:
        return ""
    
    # เก็บข้อความต้นฉบับเพื่อตรวจสอบการเปลี่ยนแปลง
    original_text = response_text
    
    try:
        # 1. ลบแท็ก <|start_header_id|>assistant<|end_header_id|> และแท็กที่คล้ายกัน
        response_text = re.sub(r'<\|start_header_id\|>.*?<\|end_header_id\|>\s*', '', response_text)
        
        # 2. ลบรูปแบบแท็กอื่นๆ ที่อาจเกิดขึ้น
        response_text = re.sub(r'<\|.*?\|>', '', response_text)
        
        # 3. ลบคำว่า "assistant" ที่ขึ้นต้นข้อความ
        # รูปแบบต่างๆ ที่อาจเกิดขึ้น:
        # - "assistant" อยู่บรรทัดแรกเพียงอย่างเดียว
        # - "assistant" ตามด้วยบรรทัดว่าง
        # - "assistant" ขึ้นต้นข้อความโดยไม่มีบรรทัดว่าง
        response_text = re.sub(r'^assistant\s*\n+', '', response_text, flags=re.IGNORECASE)
        response_text = re.sub(r'^assistant\s*[:]\s*', '', response_text, flags=re.IGNORECASE)
        response_text = re.sub(r'^assistant\s+', '', response_text, flags=re.IGNORECASE)
        
        # 4. ลบช่องว่างและบรรทัดว่างที่มากเกินไป
        response_text = re.sub(r'\n{3,}', '\n\n', response_text)  # ลดบรรทัดว่างที่ติดกันมากกว่า 2 บรรทัด
        response_text = response_text.strip()  # ลบช่องว่างหัวท้าย
        
        # หากมีการเปลี่ยนแปลง ให้บันทึกลง log
        if original_text != response_text:
            logging.info(f"ทำความสะอาด response แล้ว: ลบแท็กและข้อความที่ไม่ต้องการออก")
            
        return response_text
        
    except Exception as e:
        # หากเกิดข้อผิดพลาด กลับไปใช้ข้อความเดิม
        logging.error(f"เกิดข้อผิดพลาดในการทำความสะอาด response: {str(e)}")
        return original_text
