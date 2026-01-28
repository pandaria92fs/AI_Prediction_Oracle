"""
AI Predictions Seed Script
从 CSV 文件导入 AI 分析数据到 ai_predictions 表

Usage:
    python -m scripts.seed_ai_predictions [csv_path]

CSV Format:
    event_id, event_title, summary_and_calibration_json
"""

import asyncio
import csv
import json
import re
import sys
from decimal import Decimal
from pathlib import Path

# 确保项目根目录在 Python path 中
project_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(project_root))

from sqlalchemy import select, delete
from sqlalchemy.dialects.postgresql import insert

from app.db.session import async_session_factory
from app.models import EventCard, AIPrediction


# 默认 CSV 文件路径
DEFAULT_CSV_PATH = project_root / "polymarket_analyses_summary1.csv"


def fix_json_string(json_str: str) -> str:
    """
    修复 JSON 中的常见问题：
    1. 未被引号包裹的百分比值: 0.01% -> "0.01%"
    2. 字符串值内部未转义的双引号: the "Invisible Primary" -> the \"Invisible Primary\"
    """
    # 1. 修复百分比值
    json_str = re.sub(r':\s*(\d+\.?\d*)%', r': "\1%"', json_str)
    
    # 2. 修复字符串内部的未转义双引号
    # 开引号: 字母 + 空格 + " + 字母 (如: the "Invisible)
    json_str = re.sub(r'([a-zA-Z]) "([A-Za-z])', r'\1 \\"\2', json_str)
    
    # 闭引号: 字母 + " + 空格 + 小写字母 (如: Primary" phase)
    json_str = re.sub(r'([a-zA-Z])" ([a-z])', r'\1\\" \2', json_str)
    
    # 闭引号: 字母 + " + 空格 + 左括号 (如: Capital" (BlackRock))
    json_str = re.sub(r'([a-zA-Z])" \(', r'\1\\" (', json_str)
    
    # 闭引号: 字母 + " + 逗号 (如: something", next)
    json_str = re.sub(r'([a-zA-Z])",', r'\1\\",', json_str)
    
    return json_str


def parse_odds(value) -> float:
    """
    解析 ai_calibrated_odds_pct，支持多种格式：
    - 小数: 0.565 -> 56.5
    - 百分比字符串: "22.00%" -> 22.0
    - 百分比字符串(小数): "0.01%" -> 0.01
    """
    if value is None:
        return 0.0
    
    if isinstance(value, (int, float)):
        # 小数格式 (0.565)，转为百分比
        if value <= 1.0:
            return float(value) * 100
        # 已经是百分比数字
        return float(value)
    
    if isinstance(value, str):
        # 去掉 % 符号
        clean = value.strip().rstrip('%')
        try:
            num = float(clean)
            # 如果原字符串有 %，说明已经是百分比
            if '%' in value:
                return num
            # 否则是小数，转百分比
            if num <= 1.0:
                return num * 100
            return num
        except ValueError:
            return 0.0
    
    return 0.0


async def seed(csv_path: Path):
    """从 CSV 导入 AI 分析数据"""
    
    if not csv_path.exists():
        print(f"❌ CSV 文件不存在: {csv_path}")
        print(f"   请将 CSV 文件放到: {DEFAULT_CSV_PATH}")
        return
    
    print(f"📄 读取 CSV: {csv_path}")
    
    # 读取 CSV 数据
    rows = []
    with open(csv_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(row)
    
    print(f"   共 {len(rows)} 条记录")
    
    async with async_session_factory() as session:
        # 1. 获取所有 event_id 列表
        event_ids = [row["event_id"] for row in rows]
        
        # 2. 批量查询已存在的 EventCard
        stmt = select(EventCard.id, EventCard.polymarket_id).where(
            EventCard.polymarket_id.in_(event_ids)
        )
        result = await session.execute(stmt)
        
        # 构建 polymarket_id -> card_id 映射
        card_map: dict[str, int] = {}
        for card_id, poly_id in result.all():
            card_map[poly_id] = card_id
        
        print(f"🔍 匹配到 {len(card_map)}/{len(event_ids)} 个 EventCard")
        
        # 打印未匹配的 event_id
        unmatched = [eid for eid in event_ids if eid not in card_map]
        if unmatched:
            print(f"   ⚠️ 未匹配的 event_id ({len(unmatched)} 条): {unmatched[:10]}{'...' if len(unmatched) > 10 else ''}")
        
        # 3. 处理每条记录
        predictions_to_insert = []
        skipped = 0
        json_errors = 0
        
        for row in rows:
            event_id = row["event_id"]
            
            # 查找对应的 card_id
            card_id = card_map.get(event_id)
            if not card_id:
                skipped += 1
                continue
            
            # 解析 JSON（预处理修复格式问题）
            try:
                raw_json = row["summary_and_calibration_json"]
                fixed_json = fix_json_string(raw_json)
                data = json.loads(fixed_json)
            except json.JSONDecodeError as e:
                # 打印详细调试信息
                error_pos = e.pos if hasattr(e, 'pos') else 0
                context_start = max(0, error_pos - 30)
                context_end = min(len(fixed_json), error_pos + 30)
                context = fixed_json[context_start:context_end]
                print(f"   ⚠️ JSON 解析失败 (event_id={event_id}): {e}")
                print(f"      错误位置附近: ...{context}...")
                json_errors += 1
                skipped += 1
                continue
            
            # 提取字段
            executive_summary = data.get("executive_summary", "")
            markets = data.get("markets", {})
            
            # 找出 original_odds 最高的 market，提取其 ai_calibrated_odds_pct
            outcome_prediction = "N/A"
            if markets:
                # 找到 original_odds 最高的 market
                best_market = max(
                    markets.items(),
                    key=lambda x: float(x[1].get("original_odds", 0))
                )
                market_id, market_data = best_market
                ai_odds_raw = market_data.get("ai_calibrated_odds_pct", 0)
                question = market_data.get("question", "Unknown")
                
                # 解析 ai_calibrated_odds_pct（可能是小数 0.565 或百分比字符串 "22.00%"）
                ai_odds_pct = parse_odds(ai_odds_raw)
                
                # 格式化输出，例如: "56.5% - Will Trump win?"
                outcome_prediction = f"{ai_odds_pct:.1f}% - {question[:100]}"
            
            # 精简 raw_analysis，只保留关键字段，统一格式
            raw_markets = {}
            for mid, mdata in markets.items():
                raw_markets[mid] = {
                    "question": mdata.get("question"),
                    "original_odds": mdata.get("original_odds"),
                    # 统一转换为百分比数值 (如 56.5)
                    "ai_calibrated_odds_pct": parse_odds(mdata.get("ai_calibrated_odds_pct")),
                }
            
            predictions_to_insert.append({
                "card_id": card_id,
                "summary": executive_summary or "No summary available",
                "confidence_score": Decimal("0.85"),  # 默认置信度
                "outcome_prediction": outcome_prediction,
                "raw_analysis": json.dumps(raw_markets, ensure_ascii=False),
            })
        
        # 打印详细统计
        print(f"\n📊 处理统计:")
        print(f"   ├─ CSV 总记录数: {len(rows)}")
        print(f"   ├─ Card 匹配成功: {len(card_map)}")
        print(f"   ├─ Card 未找到: {len(unmatched)}")
        print(f"   ├─ JSON 解析失败: {json_errors}")
        print(f"   └─ 待导入记录数: {len(predictions_to_insert)}")
        
        if not predictions_to_insert:
            print("\n❌ 没有有效数据可导入")
            return
        
        # 4. 批量 UPSERT (基于 card_id 去重 - 每个 card 只保留最新一条)
        # 由于 ai_predictions 没有唯一约束，我们先删除已存在的记录再插入
        existing_card_ids = [p["card_id"] for p in predictions_to_insert]
        
        # 删除这些 card 的旧预测
        await session.execute(
            delete(AIPrediction).where(AIPrediction.card_id.in_(existing_card_ids))
        )
        
        # 批量插入新预测
        await session.execute(
            insert(AIPrediction).values(predictions_to_insert)
        )
        
        await session.commit()
        
        print(f"✅ 成功导入 {len(predictions_to_insert)} 条 AI 预测")


async def main():
    # 支持命令行参数指定 CSV 路径
    if len(sys.argv) > 1:
        csv_path = Path(sys.argv[1])
    else:
        csv_path = DEFAULT_CSV_PATH
    
    await seed(csv_path)


if __name__ == "__main__":
    asyncio.run(main())
