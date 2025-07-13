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
                
                # ตรวจสอบข้อผิดพลาดเฉพาะของ DeepSeek API
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

def handle_deepseek_api_error(e, user_id, user_message):
    """
    จัดการกับข้อผิดพลาดเฉพาะของ DeepSeek API และส่งข้อความที่เหมาะสมไปยังผู้ใช้
    
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

def check_hospital_inquiry(user_message):
    """ตรวจสอบว่าข้อความเป็นการสอบถามเกี่ยวกับที่ตั้งของสถานพยาบาลหรือไม่"""
    user_message_lower = user_message.lower()
    
    # คำที่เกี่ยวข้องกับสถานพยาบาล
    hospital_terms = ['โรงพยาบาล', 'สถานพยาบาล', 'รพ.', 'ร.พ.', 'คลินิก', 'hospital', 'clinic']
    
    # คำที่บ่งชี้การสอบถามที่ตั้ง/ตำแหน่ง
    location_inquiry_terms = [
        'ที่อยู่', 'อยู่ที่ไหน', 'ใกล้บ้าน', 'ใกล้ฉัน', 'ใกล้เคียง', 'ใกล้ที่สุด',
        'รักษาที่ไหน', 'ไปหาหมอ', 'พบแพทย์', 'ไปรักษา', 'ไปดู',
        'location', 'address', 'where', 'nearest', 'close to', 'near me'
    ]
    
    # คำถามที่บ่งชี้การสอบถามที่ตั้ง
    location_question_patterns = [
        'หา', 'มี', 'แนะนำ', 'ช่วย', 'บอก', 'ขอ', 'ต้องการ', 'อยาก',
        'find', 'recommend', 'suggest', 'help', 'need', 'want'
    ]
    
    # ตรวจสอบว่ามีคำเกี่ยวกับสถานพยาบาล
    has_hospital_term = any(term in user_message_lower for term in hospital_terms)
    
    # ตรวจสอบว่ามีคำบ่งชี้การสอบถามที่ตั้ง
    has_location_inquiry = any(term in user_message_lower for term in location_inquiry_terms)
    
    # ตรวจสอบว่ามีรูปแบบการถาม
    has_question_pattern = any(pattern in user_message_lower for pattern in location_question_patterns)
    
    # คืนค่า True เฉพาะเมื่อมีทั้งคำเกี่ยวกับสถานพยาบาล และมีการสอบถามที่ตั้ง
    # หรือมีคำเกี่ยวกับสถานพยาบาล + รูปแบบการถาม + คำบ่งชี้ที่ตั้ง
    return has_hospital_term and (has_location_inquiry or (has_question_pattern and any(word in user_message_lower for word in ['ไหน', 'ที่', 'แห่ง', 'สถานที่', 'where'])))

def get_hospital_information_message():
    """ส่งคืนข้อความข้อมูลสถานพยาบาล"""
    return (
        "📍 ข้อมูลสถานพยาบาล\n\n"
        "สำหรับข้อมูลที่อยู่และรายละเอียดของสถานพยาบาลในพื้นที่ของคุณ:\n\n"
        "📞 โทร 1413 - ศูนย์บริการสารสนเทศสุขภาพ กรมสนับสนุนบริการสุขภาพ\n"
        "• ให้บริการ 24 ชั่วโมง\n"
        "• มีข้อมูลสถานพยาบาลทั่วประเทศ\n"
        "• สามารถสอบถามบริการรักษายาเสพติดได้\n\n"
        "💡 เจ้าหน้าที่จะแนะนำสถานพยาบาลที่ใกล้บ้านคุณที่สุด และมีบริการที่เหมาะสมกับความต้องการของคุณ"
    )

def get_user_risk_context(user_id, db_manager):
    """ดึงข้อมูลความเสี่ยงของผู้ใช้เพื่อใช้เป็นบริบทในการสนทนา (ใช้ข้อมูลสรุปเพื่อประสิทธิภาพ)"""
    try:
        query = '''
            SELECT risk_summary, assist_scores, created_at
            FROM user_risk_assessments 
            WHERE user_id = %s
            ORDER BY created_at DESC 
            LIMIT 1
        '''
        result = db_manager.execute_query(query, (user_id,))
        
        if not result:
            return None
            
        risk_summary, assist_scores, created_at = result[0]
        
        # ใช้ข้อมูลสรุปที่กระชับแทนข้อมูลเต็ม
        context_summary = create_summarized_ai_context(
            risk_summary, assist_scores, created_at
        )
        
        return context_summary
        
    except Exception as e:
        logging.error(f"เกิดข้อผิดพลาดในการดึงข้อมูลความเสี่ยง: {str(e)}")
        return None

def create_summarized_ai_context(risk_summary, assist_scores, created_at):
    """สร้างบริบทสรุปที่กระชับสำหรับ AI"""
    try:
        context_parts = []
        
        # เพิ่มข้อมูลพื้นฐาน
        if created_at:
            context_parts.append(f"ประเมินความเสี่ยง: {created_at.strftime('%Y-%m-%d')}")
        
        # เพิ่มสรุปความเสี่ยงที่มีอยู่แล้ว
        if risk_summary:
            context_parts.append(f"สรุป: {risk_summary}")
        
        # เพิ่มข้อมูล ASSIST scores แบบกระชับ
        if assist_scores:
            try:
                import json
                if isinstance(assist_scores, str):
                    assist_scores = json.loads(assist_scores)
                
                high_risk_substances = []
                for substance, score in assist_scores.items():
                    if score > 10:  # เฉพาะความเสี่ยงสูง
                        risk_level = get_risk_level_from_score_util(substance, score)
                        if 'สูง' in risk_level:
                            high_risk_substances.append(f"{substance}({score})")
                
                if high_risk_substances:
                    context_parts.append(f"ความเสี่ยงสูง: {', '.join(high_risk_substances)}")
                    
            except Exception as e:
                logging.warning(f"ไม่สามารถแปลงข้อมูล ASSIST scores: {str(e)}")
        
        # คำแนะนำสำหรับ AI แบบกระชับ
        context_parts.append("[ใช้ข้อมูลนี้ปรับระดับการดูแลให้เหมาะสม]")
        
        return "\n".join(context_parts) if context_parts else None
        
    except Exception as e:
        logging.error(f"เกิดข้อผิดพลาดในการสร้างบริบทสรุป: {str(e)}")
        return None

def create_ai_context_from_risk_data(form_data, risk_summary, assist_scores, screening_results, created_at):
    """สร้างบริบทที่เหมาะสมสำหรับ AI จากข้อมูลความเสี่ยง"""
    try:
        context_parts = []
        
        # เพิ่มข้อมูลพื้นฐาน
        if created_at:
            context_parts.append(f"ข้อมูลการประเมินความเสี่ยง (วันที่: {created_at.strftime('%Y-%m-%d')})")
        
        # เพิ่มสรุปความเสี่ยง
        if risk_summary:
            context_parts.append(f"สรุปความเสี่ยง: {risk_summary}")
        
        # เพิ่มข้อมูล ASSIST scores ถ้ามี
        if assist_scores:
            try:
                import json
                if isinstance(assist_scores, str):
                    assist_scores = json.loads(assist_scores)
                
                context_parts.append("คะแนน ASSIST:")
                for substance, score in assist_scores.items():
                    if score > 0:
                        risk_level = get_risk_level_from_score_util(substance, score)
                        context_parts.append(f"- {substance}: {score} คะแนน ({risk_level})")
            except Exception as e:
                logging.warning(f"ไม่สามารถแปลงข้อมูล ASSIST scores: {str(e)}")
        
        # เพิ่มข้อมูลการคัดกรองถ้ามี
        if screening_results:
            try:
                import json
                if isinstance(screening_results, str):
                    screening_results = json.loads(screening_results)
                
                context_parts.append("ผลการคัดกรอง:")
                for criteria, result in screening_results.items():
                    context_parts.append(f"- {criteria}: {result}")
            except Exception as e:
                logging.warning(f"ไม่สามารถแปลงข้อมูลการคัดกรอง: {str(e)}")
        
        # สร้างคำแนะนำสำหรับ AI
        context_parts.append("\n[คำแนะนำสำหรับการสนทนา: ใช้ข้อมูลนี้เป็นบริบทในการให้คำปรึกษาและการดูแลที่เหมาะสมกับระดับความเสี่ยงของผู้ใช้]")
        
        return "\n".join(context_parts) if context_parts else None
        
    except Exception as e:
        logging.error(f"เกิดข้อผิดพลาดในการสร้างบริบท AI: {str(e)}")
        return None

def get_risk_level_from_score_util(substance, score):
    """แปลคะแนน ASSIST เป็นระดับความเสี่ยง (utility function)"""
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

def summarize_user_risk_profile(user_id, db_manager, deepseek_client, config):
    """สร้างสรุปโปรไฟล์ความเสี่ยงของผู้ใช้โดยใช้ AI"""
    try:
        # ดึงข้อมูลความเสี่ยงแบบดิบ
        risk_context = get_user_risk_context(user_id, db_manager)
        
        if not risk_context:
            return "ไม่มีข้อมูลการประเมินความเสี่ยง"
        
        # สร้าง prompt สำหรับ AI
        summary_prompt = f"""
จากข้อมูลการประเมินความเสี่ยงต่อไปนี้:

{risk_context}

โปรดสร้างสรุปโปรไฟล์ความเสี่ยงของผู้ใช้ที่:
1. กระชับและเข้าใจง่าย
2. เน้นประเด็นสำคัญที่ควรให้ความสนใจ
3. ให้คำแนะนำเบื้องต้นที่เหมาะสม
4. ใช้ภาษาไทยที่เป็นมิตรและไม่ทำให้เกิดความกังวล
5. ความยาวไม่เกิน 200 คำ

สรุปโปรไฟล์ความเสี่ยง:"""

        # เรียกใช้ AI
        response = deepseek_client.chat.completions.create(
            model=config.DEEPSEEK_MODEL,
            messages=[
                {"role": "system", "content": "คุณคือผู้เชี่ยวชาญด้านการประเมินความเสี่ยงสารเสพติดที่ให้คำปรึกษาด้วยความเข้าใจและเป็นมิตร"},
                {"role": "user", "content": summary_prompt}
            ],
            temperature=0.3,
            max_tokens=300
        )
        
        if response and response.choices and response.choices[0].message.content:
            summary = response.choices[0].message.content.strip()
            summary = clean_ai_response(summary)
            return summary
        
        # Fallback ถ้า AI ไม่ตอบ
        return risk_context
        
    except Exception as e:
        logging.error(f"เกิดข้อผิดพลาดในการสร้างสรุปโปรไฟล์ความเสี่ยง: {str(e)}")
        return "ไม่สามารถสร้างสรุปโปรไฟล์ความเสี่ยงได้"
