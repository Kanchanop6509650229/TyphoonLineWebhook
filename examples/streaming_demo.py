import os
import asyncio

from app.llm import grok_client


def run_sync_stream():
    print("Sync streaming demo (Grok-4):\n")
    messages = [{"role": "user", "content": "พิมพ์กลอนสั้น ๆ ให้หน่อย"}]
    for chunk in grok_client.stream_chat(messages, model=os.getenv("XAI_MODEL", "grok-4")):
        print(chunk, end="", flush=True)
    print()


async def run_async_stream():
    print("\nAsync streaming demo (Grok-4):\n")
    messages = [{"role": "user", "content": "เขียนสรุปหนึ่งย่อหน้าเกี่ยวกับดาวอังคาร"}]
    async for chunk in grok_client.astream_chat_iter(messages, model=os.getenv("XAI_MODEL", "grok-4")):
        print(chunk, end="", flush=True)
    print()


if __name__ == "__main__":
    run_sync_stream()
    asyncio.run(run_async_stream())

