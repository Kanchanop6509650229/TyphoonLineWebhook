"""
โมดูลสำหรับการเรียกใช้ Together API แบบอะซิงโครนัส
ช่วยเพิ่มประสิทธิภาพการตอบสนองต่อผู้ใช้
"""
import asyncio
import httpx
import os
import logging
import json
from typing import Dict, List, Any, Optional, Union

class AsyncTogetherClient:
    """
    ไคลเอนต์แบบอะซิงโครนัสสำหรับการเรียกใช้ Together AI API
    """
    def __init__(self, api_key: str, model: str = "scb10x/scb10x-llama3-1-typhoon2-60256"):
        """
        สร้างไคลเอนต์อะซิงโครนัสสำหรับ Together AI
        
        Args:
            api_key (str): คีย์ API ของ Together
            model (str): ชื่อโมเดลที่ใช้
        """
        self.api_key = api_key
        self.model = model
        self.client = None
        
    async def setup(self):
        """
        เริ่มต้นไคลเอนต์ HTTP
        
        Returns:
            AsyncTogetherClient: ตัวเองเพื่อให้สามารถรวมคำสั่งได้
        """
        if self.client is None:
            self.client = httpx.AsyncClient(
                base_url="https://api.together.xyz",
                timeout=60.0,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json"
                }
            )
        return self
        
    async def close(self):
        """ปิดไคลเอนต์ HTTP"""
        if self.client:
            await self.client.aclose()
            self.client = None
            
    async def generate_completion(self, 
                            messages: List[Dict[str, str]], 
                            config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        สร้างการเติมเต็มการแชทแบบอะซิงโครนัส
        
        Args:
            messages (List[Dict[str, str]]): ลิสต์ของข้อความแชท
            config (Dict[str, Any], optional): การตั้งค่าการสร้าง
            
        Returns:
            Dict[str, Any]: การตอบกลับจาก API
        """
        if not self.client:
            await self.setup()
            
        default_config = {
            "temperature": 1.0,
            "max_tokens": 500,
            "top_p": 0.9
        }
        
        # รวมการตั้งค่า
        merged_config = {**default_config, **(config or {})}
            
        try:
            response = await self.client.post(
                "/v1/chat/completions",
                json={
                    "model": self.model,
                    "messages": messages,
                    **merged_config
                }
            )
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logging.error(f"Error in async API call: {str(e)}")
            raise
    
    async def summarize_conversation(self, 
                                    history: List[tuple], 
                                    system_message: Dict[str, str],
                                    max_tokens: int = 500) -> str:
        """
        สรุปประวัติการสนทนาแบบอะซิงโครนัส
        
        Args:
            history (List[tuple]): ประวัติการสนทนา (id, user_message, bot_response)
            system_message (Dict[str, str]): ข้อความคำแนะนำระบบ
            max_tokens (int): จำนวนโทเค็นสูงสุดในการสรุป
            
        Returns:
            str: ข้อความสรุป
            
        Raises:
            Exception: กรณีที่มีข้อผิดพลาดในระหว่างการสรุป
        """
        if not history:
            return ""
            
        try:
            summary_prompt = "นี่คือประวัติการสนทนา โปรดสรุปประเด็นสำคัญในประวัติการสนทนานี้:\n"
            for _, msg, resp in history:
                summary_prompt += f"\nผู้ใช้: {msg}\nบอท: {resp}\n"
            
            config = {
                "temperature": 0.3,
                "max_tokens": max_tokens,
                "top_p": 0.9
            }
            
            response = await self.generate_completion(
                messages=[
                    system_message,
                    {"role": "user", "content": summary_prompt}
                ],
                config=config
            )
            
            return response["choices"][0]["message"]["content"]
        except Exception as e:
            logging.error(f"Error in async summarize_conversation: {str(e)}")
            return ""
    
    async def process_message_batch(self, 
                                   batch: List[Dict[str, Any]], 
                                   system_message: Dict[str, str]) -> List[Dict[str, Any]]:
        """
        ประมวลผลชุดข้อความพร้อมกัน
        
        Args:
            batch (List[Dict[str, Any]]): ลิสต์ของข้อความและข้อมูลบริบท
            system_message (Dict[str, str]): ข้อความคำแนะนำระบบ
            
        Returns:
            List[Dict[str, Any]]: ผลลัพธ์การประมวลผลสำหรับแต่ละข้อความ
        """
        if not batch:
            return []
            
        tasks = []
        for item in batch:
            messages = [system_message] + item.get("messages", [])
            task = asyncio.create_task(
                self.generate_completion(
                    messages=messages,
                    config=item.get("config", {})
                )
            )
            tasks.append((item.get("id"), task))
            
        results = []
        for item_id, task in tasks:
            try:
                response = await task
                results.append({
                    "id": item_id,
                    "success": True,
                    "response": response
                })
            except Exception as e:
                results.append({
                    "id": item_id,
                    "success": False,
                    "error": str(e)
                })
                
        return results