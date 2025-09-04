"""
โมดูลสำหรับการเรียกใช้ xAI Grok API แบบอะซิงโครนัส (shim อะแดปเตอร์เดิม)
"""
import asyncio
import logging
from typing import Dict, List, Any, Optional

from .llm import grok_client


class AsyncDeepseekClient:
    """
    ความเข้ากันได้แบบบางสำหรับโค้ดเดิมที่เรียก DeepSeek
    ภายในเปลี่ยนไปใช้ xAI Grok ผ่าน grok_client
    """

    def __init__(self, api_key: str, model: str = "grok-4"):
        self.api_key = api_key
        self.model = model or "grok-4"

    async def setup(self):
        # ไม่มีการตั้งค่าพิเศษ จำลองสัญญาเดิม
        return self

    async def close(self):
        # ไม่มีทรัพยากรต่อเนื่องที่ต้องปิดใน shim นี้
        return None

    async def generate_completion(
        self, messages: List[Dict[str, str]], config: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        merged = {"temperature": 1.0, "max_tokens": 500, "top_p": 0.9}
        if config:
            merged.update(config)
        try:
            text = await grok_client.astream_chat(
                messages=messages, model=self.model, **merged
            )
            return {"choices": [{"message": {"content": text}}]}
        except Exception as e:
            logging.error(f"Error in async API call (shim): {str(e)}")
            raise

    async def summarize_conversation(
        self, history: List[tuple], system_message: Dict[str, str], max_tokens: int = 500
    ) -> str:
        if not history:
            return ""
        try:
            summary_prompt = "นี่คือประวัติการสนทนา โปรดสรุปประเด็นสำคัญในประวัติการสนทนานี้:\n"
            for _, msg, resp in history:
                summary_prompt += f"\nผู้ใช้: {msg}\nบอท: {resp}\n"

            cfg = {"temperature": 0.3, "max_tokens": max_tokens, "top_p": 0.9}
            resp = await self.generate_completion(
                messages=[system_message, {"role": "user", "content": summary_prompt}],
                config=cfg,
            )
            return resp["choices"][0]["message"]["content"]
        except Exception as e:
            logging.error(f"Error in async summarize_conversation: {str(e)}")
            return ""

    async def process_message_batch(
        self, batch: List[Dict[str, Any]], system_message: Dict[str, str]
    ) -> List[Dict[str, Any]]:
        if not batch:
            return []
        tasks = []
        for item in batch:
            messages = [system_message] + item.get("messages", [])
            task = asyncio.create_task(
                self.generate_completion(messages=messages, config=item.get("config", {}))
            )
            tasks.append((item.get("id"), task))

        results = []
        for item_id, task in tasks:
            try:
                response = await task
                results.append({"id": item_id, "success": True, "response": response})
            except Exception as e:
                results.append({"id": item_id, "success": False, "error": str(e)})
        return results
