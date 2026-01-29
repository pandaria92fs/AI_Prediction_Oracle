"""
清理脏数据脚本
删除不满足 active=True, closed=False, archived=False 的 EventCard 记录
"""

import asyncio
import sys
from pathlib import Path

# 确保项目根目录在 python path 中
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select, delete, or_
from app.db.session import async_session_factory
from app.models.event_card import EventCard
from app.models.ai_prediction import AIPrediction
from app.models.card_tag import card_tags


async def clean_inactive_events():
    """清理不满足三个状态条件的 EventCard"""
    
    async with async_session_factory() as session:
        try:
            # 1. 先查询要删除的记录数量
            count_query = select(EventCard).where(
                or_(
                    EventCard.is_active != True,
                    EventCard.is_active.is_(None),
                    EventCard.is_closed != False,
                    EventCard.is_closed.is_(None),
                    EventCard.is_archived != False,
                    EventCard.is_archived.is_(None),
                )
            )
            result = await session.execute(count_query)
            cards_to_delete = result.scalars().all()
            
            if not cards_to_delete:
                print("✅ 没有需要清理的脏数据")
                return
            
            print(f"🔍 找到 {len(cards_to_delete)} 条不满足条件的记录:")
            for card in cards_to_delete[:10]:  # 只显示前 10 条
                print(f"   - ID: {card.polymarket_id} | {card.title[:40]}...")
                print(f"     active={card.is_active}, closed={card.is_closed}, archived={card.is_archived}")
            
            if len(cards_to_delete) > 10:
                print(f"   ... 还有 {len(cards_to_delete) - 10} 条")
            
            # 2. 确认删除
            confirm = input("\n⚠️ 确认删除这些记录？(y/N): ")
            if confirm.lower() != 'y':
                print("❌ 取消操作")
                return
            
            # 3. 获取要删除的 card IDs
            card_ids = [card.id for card in cards_to_delete]
            
            # 4. 先删除关联的 AI predictions
            await session.execute(
                delete(AIPrediction).where(AIPrediction.card_id.in_(card_ids))
            )
            print(f"   🗑️ 删除关联的 AI predictions")
            
            # 5. 删除关联的 card_tags
            await session.execute(
                delete(card_tags).where(card_tags.c.card_id.in_(card_ids))
            )
            print(f"   🗑️ 删除关联的 card_tags")
            
            # 6. 删除 EventCard
            await session.execute(
                delete(EventCard).where(EventCard.id.in_(card_ids))
            )
            print(f"   🗑️ 删除 EventCard 记录")
            
            await session.commit()
            print(f"\n✅ 成功清理 {len(cards_to_delete)} 条脏数据")
            
        except Exception as e:
            await session.rollback()
            print(f"❌ 清理失败: {e}")
            raise


async def show_stats():
    """显示当前数据状态统计"""
    
    async with async_session_factory() as session:
        # 总数
        total_result = await session.execute(select(EventCard))
        total = len(total_result.scalars().all())
        
        # 满足条件的数量
        valid_query = select(EventCard).where(
            EventCard.is_active == True,
            EventCard.is_active.isnot(None),
            EventCard.is_closed == False,
            EventCard.is_closed.isnot(None),
            EventCard.is_archived == False,
            EventCard.is_archived.isnot(None),
        )
        valid_result = await session.execute(valid_query)
        valid = len(valid_result.scalars().all())
        
        print("\n📊 数据库统计:")
        print(f"   总记录数: {total}")
        print(f"   有效记录: {valid}")
        print(f"   脏数据: {total - valid}")


async def main():
    """主函数"""
    print("=" * 50)
    print("🧹 EventCard 脏数据清理脚本")
    print("=" * 50)
    print("\n条件: active=True AND closed=False AND archived=False")
    print("不满足以上条件的记录将被删除\n")
    
    await show_stats()
    print()
    await clean_inactive_events()


if __name__ == "__main__":
    asyncio.run(main())
