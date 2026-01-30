"""
调试脚本：测试 Gemini Analyzer 的输入输出
运行方式: python scripts/debug_gemini.py
"""
import json
import logging
import sys
import os

# 添加项目根目录到 Python 路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()  # 加载 .env 文件中的 GEMINI_API_KEY

from app.services.gemini_analyzer import ai_analyzer

# 配置日志到控制台以便直接观察
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)


def debug_single_event():
    # 1. 带入测试数据
    test_event = {
        "id": "145916",
        "title": "New 'Stranger Things' episode released by...?",
        "description": "This market will resolve based on the official release date of a new Stranger Things episode on Netflix.",
        "markets": [
            {
                "id": "1119647", 
                "question": "Will a new episode be released by December 31, 2025?", 
                "probability": 0.10,
                "outcomePrices": "[0.10, 0.90]"
            },
            {
                "id": "1278048", 
                "question": "Will a new episode be released by February 28, 2026?", 
                "probability": 0.011,
                "outcomePrices": "[0.011, 0.989]"
            },
            {
                "id": "1278049", 
                "question": "Will a new episode be released by March 31, 2026?", 
                "probability": 0.25,
                "outcomePrices": "[0.25, 0.75]"
            },
        ]
    }

    print("\n" + "=" * 50 + " START DEBUG " + "=" * 50)
    print(f"Event ID: {test_event['id']}")
    print(f"Event Title: {test_event['title']}")
    print(f"Markets Count: {len(test_event['markets'])}")
    
    # 2. 调用 analyze_with_gemini（带审计日志）
    result = ai_analyzer.analyze_with_gemini(test_event)
    
    print("\n" + "=" * 50 + " FINAL PARSED RESULT " + "=" * 50)
    if result:
        print(json.dumps(result, indent=2, ensure_ascii=False))
        
        # 3. 转换为 raw_analysis 格式
        print("\n" + "=" * 50 + " TRANSFORMED RAW_ANALYSIS " + "=" * 50)
        raw_analysis = ai_analyzer.transform_to_raw_analysis(result)
        print(json.dumps(raw_analysis, indent=2, ensure_ascii=False))
    else:
        print("❌ Result is None - 分析失败")

    print("\n" + "=" * 50 + " END DEBUG " + "=" * 50)


if __name__ == "__main__":
    debug_single_event()
