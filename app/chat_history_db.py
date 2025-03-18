"""
โมดูลฐานข้อมูลประวัติการแชทสำหรับแชทบอท 'ใจดี'
"""
from datetime import datetime
import logging
from .utils import safe_db_operation
from .token_counter import TokenCounter

class ChatHistoryDB:
    """
    คลาสสำหรับจัดการการดำเนินการกับฐานข้อมูลประวัติการแชท
    """
    
    def __init__(self, mysql_pool):
        """
        สร้างอินสแตนซ์ของ ChatHistoryDB
        
        Args:
            mysql_pool: MySQL connection pool
        """
        self.pool = mysql_pool
        self.counter = TokenCounter()
        logging.info("ChatHistoryDB initialized")
        
    def get_connection(self):
        """
        ดึงการเชื่อมต่อฐานข้อมูลจาก pool
        
        Returns:
            Connection: การเชื่อมต่อฐานข้อมูล
        """
        return self.pool.get_connection()
        
    @safe_db_operation
    def get_user_history(self, user_id, max_tokens=10000):
        """
        ดึงประวัติการสนทนาของผู้ใช้แบบเหมาะสม
        
        Args:
            user_id (str): LINE User ID
            max_tokens (int): จำนวนโทเค็นสูงสุดในประวัติ
            
        Returns:
            list: ประวัติการสนทนาที่เลือก
        """
        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            
            # First get important messages using a single query with indexing
            cursor.execute('''
                SELECT c.id, c.timestamp, c.user_message, c.bot_response, c.token_count 
                FROM conversations c
                WHERE c.user_id = %s
                ORDER BY 
                    c.important_flag DESC, -- Important messages first
                    c.timestamp DESC -- Then most recent
                LIMIT 50 -- Reasonable limit to process
            ''', (user_id,))
            
            all_messages = cursor.fetchall()
            
            # Apply token limit
            selected_history = []
            total_tokens = 0
            
            for msg in all_messages:
                msg_tokens = msg[4] or self.counter.count_tokens(msg[2] + msg[3])
                if total_tokens + msg_tokens <= max_tokens:
                    selected_history.append((msg[0], msg[2], msg[3]))
                    total_tokens += msg_tokens
                else:
                    break
                    
            return selected_history
        finally:
            cursor.close()
            conn.close()

    @safe_db_operation
    def save_conversation(self, user_id, user_message, bot_response, token_count=0, important=False):
        """
        บันทึกการสนทนาเดี่ยวลงในฐานข้อมูล
        
        Args:
            user_id (str): LINE User ID
            user_message (str): ข้อความของผู้ใช้
            bot_response (str): การตอบกลับของบอท
            token_count (int): จำนวนโทเค็นที่ใช้
            important (bool): ความสำคัญของข้อความ
            
        Returns:
            bool: True หากสำเร็จ
        """
        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            
            # ตรวจสอบความสำคัญถ้าไม่มีการระบุ
            if not isinstance(important, bool):
                important = self._check_message_importance(user_message, bot_response)
            
            cursor.execute('''
                INSERT INTO conversations 
                (user_id, timestamp, user_message, bot_response, token_count, important_flag)
                VALUES (%s, %s, %s, %s, %s, %s)
            ''', (
                user_id, 
                datetime.now(), 
                user_message, 
                bot_response, 
                token_count,
                important
            ))
            
            conn.commit()
            return True
        except Exception as e:
            conn.rollback()
            logging.error(f"Error saving conversation: {str(e)}")
            raise
        finally:
            cursor.close()
            conn.close()
            
    @safe_db_operation
    def save_batch_conversations(self, conversations):
        """
        บันทึกหลายการสนทนาพร้อมกันเพื่อประสิทธิภาพ
        
        Args:
            conversations (list): รายการข้อมูลการสนทนา
            
        Returns:
            bool: True หากสำเร็จ
        """
        if not conversations:
            return True
            
        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            
            # Prepare batch values
            values = []
            for conv in conversations:
                important = conv.get('important', self._check_message_importance(
                    conv['user_message'], conv['bot_response']
                ))
                
                values.append((
                    conv['user_id'],
                    conv.get('timestamp', datetime.now()),
                    conv['user_message'],
                    conv['bot_response'],
                    conv.get('token_count', 0),
                    important
                ))
            
            # Execute batch insert
            cursor.executemany('''
                INSERT INTO conversations 
                (user_id, timestamp, user_message, bot_response, token_count, important_flag)
                VALUES (%s, %s, %s, %s, %s, %s)
            ''', values)
            
            conn.commit()
            return True
        except Exception as e:
            conn.rollback()
            raise
        finally:
            cursor.close()
            conn.close()
    
    def _check_message_importance(self, user_message, bot_response):
        """
        ตรวจสอบความสำคัญของข้อความตามเนื้อหา
        
        Args:
            user_message (str): ข้อความของผู้ใช้
            bot_response (str): คำตอบของบอท
            
        Returns:
            bool: True หากข้อความสำคัญ
        """
        # ตรวจสอบคำสำคัญในข้อความของผู้ใช้
        important_keywords = [
            'ฆ่าตัวตาย', 'ทำร้ายตัวเอง', 'อยากตาย', 
            'overdose', 'เกินขนาด', 'ก้าวร้าว',
            'ซึมเศร้า', 'วิตกกังวล', 'ความทรงจำ', 
            'ไม่มีความสุข', 'ทรมาน', 'เครียด',
            'เลิก', 'หยุด', 'อดทน'
        ]
        
        combined_text = (user_message + " " + bot_response).lower()
        for keyword in important_keywords:
            if keyword.lower() in combined_text:
                return True
                
        # ตรวจสอบความยาวของข้อความ (ข้อความที่ยาวมักมีเนื้อหาสำคัญ)
        if len(user_message) > 300 or len(bot_response) > 500:
            return True
            
        return False
    
    @safe_db_operation
    def get_user_history_count(self, user_id):
        """
        นับจำนวนการสนทนาของผู้ใช้
        
        Args:
            user_id (str): LINE User ID
            
        Returns:
            int: จำนวนบันทึกการสนทนา
        """
        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute('SELECT COUNT(*) FROM conversations WHERE user_id = %s', (user_id,))
            count = cursor.fetchone()[0]
            return count
        finally:
            cursor.close()
            conn.close()
    
    @safe_db_operation
    def get_important_message_count(self, user_id):
        """
        นับจำนวนข้อความสำคัญของผู้ใช้
        
        Args:
            user_id (str): LINE User ID
            
        Returns:
            int: จำนวนข้อความสำคัญ
        """
        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute('SELECT COUNT(*) FROM conversations WHERE user_id = %s AND important_flag = TRUE', (user_id,))
            count = cursor.fetchone()[0]
            return count
        finally:
            cursor.close() 
            conn.close()
    
    @safe_db_operation
    def get_last_interaction(self, user_id):
        """
        ดึงเวลาของการสนทนาล่าสุด
        
        Args:
            user_id (str): LINE User ID
            
        Returns:
            str: เวลาในรูปแบบ string หรือ "ไม่มีข้อมูล"
        """
        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute('SELECT MAX(timestamp) FROM conversations WHERE user_id = %s', (user_id,))
            timestamp = cursor.fetchone()[0]
            
            if timestamp:
                return timestamp.strftime('%Y-%m-%d %H:%M:%S')
            return "ไม่มีข้อมูล"
        finally:
            cursor.close()
            conn.close()
    
    @safe_db_operation
    def get_total_tokens(self, user_id):
        """
        คำนวณจำนวนโทเค็นทั้งหมดที่ใช้งานโดยผู้ใช้
        
        Args:
            user_id (str): LINE User ID
            
        Returns:
            int: จำนวนโทเค็นทั้งหมด
        """
        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute('SELECT SUM(token_count) FROM conversations WHERE user_id = %s', (user_id,))
            total = cursor.fetchone()[0]
            return total or 0
        finally:
            cursor.close()
            conn.close()
    
    @safe_db_operation
    def clear_user_history(self, user_id):
        """
        ลบประวัติการสนทนาทั้งหมดของผู้ใช้
        
        Args:
            user_id (str): LINE User ID
            
        Returns:
            bool: True หากสำเร็จ
        """
        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute('DELETE FROM conversations WHERE user_id = %s', (user_id,))
            conn.commit()
            return True
        except Exception as e:
            conn.rollback()
            logging.error(f"Error clearing user history: {str(e)}")
            raise
        finally:
            cursor.close()
            conn.close()
    
    @safe_db_operation
    def update_follow_up_status(self, user_id, status, timestamp=None):
        """
        อัพเดทสถานะการติดตามผลสำหรับผู้ใช้
        
        Args:
            user_id (str): LINE User ID
            status (str): สถานะการติดตาม ('scheduled', 'sent', 'completed')
            timestamp (datetime, optional): เวลาที่อัพเดท
            
        Returns:
            bool: True หากสำเร็จ
        """
        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            
            if timestamp is None:
                timestamp = datetime.now()
                
            # ตรวจสอบว่ามีรายการติดตามอยู่แล้วหรือไม่
            cursor.execute(
                'SELECT id FROM follow_ups WHERE user_id = %s AND status != %s', 
                (user_id, 'completed')
            )
            
            result = cursor.fetchone()
            
            if result:
                # อัพเดทรายการที่มีอยู่
                cursor.execute(
                    'UPDATE follow_ups SET status = %s, updated_at = %s WHERE id = %s',
                    (status, timestamp, result[0])
                )
            else:
                # สร้างรายการใหม่
                cursor.execute(
                    'INSERT INTO follow_ups (user_id, status, created_at, updated_at) VALUES (%s, %s, %s, %s)',
                    (user_id, status, timestamp, timestamp)
                )
                
            conn.commit()
            return True
        except Exception as e:
            conn.rollback()
            logging.error(f"Error updating follow-up status: {str(e)}")
            raise
        finally:
            cursor.close()
            conn.close()