# test_gemini_simple.py
"""
简单的 Gemini API 连接测试脚本

Usage:
    python scripts/test_gemini_simple.py
"""

import os
import asyncio
import google.generativeai as genai
from dotenv import load_dotenv

# 加载 .env
load_dotenv()

api_key = os.getenv("GEMINI_API_KEY")

if not api_key:
    print("❌ 错误: 没找到 GEMINI_API_KEY，请检查 .env 文件")
    exit(1)

genai.configure(api_key=api_key)


async def test():
    print(f"🔑 使用 Key: {api_key[:10]}******")
    print("🤖 正在尝试连接 Gemini...")
    
    try:
        model = genai.GenerativeModel("gemini-1.5-pro")  # 或者 gemini-1.5-flash
        response = await model.generate_content_async(
            "Hello! Reply with strict JSON: {'status': 'ok'}"
        )
        print("✅ 连接成功！模型回复：")
        print(response.text)
    except Exception as e:
        print(f"❌ 连接失败: {e}")


if __name__ == "__main__":
    asyncio.run(test())
