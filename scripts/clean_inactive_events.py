"""
清理脏数据脚本
1. 查询线上 Polymarket API 获取最新状态
2. 更新数据库中的状态字段
3. 删除不满足 active=True, closed=False, archived=False 的记录
"""

import asyncio
import sys
from pathlib import Path

# 确保项目根目录在 python path 中
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx
from sqlalchemy import select, delete, update
from app.db.session import async_session_factory
from app.models.event_card import EventCard
from app.models.ai_prediction import AIPrediction
from app.models.card_tag import card_tags

POLYMARKET_API_URL = "https://gamma-api.polymarket.com/events"


async def fetch_event_status(client: httpx.AsyncClient, event_id: str) -> dict:
    """从 Polymarket API 获取事件状态"""
    try:
        response = await client.get(f"{POLYMARKET_API_URL}?id={event_id}")
        response.raise_for_status()
        data = response.json()
        if data and len(data) > 0:
            event = data[0]
            return {
                "id": event_id,
                "active": event.get("active", True),
                "closed": event.get("closed", False),
                "archived": event.get("archived", False),
                "found": True
            }
    except Exception as e:
        print(f"   ⚠️ 查询 {event_id} 失败: {e}")
    
    return {"id": event_id, "found": False}


async def sync_and_clean():
    """同步线上状态并清理脏数据"""
    
    async with async_session_factory() as session:
        # 1. 获取所有 EventCard
        result = await session.execute(select(EventCard))
        all_cards = result.scalars().all()
        
        print(f"📊 数据库共有 {len(all_cards)} 条记录")
        print("\n🔍 正在查询线上状态...")
        
        # 2. 批量查询线上状态
        cards_to_update = []
        cards_to_delete = []
        not_found = []
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            for i, card in enumerate(all_cards):
                if (i + 1) % 10 == 0:
                    print(f"   进度: {i + 1}/{len(all_cards)}")
                
                status = await fetch_event_status(client, card.polymarket_id)
                
                if not status["found"]:
                    not_found.append(card)
                    continue
                
                # 检查是否需要更新
                need_update = (
                    card.is_active != status["active"] or
                    card.is_closed != status["closed"] or
                    card.is_archived != status["archived"]
                )
                
                if need_update:
                    cards_to_update.append({
                        "card": card,
                        "new_status": status
                    })
                
                # 检查是否需要删除（不满足条件）
                if not status["active"] or status["closed"] or status["archived"]:
                    cards_to_delete.append({
                        "card": card,
                        "reason": f"active={status['active']}, closed={status['closed']}, archived={status['archived']}"
                    })
                
                # 限流
                await asyncio.sleep(0.1)
        
        # 3. 显示结果
        print(f"\n📋 同步结果:")
        print(f"   需要更新状态: {len(cards_to_update)} 条")
        print(f"   需要删除: {len(cards_to_delete)} 条")
        print(f"   线上找不到: {len(not_found)} 条")
        
        if cards_to_delete:
            print(f"\n🗑️ 将要删除的记录:")
            for item in cards_to_delete[:10]:
                card = item["card"]
                print(f"   - {card.polymarket_id}: {card.title[:40]}...")
                print(f"     原因: {item['reason']}")
            if len(cards_to_delete) > 10:
                print(f"   ... 还有 {len(cards_to_delete) - 10} 条")
        
        if not cards_to_update and not cards_to_delete:
            print("\n✅ 数据库状态已是最新，无需清理")
            return
        
        # 4. 确认操作
        confirm = input("\n⚠️ 确认执行更新和删除操作？(y/N): ")
        if confirm.lower() != 'y':
            print("❌ 取消操作")
            return
        
        # 5. 更新状态
        if cards_to_update:
            print("\n📝 更新状态...")
            for item in cards_to_update:
                card = item["card"]
                status = item["new_status"]
                await session.execute(
                    update(EventCard)
                    .where(EventCard.id == card.id)
                    .values(
                        is_active=status["active"],
                        is_closed=status["closed"],
                        is_archived=status["archived"]
                    )
                )
            print(f"   ✅ 更新了 {len(cards_to_update)} 条记录")
        
        # 6. 删除脏数据
        if cards_to_delete:
            print("\n🗑️ 删除脏数据...")
            card_ids = [item["card"].id for item in cards_to_delete]
            
            # 删除关联数据
            await session.execute(
                delete(AIPrediction).where(AIPrediction.card_id.in_(card_ids))
            )
            await session.execute(
                delete(card_tags).where(card_tags.c.card_id.in_(card_ids))
            )
            await session.execute(
                delete(EventCard).where(EventCard.id.in_(card_ids))
            )
            print(f"   ✅ 删除了 {len(cards_to_delete)} 条记录")
        
        await session.commit()
        print("\n✅ 操作完成!")


async def main():
    """主函数"""
    print("=" * 60)
    print("🧹 EventCard 状态同步 & 脏数据清理脚本")
    print("=" * 60)
    print("\n步骤:")
    print("1. 从 Polymarket API 查询每个事件的最新状态")
    print("2. 更新数据库中的 active/closed/archived 字段")
    print("3. 删除不满足 active=True, closed=False, archived=False 的记录")
    print()
    
    await sync_and_clean()


if __name__ == "__main__":
    asyncio.run(main())
