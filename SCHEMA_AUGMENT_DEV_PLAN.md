# 本体 Schema 层增广开发计划

> 创建时间：2026-07-23
> 状态：计划阶段
> 目标：让信息输入机制能自动建立类型层次、关系语义，并支持 Schema 演化

---

## 一、现状分析

### 当前能力

| 能力 | 状态 | 说明 |
|------|------|------|
| 实例层 CRUD | ✅ 已有 | `ingest_document` / `modify_ontology` 支持 entities 和 relations 的增删改 |
| Schema 层读取 | ✅ 已有 | init_db 时硬编码预置 8 种实体类型 + 10 种关系类型 |
| Schema 层修改 | ❌ 缺失 | 没有任何工具能修改 entity_types 或 relation_types |
| 类型层次 (parent_id) | ❌ 未使用 | 字段存在，但全为 NULL，且无法设置 |
| 关系语义 (symmetric/transitive) | ⚠️ 半用 | 仅 init_db 时硬编码，推理引擎能读取但无法动态修改 |
| 逆关系 (inverse_of) | ❌ 缺失字段 | relation_types 表没有 inverse_of 字段 |
| 类型约束 (domain/range) | ❌ 缺失字段 | 无法声明"depends_on 只能连接 function->module" |

### 输入机制现状

当前有 3 条信息输入路径：

| 路径 | 工具 | 能做什么 | 不能做什么 |
|------|------|---------|-----------|
| A. 文档摄入 | `ingest_document` | LLM 从文档抽取实体+关系（实例层） | 不能修改 Schema |
| B. 自然语言修改 | `modify_ontology` | LLM 生成变更计划，增删改实体+关系 | 不能修改 Schema |
| C. 种子脚本 | `seed_*.py` | 手动 Python 脚本批量写入 | 非交互式，需要人工编码 |

三条路径**全部只操作实例层**，Schema 层是封闭的。

---

## 二、设计原则

### 核心思路：LLM 自动分析 + 人工后处理

> LLM 负责从输入文档中**自动识别**潜在的 Schema 元素（新类型、类型层次、关系语义），
> 生成 Schema 变更计划（dry_run），人工审核后执行。

```
文档输入
  │
  ▼
[LLM] Schema 分析器
  │  ├─ 识别新实体类型 + 推断 parent_id（类型层次）
  │  ├─ 识别新关系类型 + 推断 symmetric/transitive/inverse_of
  │  └─ 识别类型约束（domain/range）
  │
  ▼
dry_run 变更计划 ──→ 人工审核
  │
  ▼ (确认后)
Schema 写入（entity_types / relation_types 表）
  │
  ▼
实例层写入（entities / relations 表，复用现有流程）
```

### 设计约束

1. **不新建工具**：在现有 `ingest_document` 和 `modify_ontology` 上增广
2. **向后兼容**：原有实例层操作不受影响
3. **Schema 变更必须 dry_run**：所有 Schema 层修改默认 dry_run=true，必须人工确认
4. **LLM 做分析，规则做校验**：LLM 负责识别 Schema 元素，一致性检查器负责校验合理性

---

## 三、数据库改动

### 3.1 relation_types 表新增字段

```sql
-- 新增 3 个字段（通过 ALTER TABLE，不重建表）
ALTER TABLE relation_types ADD COLUMN inverse_of TEXT REFERENCES relation_types(id);
ALTER TABLE relation_types ADD COLUMN domain_type TEXT;   -- 允许的源实体类型 ID（逗号分隔，NULL=不限）
ALTER TABLE relation_types ADD COLUMN range_type TEXT;    -- 允许的目标实体类型 ID（逗号分隔，NULL=不限）
```

字段说明：
- `inverse_of`：逆关系类型 ID。如 `implements` 的 inverse_of 设为 `is_implemented_by`（新关系类型）
- `domain_type`：此关系的源实体类型约束。如 `implements` 的 domain_type 设为 `function,module`（只有功能/模块能实现）
- `range_type`：此关系的目标实体类型约束。如 `implements` 的 range_type 设为 `interface,requirement`

### 3.2 entity_types 表：利用现有 parent_id

`parent_id` 字段已存在，只需设置值即可建立层次。

### 3.3 预置类型层次（修正 init_db）

```
requirement (需求)           ← 根类型
  ├── function (功能)         ← parent: requirement
  ├── constraint (约束)       ← parent: requirement
  └── test_case (测试用例)    ← parent: requirement

module (模块)                 ← 根类型
  └── interface (接口)        ← parent: module

actor (角色)                  ← 根类型
  └── data_entity (数据实体)   ← parent: actor
```

### 3.4 预置关系语义修正

| 关系类型 | symmetric | transitive | inverse_of | domain_type | range_type |
|---------|-----------|------------|------------|-------------|------------|
| depends_on | 0 | 1 ✅ | is_depended_by | function,module,requirement | * |
| causes | 0 | **1** ⬆️ | is_caused_by | * | * |
| constrains | 0 | 0 | is_constrained_by | constraint | * |
| impacts | 0 | 0 | is_impacted_by | * | * |
| conflicts_with | 1 ✅ | 0 | (自身) | * | * |
| derived_from | 0 | 1 ✅ | derives | * | * |
| implements | 0 | 0 | is_implemented_by | function,module | interface,requirement |
| contains | 0 | 1 ✅ | is_contained_in | module,requirement | * |
| refines | 0 | 0 | is_refined_by | function | requirement,function |
| relates_to | 1 ✅ | 0 | (自身) | * | * |

修正项：
- `causes` 的 transitive 改为 1（因果有传递性：A导致B, B导致C -> A间接导致C）
- 新增 5 个逆关系类型：`is_depended_by`、`is_caused_by`、`is_constrained_by`、`is_impacted_by`、`is_implemented_by`、`is_contained_in`、`is_refined_by`、`derives`

> 注意：逆关系类型不一定要作为独立行存入 relation_types。可以只存 `inverse_of` 字段，推理引擎的 InverseRelationRule 已经能根据 INVERSE_MAP 推导。但显式存储可以让查询更直接，也让人工审核更清晰。

**决策：逆关系不新增为独立关系类型行，只在 `inverse_of` 字段中记录语义。** 推理引擎负责推导逆关系，不在存储层创建冗余行。这样避免数据冗余和一致性问题。

简化后的 relation_types 改动：

| 关系类型 | 新增字段值 |
|---------|-----------|
| depends_on | inverse_of: (不存，由推理引擎推导) |
| causes | transitive: 0→1 |
| implements | domain_type: "function,module", range_type: "interface,requirement" |
| contains | domain_type: "module,requirement" |
| refines | domain_type: "function", range_type: "requirement,function" |
| constrains | domain_type: "constraint" |

---

## 四、工具增广方案

### 4.1 `ingest_document` 增广：Schema 感知

**当前流程**：
```
文档 -> LLM 抽取实体+关系 -> 变更计划(dry_run) -> 执行写入
```

**增广后流程**：
```
文档 -> LLM 抽取实体+关系
          │
          ├─ 实例层：实体+关系（现有逻辑不变）
          │
          └─ Schema 层（新增）：
              ├─ 新实体类型识别（文档中出现了现有类型无法覆盖的概念）
              ├─ 类型层次推断（推断 parent_id）
              └─ 关系语义推断（推断新关系类型的 symmetric/transitive/domain/range）
          │
          ▼
      变更计划(dry_run)：
          {
            "entities": {...},      // 实例层（不变）
            "relations": {...},     // 实例层（不变）
            "schema": {             // 新增
              "entity_types": {"create": [...], "update": [...]},
              "relation_types": {"create": [...], "update": [...]}
            }
          }
          │
          ▼
      人工审核 -> 确认后执行（实例+Schema 一起写入）
```

**LLM Prompt 增广**：

在现有 `extract_entities_and_relations` 的 system prompt 中追加 Schema 分析指令：

```
## Schema 分析（新增）

除了抽取实体和关系实例外，请同时分析以下 Schema 层信息：

1. **新实体类型识别**：如果文档中出现了不属于现有 8 种类型的概念（如"流程"、"状态机"、"配置项"），提出新增实体类型建议，包括 parent_id。

2. **类型层次推断**：如果抽取的实体属于某个已有类型，但语义上更具体（如"API接口"比"接口"更具体），考虑是否该设置 parent_id。

3. **关系语义推断**：如果抽取的关系不属于现有 10 种类型，提出新增关系类型建议，包括 symmetric/transitive/inverse_of/domain/range。

返回格式新增 schema 字段：
```json
{
  "entities": [...],      // 实例（不变）
  "relations": [...],     // 实例（不变）
  "schema": {
    "entity_types": {
      "create": [
        {"id": "process", "name": "流程", "description": "业务流程", "parent_id": "requirement"}
      ],
      "update": [
        {"id": "function", "parent_id": "requirement"}
      ]
    },
    "relation_types": {
      "create": [
        {"id": "triggers", "name": "触发", "description": "A触发B执行", "symmetric": 0, "transitive": 0, "domain_type": "function,constraint", "range_type": "function"}
      ],
      "update": [
        {"id": "causes", "transitive": 1}
      ]
    }
  }
}
```
```

### 4.2 `modify_ontology` 增广：Schema 变更

**当前流程**：
```
自然语言描述 -> LLM 生成变更计划(实体+关系) -> dry_run -> 执行
```

**增广后流程**：
```
自然语言描述 -> LLM 生成变更计划
                  ├─ 实体+关系（现有逻辑不变）
                  └─ Schema 变更（新增）
                      ├─ 新增/修改实体类型（含 parent_id）
                      └─ 新增/修改关系类型（含 symmetric/transitive/inverse_of/domain/range）
                  │
                  ▼
              dry_run（含 Schema 变更预览）-> 人工审核 -> 执行
```

**LLM Prompt 增广**：

在现有 `plan_ontology_changes` 的 system prompt 中追加：

```
## Schema 变更能力（新增）

除了实体和关系的增删改外，你还可以生成 Schema 层的变更计划：

1. **新增实体类型**：当用户描述涉及现有类型无法覆盖的概念时
2. **修改实体类型**：设置 parent_id 建立类型层次
3. **新增关系类型**：当用户需要新的关系语义时
4. **修改关系类型**：设置 symmetric/transitive/inverse_of/domain/range

变更计划 JSON 新增 schema 字段（结构同 ingest_document）。
```

### 4.3 新增 `manage_schema` MCP 工具

虽然不新建工具是理想方案，但 Schema 变更逻辑较复杂，独立工具更清晰。

```python
# tools/manage_schema.py

def register(mcp):
    @mcp.tool()
    def manage_schema(
        action: str = "inspect",  # inspect / update / init_hierarchy
        entity_types: Optional[dict] = None,
        relation_types: Optional[dict] = None,
        dry_run: bool = True,
        db_path: Optional[str] = None,
    ) -> dict:
        """管理本体 Schema 层：查看/修改实体类型和关系类型。
        
        Actions:
        - inspect: 查看当前 Schema（类型层次、关系语义）
        - update: 修改 Schema（设置 parent_id, symmetric, transitive, inverse_of, domain, range）
        - init_hierarchy: 一键初始化预置类型层次和关系语义
        """
```

---

## 五、实现方案

### 5.1 数据库迁移

```python
# models/migration_schema_v2.py

def migrate_schema_v2(db_path=None):
    """Schema V2 迁移：新增 relation_types 字段 + 修正预置数据"""
    from models.schema import get_connection
    
    conn = get_connection(db_path)
    
    # 1. 检查是否已迁移
    cols = {row[1] for row in conn.execute("PRAGMA table_info(relation_types)").fetchall()}
    
    # 2. 新增字段
    if "inverse_of" not in cols:
        conn.execute("ALTER TABLE relation_types ADD COLUMN inverse_of TEXT")
    if "domain_type" not in cols:
        conn.execute("ALTER TABLE relation_types ADD COLUMN domain_type TEXT")
    if "range_type" not in cols:
        conn.execute("ALTER TABLE relation_types ADD COLUMN range_type TEXT")
    
    # 3. 修正预置类型层次
    type_hierarchy = {
        "function": "requirement",
        "constraint": "requirement",
        "test_case": "requirement",
        "interface": "module",
        "data_entity": "actor",
    }
    for child, parent in type_hierarchy.items():
        conn.execute(
            "UPDATE entity_types SET parent_id = ? WHERE id = ? AND parent_id IS NULL",
            (parent, child)
        )
    
    # 4. 修正关系语义
    relation_updates = [
        # causes 改为 transitive
        {"id": "causes", "transitive": 1},
        # 设置 domain/range 约束
        {"id": "implements", "domain_type": "function,module", "range_type": "interface,requirement"},
        {"id": "contains", "domain_type": "module,requirement"},
        {"id": "refines", "domain_type": "function", "range_type": "requirement,function"},
        {"id": "constrains", "domain_type": "constraint"},
    ]
    for update in relation_updates:
        set_clauses = []
        values = []
        for k, v in update.items():
            if k == "id":
                continue
            set_clauses.append(f"{k} = ?")
            values.append(v)
        values.append(update["id"])
        conn.execute(
            f"UPDATE relation_types SET {', '.join(set_clauses)} WHERE id = ?",
            values
        )
    
    conn.commit()
    conn.close()
```

### 5.2 LLM Prompt 增广

在 `parser/llm_parser.py` 中修改 `extract_entities_and_relations` 的 system prompt：

```python
# 新增 Schema 分析部分到 system prompt
SCHEMA_ANALYSIS_PROMPT = """

## Schema 分析（额外任务）

在抽取实体和关系实例之外，请同时分析以下 Schema 层信息。只在有把握时才提出 Schema 变更建议，不确定的不写。

### 现有实体类型
{entity_types_list}

### 现有关系类型
{relation_types_list}

### 需要分析的 Schema 维度

1. **新实体类型**：文档中是否出现了现有类型无法覆盖的概念？
   - 如有，提出新类型建议（含 id, name, description, parent_id）
   - parent_id 必须是现有类型之一

2. **类型层次**：现有类型是否应该建立父子关系？
   - 如 function 的 parent_id 应为 requirement

3. **新关系类型**：是否需要现有 10 种之外的关系？
   - 如有，提出新关系类型建议（含 symmetric, transitive, domain_type, range_type）

4. **关系语义修正**：现有关系类型的 symmetric/transitive 是否需要调整？

### 返回格式

在原有 JSON 中增加 schema 字段：
```json
{
  "entities": [...],
  "relations": [...],
  "schema": {
    "entity_types": {
      "create": [],
      "update": []
    },
    "relation_types": {
      "create": [],
      "update": []
    }
  }
}
```

如果不需要 Schema 变更，schema 字段返回空对象：`"schema": {"entity_types": {"create": [], "update": []}, "relation_types": {"create": [], "update": []}}`
"""
```

### 5.3 Schema 变更执行

```python
# models/schema_manager.py

class SchemaManager:
    """Schema 层管理器"""
    
    def execute_schema_plan(self, schema_plan, db_path=None):
        """执行 Schema 变更计划"""
        from models.schema import get_connection
        conn = get_connection(db_path)
        
        results = {"entity_types": {"created": [], "updated": []},
                   "relation_types": {"created": [], "updated": []}}
        
        # 实体类型创建
        for et in schema_plan.get("entity_types", {}).get("create", []):
            conn.execute(
                """INSERT OR IGNORE INTO entity_types 
                   (id, name, description, parent_id, updated_at) 
                   VALUES (?, ?, ?, ?, datetime('now'))""",
                (et["id"], et["name"], et.get("description", ""), et.get("parent_id"))
            )
            results["entity_types"]["created"].append(et["id"])
        
        # 实体类型更新
        for et in schema_plan.get("entity_types", {}).get("update", []):
            updates = {}
            if "parent_id" in et:
                updates["parent_id"] = et["parent_id"]
            if "name" in et:
                updates["name"] = et["name"]
            if "description" in et:
                updates["description"] = et["description"]
            if updates:
                set_clause = ", ".join(f"{k} = ?" for k in updates)
                conn.execute(
                    f"UPDATE entity_types SET {set_clause}, updated_at = datetime('now') WHERE id = ?",
                    list(updates.values()) + [et["id"]]
                )
                results["entity_types"]["updated"].append(et["id"])
        
        # 关系类型创建
        for rt in schema_plan.get("relation_types", {}).get("create", []):
            conn.execute(
                """INSERT OR IGNORE INTO relation_types 
                   (id, name, description, symmetric, transitive, inverse_of, domain_type, range_type) 
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (rt["id"], rt["name"], rt.get("description", ""),
                 rt.get("symmetric", 0), rt.get("transitive", 0),
                 rt.get("inverse_of"), rt.get("domain_type"), rt.get("range_type"))
            )
            results["relation_types"]["created"].append(rt["id"])
        
        # 关系类型更新
        for rt in schema_plan.get("relation_types", {}).get("update", []):
            allowed = {"symmetric", "transitive", "inverse_of", "domain_type", "range_type", "name", "description"}
            updates = {k: v for k, v in rt.items() if k != "id" and k in allowed}
            if updates:
                set_clause = ", ".join(f"{k} = ?" for k in updates)
                conn.execute(
                    f"UPDATE relation_types SET {set_clause} WHERE id = ?",
                    list(updates.values()) + [rt["id"]]
                )
                results["relation_types"]["updated"].append(rt["id"])
        
        conn.commit()
        conn.close()
        return results
    
    def inspect_schema(self, db_path=None):
        """查看当前 Schema"""
        from models.schema import get_connection
        conn = get_connection(db_path)
        
        # 实体类型层次
        entity_types = []
        rows = conn.execute(
            """SELECT et.id, et.name, et.description, et.parent_id,
                      (SELECT name FROM entity_types WHERE id = et.parent_id) AS parent_name,
                      (SELECT COUNT(*) FROM entities WHERE type_id = et.id AND status = 'active') AS instance_count
               FROM entity_types et ORDER BY et.id"""
        ).fetchall()
        for r in rows:
            entity_types.append(dict(r))
        
        # 关系类型语义
        relation_types = []
        rows = conn.execute(
            """SELECT rt.id, rt.name, rt.description, rt.symmetric, rt.transitive,
                      rt.inverse_of, rt.domain_type, rt.range_type,
                      (SELECT COUNT(*) FROM relations WHERE type_id = rt.id) AS instance_count
               FROM relation_types rt ORDER BY rt.id"""
        ).fetchall()
        for r in rows:
            relation_types.append(dict(r))
        
        conn.close()
        return {"entity_types": entity_types, "relation_types": relation_types}
```

### 5.4 推理引擎适配

规则引擎需要适配新的 Schema 字段：

1. **TransitiveClosureRule**：已经从 relation_types 读 transitive 字段，无需改动
2. **SymmetricRule**：已经从 relation_types 读 symmetric 字段，无需改动
3. **InverseRelationRule**：从硬编码的 INVERSE_MAP 改为读取 relation_types.inverse_of 字段
4. **InheritanceRule**：已经从 entity_types 读 parent_id，无需改动
5. **ConstraintPropagationRule**：无需改动
6. **ConflictDetectionRule**：新增利用 domain_type/range_type 做类型兼容性检查
7. **ImpactAnalysisRule**：无需改动
8. **ConsistencyChecker**：利用 domain_type/range_type 做关系两端类型校验

---

## 六、开发计划

### Phase 1: 数据库迁移 + 预置语义修正（0.5 天）

| 序号 | 任务 | 产出文件 | 依赖 |
|------|------|---------|------|
| 1.1 | 创建迁移脚本 | `models/migration_schema_v2.py` | 无 |
| 1.2 | 修改 `init_db` 中的预置数据 | 修改 `models/schema.py` | 无 |
| 1.3 | 执行迁移 + 验证 | 运行迁移脚本 | 1.1, 1.2 |

### Phase 2: Schema 管理器 + MCP 工具（1 天）

| 序号 | 任务 | 产出文件 | 依赖 |
|------|------|---------|------|
| 2.1 | 实现 SchemaManager | `models/schema_manager.py` | Phase 1 |
| 2.2 | 实现 `manage_schema` MCP 工具 | `tools/manage_schema.py` | 2.1 |
| 2.3 | 注册到 server.py | 修改 `server.py` | 2.2 |
| 2.4 | 单元测试 | `tests/test_schema_manager.py` | 2.1 |

### Phase 3: LLM Prompt 增广（1 天）

| 序号 | 任务 | 产出文件 | 依赖 |
|------|------|---------|------|
| 3.1 | 增广 `extract_entities_and_relations` 的 prompt | 修改 `parser/llm_parser.py` | Phase 2 |
| 3.2 | 增广 `plan_ontology_changes` 的 prompt | 修改 `parser/llm_parser.py` | Phase 2 |
| 3.3 | 增广 `ingest_document` 变更计划执行 | 修改 `tools/ingest_document.py` | 3.1 |
| 3.4 | 增广 `modify_ontology` 变更计划执行 | 修改 `tools/modify_ontology.py` | 3.2 |

### Phase 4: 推理引擎适配（0.5 天）

| 序号 | 任务 | 产出文件 | 依赖 |
|------|------|---------|------|
| 4.1 | InverseRelationRule 改为读取 inverse_of | 修改 `engine/rules/inverse.py` | Phase 1 |
| 4.2 | ConsistencyChecker 利用 domain/range | 修改 `engine/checker.py` | Phase 1 |
| 4.3 | 更新推理引擎测试 | 修改 `tests/test_reasoning_engine.py` | 4.1, 4.2 |

### Phase 5: 端到端测试（0.5 天）

| 序号 | 任务 | 依赖 |
|------|------|------|
| 5.1 | ingest_document + Schema 自动识别测试 | Phase 3 |
| 5.2 | modify_ontology + Schema 变更测试 | Phase 3 |
| 5.3 | manage_schema 工具测试 | Phase 2 |
| 5.4 | 推理引擎 + 新语义端到端测试 | Phase 4 |

---

## 七、验收标准

1. **类型层次**：8 个预置实体类型建立了合理的 parent_id 层次
2. **关系语义**：relation_types 表有 inverse_of、domain_type、range_type 字段
3. **causes 修正**：causes 的 transitive 从 0 改为 1
4. **ingest_document**：能自动识别新实体类型和关系类型，在 dry_run 计划中包含 schema 变更
5. **modify_ontology**：能通过自然语言修改 Schema（如"把 function 设为 requirement 的子类型"）
6. **manage_schema**：新 MCP 工具能查看和修改 Schema
7. **推理引擎**：InverseRelationRule 从 inverse_of 字段读取，ConsistencyChecker 利用 domain/range 校验
8. **向后兼容**：原有实例层操作不受影响
9. **单元测试**：Schema 管理器 + 推理引擎适配测试全部通过

---

## 八、文件清单

### 新增文件

```
models/migration_schema_v2.py    # 数据库迁移脚本
models/schema_manager.py         # Schema 管理器
tools/manage_schema.py           # Schema 管理 MCP 工具
tests/test_schema_manager.py     # Schema 管理测试
```

### 修改文件

```
models/schema.py                 # 修改 init_db 预置数据（类型层次 + 关系语义）
parser/llm_parser.py             # 增广 extract_entities_and_relations + plan_ontology_changes 的 prompt
tools/ingest_document.py          # 变更计划执行时处理 schema 变更
tools/modify_ontology.py          # 变更计划执行时处理 schema 变更
engine/rules/inverse.py           # 从 inverse_of 字段读取逆关系
engine/checker.py                 # 利用 domain/range 做类型校验
tests/test_reasoning_engine.py    # 更新测试用例
server.py                         # 注册 manage_schema 工具
```

### 不变文件

```
engine/core.py                    # 推理引擎核心不变
engine/result.py                  # 数据结构不变
engine/rules/transitive.py        # 已从 relation_types 读 transitive
engine/rules/symmetric.py          # 已从 relation_types 读 symmetric
engine/rules/impact.py            # 不依赖 Schema 层
engine/rules/constraint.py        # 不依赖 Schema 层
engine/rules/inheritance.py       # 已从 entity_types 读 parent_id
engine/rules/conflict.py          # 不依赖 Schema 层
```
