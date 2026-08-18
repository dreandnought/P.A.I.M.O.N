# 临时关系（时间相关关系）修改方案

> 目标：支持"未来生效"的虚关系（如"3天后发布 nanomax 新模型"），
> 并在检索 / 推理时能够按需过滤这些虚关系。
>
> **实施状态：✅ 已实施（Phase 5）**
> - 核心（关系模型 + 推理引擎 + MCP 工具 + Web 看板 + 测试）已完成
> - 新增 `models/time_parse.py` 支撑 LLM 层相对时间解析

---

## 一、需求分析

### 1.1 场景描述

当用户告知本体 **"3天后会发布一个名为 nanomax 的新模型"** 时：

- **实体**：应创建 `nanomax` 实体（它是一个真实存在的实体，只是"未发布"）
- **关系**：`nanomax` 与当前本体中其他实体的关系是**虚关系**（如 `nanomax derives_from 现有模型`、`nanomax impacts 某模块`），**在 3 天后才真正生效**
- **检索/推理**：默认情况下，查询和推理应该能**过滤掉这些虚关系**，只基于"当前已生效"的关系；但用户也可以**显式查看**虚关系（用于规划、预览）

### 1.2 核心概念

| 概念 | 说明 |
|------|------|
| **实关系** | 当前已经生效的关系（`valid_from <= now` 且 `valid_until IS NULL 或 > now`） |
| **虚关系** | 未来才生效的关系（`valid_from > now`）或已过期（`valid_until < now`） |
| **时间窗口** | 每条关系都有 `valid_from` / `valid_until` 字段，定义其有效时间段 |

### 1.3 设计原则

1. **向后兼容**：现有数据（无时间字段）默认视为"永久有效"，不影响已有关系查询
2. **默认过滤**：查询/推理默认只返回已生效的实关系，避免虚关系污染推理结果
3. **显式可控**：通过参数 `include_future` / `include_expired`（或 `relation_status`）可控制是否包含虚关系
4. **集中控制**：过滤逻辑集中在 `models/relation.py` 的工具函数中，所有查询和推理统一走这个过滤

---

## 二、数据模型修改

### 2.1 `relations` 表新增字段

```sql
ALTER TABLE relations ADD COLUMN valid_from TEXT;   -- 生效时间（ISO 8601），NULL = 立即生效
ALTER TABLE relations ADD COLUMN valid_until TEXT;  -- 失效时间（ISO 8601），NULL = 永久有效
```

- `valid_from IS NULL` → 立即生效（默认，兼容现有数据）
- `valid_from > now` → **未来关系（虚关系）**
- `valid_until < now` → **已过期（历史关系）**
- `valid_from <= now AND valid_until IS NULL` → 永久实关系

### 2.2 迁移策略

在 `models/schema_manager.py` 新增 `migrate_schema_v3`：

```python
def migrate_schema_v3(db_path=None):
    """Schema V3 迁移：为 relations 表新增 valid_from / valid_until 字段。"""
    conn = get_connection(db_path)
    cols = {row[1] for row in conn.execute("PRAGMA table_info(relations)").fetchall()}
    migrated = "valid_from" in cols and "valid_until" in cols
    if not migrated:
        if "valid_from" not in cols:
            conn.execute("ALTER TABLE relations ADD COLUMN valid_from TEXT")
        if "valid_until" not in cols:
            conn.execute("ALTER TABLE relations ADD COLUMN valid_until TEXT")
        conn.commit()
    # 创建索引
    conn.execute("CREATE INDEX IF NOT EXISTS idx_relations_valid_from ON relations(valid_from)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_relations_valid_until ON relations(valid_until)")
    conn.commit()
    conn.close()
    return {"migrated": not migrated}
```

在 `get_connection()` 中自动调用迁移（幂等），确保旧库升级后立即可用。

---

## 三、核心过滤工具（models/relation.py）

新增统一的**时间过滤条件生成器**，供所有查询复用：

```python
# 关系时间状态
RELATION_STATUS = {
    "active":    "当前生效",   # valid_from <= now 且 valid_until IS NULL 或 > now
    "future":    "未来生效",   # valid_from > now（虚关系）
    "expired":   "已过期",     # valid_until < now
    "all":       "全部（不做时间过滤）",
}

def _time_filter_sql(alias, include_future=False, include_expired=False, now=None):
    """生成关系时间过滤的 SQL 片段。

    返回 (sql_fragment, params)。默认：只返回当前生效的实关系。
    """
    now = now or datetime.now(timezone.utc).isoformat()
    if include_future and include_expired:
        # 全部包含，不加过滤
        return "", []
    conditions = []
    params = []
    if not include_future:
        # 排除未来关系：valid_from IS NULL OR valid_from <= now
        conditions.append(f"({alias}.valid_from IS NULL OR {alias}.valid_from <= ?)")
        params.append(now)
    if not include_expired:
        # 排除已过期：valid_until IS NULL OR valid_until > now
        conditions.append(f"({alias}.valid_until IS NULL OR {alias}.valid_until > ?)")
        params.append(now)
    return " AND " + " AND ".join(conditions), params
```

### 查询函数签名扩展

现有查询函数（`get_entity_relations`、`get_outgoing_relations`、`get_incoming_relations`、`get_transitive_relations`）新增参数：

```python
def get_entity_relations(entity_id, relation_types=None, db_path=None,
                         include_future=False, include_expired=False):
    ...
```

**默认值 `include_future=False, include_expired=False`** → 默认只返回实关系，向后兼容。

---

## 四、关系创建入口（支持声明虚关系）

### 4.1 `create_relation` 扩展

```python
def create_relation(
    type_id, source_id, target_id,
    weight=1.0, confidence=0.8, source="llm", source_doc_id=None,
    metadata=None, db_path=None,
    valid_from=None, valid_until=None,   # 新增：时间窗口
):
```

- `valid_from` / `valid_until` 为 ISO 8601 字符串，`None` = 立即生效 / 永久有效
- 可选：`valid_from` 支持相对时间（如 `"3天后"`），由 `parse_prd` / `ingest_document` 层解析为绝对时间

### 4.2 `delete_relation` / `update_relation`

- `update_relation` 的 `allowed` 字段加入 `valid_from`、`valid_until`
- 支持"转正"操作：把虚关系更新为实关系（`valid_from = now`）

### 4.3 新增辅助函数

```python
def activate_relation(relation_id, db_path=None):
    """将虚关系转正为实关系（valid_from = now, valid_until = NULL）。"""
    return update_relation(relation_id, {"valid_from": now_iso(), "valid_until": None}, db_path)

def get_future_relations(db_path=None, limit=100):
    """查询所有未来生效的虚关系（供规划/预览）。"""
    ...
```

---

## 五、MCP 工具层修改

### 5.1 `query_ontology`（查询）

新增参数：
- `include_future: bool = False` — 是否包含未来生效的虚关系
- `include_expired: bool = False` — 是否包含已过期关系

调用 `get_entity_relations` / `get_transitive_relations` 时透传这两个参数。

### 5.2 `reason_ontology`（推理）

新增参数：
- `include_future: bool = False` — 推理时是否包含虚关系
- `include_expired: bool = False`

推理引擎内部所有规则和 `_build_subgraph` 统一走时间过滤。

### 5.3 `manage_schema`（Schema 管理）

- `inspect` 输出中为每个关系类型显示当前时间字段
- 支持通过 schema_plan 创建带时间语义的关系类型（可选）

### 5.4 `ingest_document` / `modify_ontology`

- 支持在实体/关系描述中识别时间信息（如 "3天后"、"下周"、"2026-08-08"）
- 创建关系时自动填充 `valid_from`
- 提供 `create_relation` 时显式传 `valid_from` 的能力

### 5.5 `parse_prd`（PRD 增强解析）

- LLM 抽取阶段识别时间敏感语句，标记虚关系
- 推理阶段默认过滤虚关系（`include_future=False`），但可在返回的附录中单独列出"未来关系"供参考

---

## 六、推理引擎修改

### 6.1 统一过滤入口

核心思路：**所有规则查询 relations 时，都带上时间过滤 SQL**。

在 `engine/core.py` 的 `ReasoningEngine` 增加配置：

```python
class ReasoningEngine:
    def __init__(self, db_path=None, include_future=False, include_expired=False):
        self.include_future = include_future
        self.include_expired = include_expired
```

`run()` 中把 `include_future` / `include_expired` 传给每个规则。

### 6.2 规则修改清单

| 规则文件 | 修改点 |
|---------|--------|
| `rules/transitive.py` | 递归 CTE 查询加时间过滤 |
| `rules/symmetric.py` | 关系查询加时间过滤 |
| `rules/inverse.py` | 关系查询加时间过滤 |
| `rules/constraint.py` | constrains / contains 查询加时间过滤 |
| `rules/impact.py` | BFS 遍历加时间过滤 |
| `rules/inheritance.py` | 兄弟关系查询加时间过滤 |
| `rules/conflict.py` | 冲突检测查询加时间过滤 |
| `checker.py` | 一致性检查查询加时间过滤 |
| `core.py` `_build_subgraph` | 子图构建加时间过滤 |

### 6.3 简化实现方式

为减少改动量，可在每个规则中统一调用 `_time_filter_sql` 生成片段，并追加到 WHERE 条件。提供一个公共 helper：

```python
# engine/utils.py（新增）
def apply_time_filter(where_clause, params, alias, include_future, include_expired, now=None):
    """把时间过滤片段追加到 WHERE 子句。"""
    from models.relation import _time_filter_sql
    frag, extra = _time_filter_sql(alias, include_future, include_expired, now)
    if frag:
        where_clause += frag
        params.extend(extra)
    return where_clause, params
```

---

## 七、Web 看板（web/app.py）

- 前端增加"显示未来关系"开关
- 关系列表默认只显示实关系，可切换显示虚关系（用不同颜色/虚线标注）
- 实体详情页显示该实体的未来关系

---

## 八、缓存影响

- `engine/cache.py` 的缓存失效逻辑目前基于实体/关系变更
- 虚关系转正（`activate_relation`）会触发 `invalidate_on_relation_change`，缓存自动失效
- **注意**：缓存 key 需要考虑 `include_future` / `include_expired` 参数，否则不同过滤条件下会串缓存

---

## 九、测试计划

新增 `tests/test_temporal_relations.py`：

| 测试用例 | 验证点 |
|---------|--------|
| 创建未来关系 | `valid_from` 设为未来时间，默认查询不返回 |
| 创建当前关系 | 默认查询返回 |
| 未来关系转正 | `activate_relation` 后默认查询返回 |
| 默认过滤 | `include_future=False` 时虚关系被过滤 |
| 显式包含 | `include_future=True` 时虚关系返回 |
| 推理过滤 | 推理引擎默认不把虚关系纳入推理，`include_future=True` 时纳入 |
| 过期关系 | `valid_until` 过去后默认过滤 |
| 迁移 | 旧库升级后字段存在且旧数据兼容 |

---

## 十、实施步骤（实际完成情况）

1. ✅ **Schema 迁移**：`models/schema.py` 的 `relations` 表加 `valid_from`/`valid_until` 字段 + `_migrate_relation_time_fields` 自动迁移（在 `get_connection` 中调用，幂等）
2. ✅ **关系模型**：`models/relation.py` 加 `_time_filter_sql` 工具 + `create_relation`/`update_relation` 支持时间字段 + `activate_relation`/`get_future_relations` 辅助函数
3. ✅ **查询函数**：`get_entity_relations`/`get_outgoing_relations`/`get_incoming_relations`/`get_transitive_relations` 加 `include_future`/`include_expired` 参数
4. ✅ **推理引擎**：`ReasoningEngine` 加 `include_future`/`include_expired` + 7 个规则 + `checker.py` 统一加时间过滤
5. ✅ **MCP 工具**：`query_ontology`/`reason_ontology` 加 `include_future`/`include_expired` 参数；`ingest_document`/`modify_ontology` 透传 `valid_from`/`valid_until`
6. ✅ **缓存隔离**：`engine/cache.py` 缓存 key 纳入 `time_config`，不同过滤参数不串缓存
7. ✅ **Web 看板**：`web/app.py` 的 `/api/relations` 与 `/api/entity/<id>` 支持 `include_future`/`include_expired` 参数
8. ✅ **时间解析**：新增 `models/time_parse.py`，支持"3天后"等相对时间 → 绝对时间
9. ✅ **测试**：新增 `tests/test_temporal_relations.py`（9 个用例全部通过）

> 注：LLM 层（parse_prd 的 prompt）自动识别时间敏感语句可作为后续增强，
> 目前通过 `models/time_parse.py` 提供解析能力，可由编排层显式调用。

---

## 十一、示例

### 场景：3 天后发布 nanomax

```python
# 1. 创建 nanomax 实体
from models.entity import create_entity
create_entity(
    entity_id="model:nanomax",
    type_id="data_entity",
    name="nanomax",
    description="即将发布的新模型",
)

# 2. 创建未来关系（虚关系）
from models.relation import create_relation
from datetime import datetime, timedelta, timezone

future = (datetime.now(timezone.utc) + timedelta(days=3)).isoformat()
create_relation(
    type_id="derives_from",
    source_id="model:nanomax",
    target_id="model:existing_model",
    valid_from=future,          # 3 天后生效
    metadata={"note": "3天后发布 nanomax"},
)

# 3. 默认查询：不返回虚关系
get_entity_relations("model:nanomax")  # → 空（虚关系被过滤）

# 4. 显式查询：包含虚关系
get_entity_relations("model:nanomax", include_future=True)
# → 返回 derives_from 关系（标记为 future）

# 5. 3 天后转正
from models.relation import activate_relation
activate_relation("relation_id")
get_entity_relations("model:nanomax")  # → 现在返回实关系
```

---

## 十二、边界情况与注意事项

1. **时区**：统一使用 UTC ISO 8601 存储，展示时转本地时区
2. **相对时间解析**：LLM 层需要把 "3天后"、"下周" 等相对时间解析为绝对时间，需要一个解析器（可复用 Anthropic 日期解析或自写简单规则）
3. **缓存串扰**：缓存 key 必须包含 `include_future` / `include_expired`
4. **孤立实体**：未来实体（如 nanomax）在虚关系过滤后可能表现为"孤立实体"，一致性检查器应能识别这种"规划中的实体"并单独标记，而非误报为问题
5. **实体本身的时间**：当前方案只处理"关系"的时间性。若需要"实体"本身也有发布时间（如 nanomax 3 天后才存在），可后续在 `entities` 表加 `available_from` 字段，本次先聚焦关系