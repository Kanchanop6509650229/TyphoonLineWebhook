"""
โมดูลจัดการฐานข้อมูลสำหรับแชทบอท 'ใจดี'
จัดการการเชื่อมต่อฐานข้อมูลและการดำเนินการที่เกี่ยวข้อง
"""
import logging
import time
import mysql.connector
from mysql.connector import pooling
from typing import Dict, Any, Optional, List, Tuple, Union
from contextlib import contextmanager
from functools import wraps

class DatabaseManager:
    """
    จัดการการเชื่อมต่อฐานข้อมูลและการดำเนินการที่เกี่ยวข้อง
    """
    
    def __init__(self, config: Dict[str, Any], pool_size: int = 10, pool_name: str = "chat_pool"):
        """
        สร้างอินสแตนซ์ของ DatabaseManager
        
        Args:
            config: การตั้งค่าการเชื่อมต่อฐานข้อมูล
            pool_size: ขนาดของ connection pool
            pool_name: ชื่อของ connection pool
        """
        self.config = {
            'host': config.get('MYSQL_HOST', 'localhost'),
            'port': int(config.get('MYSQL_PORT', 3306)),
            'user': config.get('MYSQL_USER', 'root'),
            'password': config.get('MYSQL_PASSWORD', ''),
            'database': config.get('MYSQL_DB', 'chatbot'),
            'charset': 'utf8mb4',
            'use_unicode': True,
            'connect_timeout': 10
        }
        self.pool_size = pool_size
        self.pool_name = pool_name
        self.pool = None
        self.init_pool()
        
    def init_pool(self) -> None:
        """
        เริ่มต้น connection pool
        """
        try:
            self.pool = pooling.MySQLConnectionPool(
                pool_name=self.pool_name,
                pool_size=self.pool_size,
                **self.config
            )
            logging.info(f"MySQL connection pool initialized with size {self.pool_size}")
        except Exception as e:
            logging.error(f"Error initializing MySQL connection pool: {str(e)}")
            raise
    
    @contextmanager
    def get_connection(self):
        """
        Context manager สำหรับการจัดการการเชื่อมต่อฐานข้อมูล
        
        Yields:
            Connection: การเชื่อมต่อฐานข้อมูล
        """
        conn = None
        try:
            conn = self.pool.get_connection()
            yield conn
        except mysql.connector.Error as e:
            logging.error(f"Database connection error: {str(e)}")
            # ลองเชื่อมต่อใหม่ถ้าการเชื่อมต่อหลุด
            if e.errno == 2006:  # MySQL server has gone away
                logging.info("Attempting to reconnect to database...")
                self.init_pool()
                conn = self.pool.get_connection()
                yield conn
            else:
                raise
        finally:
            if conn and conn.is_connected():
                conn.close()
    
    @contextmanager
    def get_cursor(self, dictionary=False):
        """
        Context manager สำหรับการจัดการ cursor
        
        Args:
            dictionary: ถ้าเป็น True จะคืนค่าเป็น dictionary แทน tuple
            
        Yields:
            Cursor: cursor สำหรับการดำเนินการกับฐานข้อมูล
        """
        with self.get_connection() as conn:
            cursor = conn.cursor(dictionary=dictionary)
            try:
                yield cursor
                conn.commit()
            except Exception as e:
                conn.rollback()
                logging.error(f"Database operation error: {str(e)}")
                raise
            finally:
                cursor.close()
    
    def execute_query(self, query: str, params: Optional[Tuple] = None, dictionary: bool = False) -> List[Dict[str, Any]]:
        """
        ดำเนินการ query และคืนค่าผลลัพธ์
        
        Args:
            query: SQL query
            params: พารามิเตอร์สำหรับ query
            dictionary: ถ้าเป็น True จะคืนค่าเป็น dictionary แทน tuple
            
        Returns:
            List: ผลลัพธ์ของ query
        """
        with self.get_cursor(dictionary=dictionary) as cursor:
            cursor.execute(query, params or ())
            return cursor.fetchall()
    
    def execute_many(self, query: str, params_list: List[Tuple]) -> int:
        """
        ดำเนินการ query หลายครั้งด้วยชุดพารามิเตอร์ที่แตกต่างกัน
        
        Args:
            query: SQL query
            params_list: รายการของพารามิเตอร์สำหรับแต่ละการดำเนินการ
            
        Returns:
            int: จำนวนแถวที่ได้รับผลกระทบ
        """
        with self.get_cursor() as cursor:
            cursor.executemany(query, params_list)
            return cursor.rowcount
    
    def execute_and_commit(self, query: str, params: Optional[Tuple] = None) -> int:
        """
        ดำเนินการ query และ commit การเปลี่ยนแปลง
        
        Args:
            query: SQL query
            params: พารามิเตอร์สำหรับ query
            
        Returns:
            int: จำนวนแถวที่ได้รับผลกระทบ
        """
        with self.get_cursor() as cursor:
            cursor.execute(query, params or ())
            return cursor.rowcount
    
    def execute_and_get_last_id(self, query: str, params: Optional[Tuple] = None) -> int:
        """
        ดำเนินการ query และคืนค่า last insert ID
        
        Args:
            query: SQL query
            params: พารามิเตอร์สำหรับ query
            
        Returns:
            int: last insert ID
        """
        with self.get_cursor() as cursor:
            cursor.execute(query, params or ())
            return cursor.lastrowid
    
    def check_connection(self) -> bool:
        """
        ตรวจสอบการเชื่อมต่อฐานข้อมูล
        
        Returns:
            bool: True ถ้าการเชื่อมต่อปกติ
        """
        try:
            with self.get_connection() as conn:
                return conn.is_connected()
        except Exception:
            return False
    
    def get_table_schema(self, table_name: str) -> List[Dict[str, Any]]:
        """
        ดึงโครงสร้างของตาราง
        
        Args:
            table_name: ชื่อตาราง
            
        Returns:
            List: รายการคอลัมน์และชนิดข้อมูล
        """
        query = """
            SELECT COLUMN_NAME, DATA_TYPE, CHARACTER_MAXIMUM_LENGTH, IS_NULLABLE, COLUMN_KEY
            FROM INFORMATION_SCHEMA.COLUMNS
            WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s
            ORDER BY ORDINAL_POSITION
        """
        return self.execute_query(query, (table_name,), dictionary=True)
    
    def table_exists(self, table_name: str) -> bool:
        """
        ตรวจสอบว่าตารางมีอยู่หรือไม่
        
        Args:
            table_name: ชื่อตาราง
            
        Returns:
            bool: True ถ้าตารางมีอยู่
        """
        query = """
            SELECT COUNT(*) 
            FROM information_schema.tables 
            WHERE table_schema = DATABASE()
            AND table_name = %s
        """
        result = self.execute_query(query, (table_name,))
        return result[0][0] > 0
