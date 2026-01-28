-- =====================================================
-- 性能优化索引创建脚本
-- 在 Supabase SQL Editor 中执行此脚本
-- =====================================================

-- 1. event_snapshots(polymarket_id) 索引（关键：修复 10s 延迟）
-- 注意：如果已有复合索引，这个单列索引可能不是必需的，但可以加速单列查询
CREATE INDEX IF NOT EXISTS idx_event_snapshots_polymarket_id 
ON event_snapshots(polymarket_id);

-- 2. event_cards(is_active) 索引（用于过滤活跃卡片）
CREATE INDEX IF NOT EXISTS idx_event_cards_is_active 
ON event_cards(is_active) 
WHERE is_active = true;  -- 部分索引，只索引活跃的卡片

-- 3. event_cards(volume) 索引（用于排序）
CREATE INDEX IF NOT EXISTS idx_event_cards_volume 
ON event_cards(volume DESC NULLS LAST);

-- 4. 复合索引：is_active + volume（优化常见查询模式）
CREATE INDEX IF NOT EXISTS idx_event_cards_active_volume 
ON event_cards(is_active, volume DESC NULLS LAST) 
WHERE is_active = true;

-- 5. 优化 event_snapshots 的复合索引（如果不存在）
-- 用于 DISTINCT ON 查询：按 polymarket_id 分组，取最新的 created_at
CREATE INDEX IF NOT EXISTS idx_event_snapshots_polymarket_created 
ON event_snapshots(polymarket_id, created_at DESC);

-- =====================================================
-- 验证索引是否创建成功
-- =====================================================
-- SELECT 
--     schemaname,
--     tablename,
--     indexname,
--     indexdef
-- FROM pg_indexes
-- WHERE tablename IN ('event_cards', 'event_snapshots')
-- ORDER BY tablename, indexname;
