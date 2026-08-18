"""
推理结果缓存与增量更新

当本体数据变更时（实体/关系的增删改），自动标记缓存失效。
使用内存缓存（进程级别），适合 MCP stdio 模式下单进程运行。

缓存策略：
- 以 (entity_ids 排序后的哈希, rules 配置) 作为缓存键
- 当实体/关系发生变更时，相关缓存自动失效
- 支持手动清除全部缓存
- 支持缓存命中率统计
"""

import hashlib
import time
from dataclasses import dataclass, field
from typing import Optional
from collections import defaultdict


@dataclass
class CacheEntry:
    """缓存条目"""
    key: str
    result: dict                     # 推理结果（序列化后的 dict）
    created_at: float
    hit_count: int = 0
    entity_ids: list = field(default_factory=list)  # 涉及的实体 IDs（用于失效判断）


class ReasoningCache:
    """推理结果缓存"""

    def __init__(self, max_entries: int = 500, ttl_seconds: int = 3600):
        """
        Args:
            max_entries: 最大缓存条目数
            ttl_seconds: 缓存生存时间（秒），默认 1 小时
        """
        self.max_entries = max_entries
        self.ttl_seconds = ttl_seconds
        self._cache: dict[str, CacheEntry] = {}
        self._entity_index: dict[str, set] = defaultdict(set)  # entity_id -> set of cache keys
        self._stats = {
            "hits": 0,
            "misses": 0,
            "evictions": 0,
            "invalidations": 0,
        }

    def _make_key(self, entity_ids: list, rules_config: Optional[list] = None,
                  time_config: Optional[tuple] = None) -> str:
        """生成缓存键"""
        sorted_ids = sorted(entity_ids)
        rules_str = ",".join(sorted(rules_config)) if rules_config else "all"
        time_str = ""
        if time_config:
            time_str = f"|future={int(time_config[0])},expired={int(time_config[1])}"
        raw = f"{sorted_ids}|{rules_str}{time_str}"
        return hashlib.md5(raw.encode()).hexdigest()

    def get(self, entity_ids: list, rules_config: Optional[list] = None,
            time_config: Optional[tuple] = None) -> Optional[dict]:
        """查询缓存"""
        key = self._make_key(entity_ids, rules_config, time_config)
        entry = self._cache.get(key)

        if entry is None:
            self._stats["misses"] += 1
            return None

        # TTL 检查
        if time.time() - entry.created_at > self.ttl_seconds:
            self._evict(key)
            self._stats["misses"] += 1
            return None

        entry.hit_count += 1
        self._stats["hits"] += 1
        return entry.result

    def put(self, entity_ids: list, result: dict, rules_config: Optional[list] = None,
            involved_entities: Optional[list] = None,
            time_config: Optional[tuple] = None):
        """写入缓存

        Args:
            entity_ids: 入口实体 IDs（缓存键的一部分）
            result: 推理结果
            rules_config: 规则配置（缓存键的一部分）
            involved_entities: 所有涉及的实体 IDs（用于精准失效缓存）
            time_config: (include_future, include_expired) 时间过滤配置（缓存键的一部分）
        """
        key = self._make_key(entity_ids, rules_config, time_config)

        # 容量检查：LRU 淘汰
        if len(self._cache) >= self.max_entries:
            self._evict_oldest()

        # 确定用于索引的实体列表：入口实体 + 所有涉及实体
        index_entities = set(entity_ids)
        if involved_entities:
            index_entities.update(involved_entities)

        entry = CacheEntry(
            key=key,
            result=result,
            created_at=time.time(),
            entity_ids=list(index_entities),
        )
        self._cache[key] = entry

        # 建立实体索引
        for eid in index_entities:
            self._entity_index[eid].add(key)

    def invalidate_entity(self, entity_id: str):
        """当某个实体变更时，失效相关缓存"""
        affected_keys = self._entity_index.pop(entity_id, set())
        for key in affected_keys:
            if key in self._cache:
                del self._cache[key]
                self._stats["invalidations"] += 1

    def invalidate_entities(self, entity_ids: list):
        """批量失效"""
        for eid in entity_ids:
            self.invalidate_entity(eid)

    def invalidate_all(self):
        """清除全部缓存"""
        count = len(self._cache)
        self._cache.clear()
        self._entity_index.clear()
        self._stats["invalidations"] += count

    def _evict(self, key: str):
        """淘汰单个条目"""
        entry = self._cache.pop(key, None)
        if entry:
            for eid in entry.entity_ids:
                self._entity_index[eid].discard(key)
                if not self._entity_index[eid]:
                    del self._entity_index[eid]

    def _evict_oldest(self):
        """淘汰最老的条目"""
        if not self._cache:
            return
        oldest_key = min(self._cache, key=lambda k: self._cache[k].created_at)
        self._evict(oldest_key)
        self._stats["evictions"] += 1

    def stats(self) -> dict:
        """缓存统计"""
        total = self._stats["hits"] + self._stats["misses"]
        hit_rate = (self._stats["hits"] / total * 100) if total > 0 else 0
        return {
            **self._stats,
            "cache_size": len(self._cache),
            "max_entries": self.max_entries,
            "hit_rate": f"{hit_rate:.1f}%",
            "entity_index_size": len(self._entity_index),
        }


# 全局缓存实例
_global_cache = ReasoningCache()


def get_cache() -> ReasoningCache:
    """获取全局缓存实例"""
    return _global_cache


def invalidate_on_entity_change(entity_id: str):
    """实体变更时调用，失效相关缓存"""
    _global_cache.invalidate_entity(entity_id)


def invalidate_on_relation_change(source_id: str, target_id: str):
    """关系变更时调用，失效两端实体的缓存"""
    _global_cache.invalidate_entity(source_id)
    _global_cache.invalidate_entity(target_id)
