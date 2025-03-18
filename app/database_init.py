"""
โมดูลเริ่มต้นฐานข้อมูลสำหรับแชทบอท 'ใจดี'
ใช้สำหรับตรวจสอบและสร้างตารางฐานข้อมูลที่จำเป็น
"""
import logging
from .utils import safe_db_operation

class DatabaseInitializer:
    """
    คลาสสำหรับเริ่มต้นและตั้งค่าฐานข้อมูล
    """
    
    def __init__(self, mysql_pool):
        """
        สร้างอินสแตนซ์ของ DatabaseInitializer
        
        Args:
            mysql_pool: MySQL connection pool
        """
        self.pool = mysql_pool
        
    def get_connection(self):
        """
        ดึงการเชื่อมต่อฐานข้อมูลจาก pool
        
        Returns:
            Connection: การเชื่อมต่อฐานข้อมูล
        """
        return self.pool.get_connection()
    
    @safe_db_operation
    def check_and_create_tables(self):
        """
        ตรวจสอบและสร้างตารางฐานข้อมูลที่จำเป็นถ้ายังไม่มี
        
        Returns:
            bool: True หากสำเร็จ
        """
        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            
            # ตรวจสอบว่ามีตาราง conversations หรือไม่
            cursor.execute("""
                SELECT COUNT(*) 
                FROM information_schema.tables 
                WHERE table_schema = DATABASE()
                AND table_name = 'conversations'
            """)
            
            if cursor.fetchone()[0] == 0:
                logging.info("ไม่พบตาราง conversations กำลังสร้าง...")
                # สร้างตาราง conversations
                cursor.execute("""
                    CREATE TABLE conversations (
                        id INT AUTO_INCREMENT PRIMARY KEY,
                        user_id VARCHAR(50) NOT NULL,
                        timestamp DATETIME NOT NULL,
                        user_message TEXT NOT NULL,
                        bot_response TEXT NOT NULL,
                        token_count INT DEFAULT 0,
                        important_flag BOOLEAN DEFAULT FALSE,
                        INDEX idx_user_id (user_id),
                        INDEX idx_timestamp (timestamp),
                        INDEX idx_important (important_flag)
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
                """)
                logging.info("สร้างตาราง conversations สำเร็จ")
            
            # ตรวจสอบว่ามีตาราง follow_ups หรือไม่
            cursor.execute("""
                SELECT COUNT(*) 
                FROM information_schema.tables 
                WHERE table_schema = DATABASE()
                AND table_name = 'follow_ups'
            """)
            
            if cursor.fetchone()[0] == 0:
                logging.info("ไม่พบตาราง follow_ups กำลังสร้าง...")
                # สร้างตาราง follow_ups
                cursor.execute("""
                    CREATE TABLE follow_ups (
                        id INT AUTO_INCREMENT PRIMARY KEY,
                        user_id VARCHAR(50) NOT NULL,
                        status VARCHAR(20) NOT NULL,
                        created_at DATETIME NOT NULL,
                        updated_at DATETIME NOT NULL,
                        scheduled_date DATETIME,
                        INDEX idx_user_id (user_id),
                        INDEX idx_status (status),
                        INDEX idx_scheduled (scheduled_date)
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
                """)
                logging.info("สร้างตาราง follow_ups สำเร็จ")
            
            # ตรวจสอบว่ามีตาราง user_metrics หรือไม่
            cursor.execute("""
                SELECT COUNT(*) 
                FROM information_schema.tables 
                WHERE table_schema = DATABASE()
                AND table_name = 'user_metrics'
            """)
            
            if cursor.fetchone()[0] == 0:
                logging.info("ไม่พบตาราง user_metrics กำลังสร้าง...")
                # สร้างตาราง user_metrics
                cursor.execute("""
                    CREATE TABLE user_metrics (
                        id INT AUTO_INCREMENT PRIMARY KEY,
                        user_id VARCHAR(50) NOT NULL,
                        metric_name VARCHAR(50) NOT NULL,
                        metric_value FLOAT NOT NULL,
                        timestamp DATETIME NOT NULL,
                        UNIQUE KEY unique_user_metric (user_id, metric_name),
                        INDEX idx_user_id (user_id),
                        INDEX idx_metric_name (metric_name)
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
                """)
                logging.info("สร้างตาราง user_metrics สำเร็จ")
            
            conn.commit()
            logging.info("การเริ่มต้นฐานข้อมูลสำเร็จ")
            return True
            
        except Exception as e:
            conn.rollback()
            logging.error(f"เกิดข้อผิดพลาดในการเริ่มต้นฐานข้อมูล: {str(e)}")
            raise
        finally:
            cursor.close()
            conn.close()

def initialize_database(mysql_pool):
    """
    ฟังก์ชันสำหรับเริ่มต้นฐานข้อมูล
    
    Args:
        mysql_pool: MySQL connection pool
        
    Returns:
        bool: True หากสำเร็จ
    """
    initializer = DatabaseInitializer(mysql_pool)
    return initializer.check_and_create_tables()