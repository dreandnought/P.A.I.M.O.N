# 本体推理引擎开发计划

> 创建时间：2026-07-23
> 状态：计划阶段
> 基于：CodingOntology 项目现有代码 + 与几爹爹的讨论

---

## 一、核心设计理念

### 当前问题

现有 `parse_prd` 流水线共 6 次 LLM 调用，其中阶段 3（推理隐含依赖/约束/影响）用 3 个并行 LLM subagent 完成。这导致：

- **推理结果不可靠**：LLM 推理是概率性的，可能产生幻觉关系
- **无法复现**：同样输入，不同次运行结果不同
- **成本高**：每次解析 6 次 LLM 调用
- **不是真正的本体**：本体的核心价值在于基于规则的可复现推理，而不是 LLM 的语义猜测

### 新设计原则

> **LLM 只负责两件事：(1) 找到入口——从 PRD 中抽取实体并匹配到本体；(2) 出口——将推理引擎的结果转化为 PRD 增广内容。中间的推理全部由规则引擎完成。**

```
PRD 输入
  │
  ▼
[LLM] 实体抽取 + 语义匹配 → 入口实体 IDs     ← LLM 仅此一处
  │
  ▼
[规则引擎] 推理流水线                         ← 核心改动
  │  ├─ 传递闭包（transitive closure）
  │  ├─ 对称推理（symmetric inference）
  │  ├─ 继承推理（type inheritance）
  │  ├─ 逆关系推理（inverse relation）
  │  ├─ 约束传播（constraint propagation）
  │  ├─ 冲突检测（conflict detection）
  │  └─ 影响分析（impact analysis, BFS）
  │
  ▼
[规则引擎] 一致性检查 + 推理结果汇总
  │
  ▼
[LLM] 将推理结果融合为增强版 PRD              ← LLM 仅此一处
  │
  ▼
输出 enriched_prd
```

### LLM 调用对比

| 阶段 | 当前 | 改造后 |
|------|------|--------|
| 实体抽取 | LLM ✅ | LLM ✅（保留） |
| 语义匹配 | LLM ✅ | LLM ✅（保留，MVP 阶段） |
| 图搜索 | SQL BFS ✅ | 规则引擎统一管理 |
| 依赖推理 | LLM ❌ | 规则引擎 ✅ |
| 约束推理 | LLM ❌ | 规则引擎 ✅ |
| 影响推理 | LLM ❌ | 规则引擎 ✅ |
| PRD 融合 | LLM ✅ | LLM ✅（保留） |
| **LLM 总调用** | **6 次** | **2 次**（抽取 + 融合） |

---

## 二、推理引擎架构设计

### 2.1 引擎分层

```
engine/
├── __init__.py
├── core.py              # 推理引擎核心：Rule + RuleEngine
├── rules/
│   ├── __init__.py
│   ├── transitive.py    # 传递闭包规则
│   ├── symmetric.py     # 对称关系规则
│   ├── inheritance.py   # 类型继承规则
│   ├── inverse.py       # 逆关系规则
│   ├── constraint.py    # 约束传播规则
│   ├── conflict.py      # 冲突检测规则
│   └── impact.py        # 影响分析规则（BFS）
├── checker.py           # 一致性检查器
├── result.py            # 推理结果数据结构
└── cache.py             # 推理结果缓存（未来）
```

### 2.2 核心数据结构

```python
# engine/result.py

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class InferenceResult:
    """单条推理结果"""
    rule_name: str           # 规则名称（如 "transitive_closure"）
    inference_type: str      # 推理类型：dependency / constraint / impact / conflict
    source_entity_id: str    # 推理起点
    target_entity_id: str    # 推理终点
    relation_type: str       # 关系类型
    evidence: str            # 推理证据（如 "A->B->C, transitive closure"）
    confidence: float        # 置信度（规则推导的置信度，非 LLM 猜测）
    depth: int = 1           # 推理深度（几跳）


@dataclass
class ReasoningOutput:
    """推理引擎完整输出"""
    entity_ids: list[str]                    # 入口实体 IDs
    inferences: list[InferenceResult]        # 所有推理结果
    conflicts: list[InferenceResult]        # 冲突检测结果
    subgraph: dict                          # 推理涉及的子图
    stats: dict                             # 统计信息
    
    def by_type(self, inference_type: str) -> list[InferenceResult]:
        """按推理类型筛选结果"""
        return [r for r in self.inferences if r.inference_type == inference_type]
    
    def to_llm_format(self) -> str:
        """将推理结果格式化为 LLM 可读的文本（供阶段 4 融合使用）"""
        ...
```

### 2.3 规则接口

```python
# engine/core.py

from abc import ABC, abstractmethod
from typing import Optional
from models.schema import get_connection


class Rule(ABC):
    """推理规则基类"""
    
    name: str = "base_rule"
    description: str = ""
    
    @abstractmethod
    def apply(self, entity_ids: list[str], db_path=None) -> list[InferenceResult]:
        """对给定实体列表应用此规则，返回推理结果"""
        ...
    
    @abstractmethod
    def is_applicable(self, entity_ids: list[str], db_path=None) -> bool:
        """检查此规则是否适用于当前实体集合"""
        ...


class ReasoningEngine:
    """推理引擎：管理规则的注册、执行和结果汇总"""
    
    def __init__(self, db_path=None):
        self.db_path = db_path
        self.rules: list[Rule] = []
        self.checkers: list = []  # 一致性检查器
    
    def register_rule(self, rule: Rule):
        """注册推理规则"""
        self.rules.append(rule)
    
    def register_checker(self, checker):
        """注册一致性检查器"""
        self.checkers.append(checker)
    
    def run(self, entity_ids: list[str]) -> ReasoningOutput:
        """执行推理流水线"""
        all_inferences = []
        all_conflicts = []
        subgraph_entities = set(entity_ids)
        
        # 1. 执行所有规则
        for rule in self.rules:
            if rule.is_applicable(entity_ids, self.db_path):
                results = rule.apply(entity_ids, self.db_path)
                all_inferences.extend(results)
                # 收集推理涉及的实体
                for r in results:
                    subgraph_entities.add(r.source_entity_id)
                    subgraph_entities.add(r.target_entity_id)
        
        # 2. 执行一致性检查
        for checker in self.checkers:
            conflicts = checker.check(entity_ids, all_inferences, self.db_path)
            all_conflicts.extend(conflicts)
        
        # 3. 构建子图
        subgraph = self._build_subgraph(list(subgraph_entities))
        
        # 4. 统计
        stats = {
            "rules_executed": len(self.rules),
            "inferences_count": len(all_inferences),
            "conflicts_count": len(all_conflicts),
            "subgraph_size": len(subgraph_entities),
        }
        
        return ReasoningOutput(
            entity_ids=entity_ids,
            inferences=all_inferences,
            conflicts=all_conflicts,
            subgraph=subgraph,
            stats=stats,
        )
    
    def _build_subgraph(self, entity_ids: list[str]) -> dict:
        """从实体列表构建子图（实体 + 直接关系）"""
        ...
```

---

## 三、七大推理规则详细设计

### 规则 1：传递闭包（Transitive Closure）

**本体语义**：如果关系类型声明为 `transitive=1`，则 A→B 且 B→C 可推导出 A→C。

**适用关系类型**：`depends_on`、`contains`、`derived_from`

```python
# engine/rules/transitive.py

class TransitiveClosureRule(Rule):
    name = "transitive_closure"
    description = "对可传递关系类型计算传递闭包"
    
    def is_applicable(self, entity_ids, db_path=None):
        # 检查是否存在可传递关系类型的边
        conn = get_connection(db_path)
        placeholders = ",".join("?" * len(entity_ids))
        row = conn.execute(
            f"""SELECT COUNT(*) as cnt FROM relations r
                JOIN relation_types rt ON r.type_id = rt.id
                WHERE rt.transitive = 1
                AND (r.source_id IN ({placeholders}) OR r.target_id IN ({placeholders}))""",
            entity_ids + entity_ids
        ).fetchone()
        conn.close()
        return row["cnt"] > 0
    
    def apply(self, entity_ids, db_path=None):
        conn = get_connection(db_path)
        results = []
        
        # 查询所有 transitive=1 的关系类型
        transitive_types = conn.execute(
            "SELECT id FROM relation_types WHERE transitive = 1"
        ).fetchall()
        
        for rt in transitive_types:
            rtype = rt["id"]
            for eid in entity_ids:
                # 用递归 CTE 计算传递闭包
                rows = conn.execute(
                    """WITH RECURSIVE closure AS (
                        SELECT target_id, 1 AS depth, source_id AS path_start
                        FROM relations
                        WHERE source_id = ? AND type_id = ?
                        UNION ALL
                        SELECT r.target_id, c.depth + 1, c.path_start
                        FROM relations r
                        JOIN closure c ON r.source_id = c.target_id
                        WHERE r.type_id = ? AND c.depth < 10
                    )
                    SELECT DISTINCT c.target_id, c.depth, c.path_start,
                           e.name AS target_name, e.type_id AS target_type
                    FROM closure c
                    JOIN entities e ON c.target_id = e.id
                    WHERE c.target_id != c.path_start
                    ORDER BY c.depth""",
                    (eid, rtype, rtype)
                ).fetchall()
                
                for row in rows:
                    results.append(InferenceResult(
                        rule_name=self.name,
                        inference_type="dependency",
                        source_entity_id=eid,
                        target_entity_id=row["target_id"],
                        relation_type=rtype,
                        evidence=f"{eid} --{rtype}({row['depth']}跳)--> {row['target_id']} (传递闭包)",
                        confidence=1.0 / (1.0 + row["depth"] * 0.1),  # 深度越深置信度越低
                        depth=row["depth"],
                    ))
        
        conn.close()
        return results
```

**输出示例**：
```
A depends_on B, B depends_on C
→ 推理：A depends_on C (depth=2, confidence=0.82)
→ 证据："A --depends_on(2跳)--> C (传递闭包)"
```

---

### 规则 2：对称推理（Symmetric Inference）

**本体语义**：如果关系类型声明为 `symmetric=1`，则 A→B 可推导出 B→A。

**适用关系类型**：`conflicts_with`、`relates_to`

```python
# engine/rules/symmetric.py

class SymmetricRule(Rule):
    name = "symmetric_inference"
    description = "对对称关系类型自动推导反向关系"
    
    def apply(self, entity_ids, db_path=None):
        conn = get_connection(db_path)
        results = []
        
        symmetric_types = conn.execute(
            "SELECT id FROM relation_types WHERE symmetric = 1"
        ).fetchall()
        
        for rt in symmetric_types:
            rtype = rt["id"]
            placeholders = ",".join("?" * len(entity_ids))
            rows = conn.execute(
                f"""SELECT r.source_id, r.target_id, r.confidence,
                           e1.name AS source_name, e2.name AS target_name
                    FROM relations r
                    JOIN entities e1 ON r.source_id = e1.id
                    JOIN entities e2 ON r.target_id = e2.id
                    WHERE r.type_id = ?
                    AND (r.source_id IN ({placeholders}) OR r.target_id IN ({placeholders}))""",
                [rtype] + entity_ids + entity_ids
            ).fetchall()
            
            seen = set()
            for row in rows:
                # 检查反向关系是否已存在
                reverse_key = f"{row['target_id']}->{rtype}->{row['source_id']}"
                if reverse_key in seen:
                    continue
                seen.add(reverse_key)
                
                exists = conn.execute(
                    "SELECT 1 FROM relations WHERE source_id=? AND target_id=? AND type_id=?",
                    (row["target_id"], row["source_id"], rtype)
                ).fetchone()
                
                if not exists:
                    results.append(InferenceResult(
                        rule_name=self.name,
                        inference_type="conflict" if rtype == "conflicts_with" else "relates_to",
                        source_entity_id=row["target_id"],
                        target_entity_id=row["source_id"],
                        relation_type=rtype,
                        evidence=f"{row['target_name']} --{rtype}--> {row['source_name']} (对称推理: 原关系 {row['source_name']}->{rtype}->{row['target_name']})",
                        confidence=row["confidence"],
                        depth=1,
                    ))
        
        conn.close()
        return results
```

---

### 规则 3：类型继承推理（Type Inheritance）

**本体语义**：如果实体类型 B 的 `parent_id` 指向 A，则 B 类型的实体继承 A 类型定义的属性约束和关系模板。

**当前状态**：`entity_types` 表有 `parent_id` 字段但完全未使用。

```python
# engine/rules/inheritance.py

class InheritanceRule(Rule):
    name = "type_inheritance"
    description = "沿类型层级继承关系约束"
    
    def apply(self, entity_ids, db_path=None):
        conn = get_connection(db_path)
        results = []
        
        for eid in entity_ids:
            # 获取实体的类型
            entity = conn.execute(
                "SELECT type_id FROM entities WHERE id = ?", (eid,)
            ).fetchone()
            if not entity:
                continue
            
            type_id = entity["type_id"]
            
            # 递归查询所有父类型
            parent_types = conn.execute(
                """WITH RECURSIVE type_tree AS (
                    SELECT id, parent_id FROM entity_types WHERE id = ?
                    UNION ALL
                    SELECT et.id, et.parent_id FROM entity_types et
                    JOIN type_tree tt ON et.id = tt.parent_id
                )
                SELECT id FROM type_tree WHERE id != ?""",
                (type_id, type_id)
            ).fetchall()
            
            # 查询同类型的其他实体（兄弟实体），发现共性模式
            siblings = conn.execute(
                """SELECT e2.id, e2.name, e2.type_id
                   FROM entities e2
                   WHERE e2.type_id = ? AND e2.id != ? AND e2.status = 'active'
                   LIMIT 10""",
                (type_id, eid)
            ).fetchall()
            
            # 如果兄弟实体有共同的关系模式，推理当前实体可能也有类似关系
            for sibling in siblings:
                sibling_relations = conn.execute(
                    """SELECT r.type_id, r.target_id, r.target_id AS rel_target,
                              e.name AS target_name, e.type_id AS target_type,
                              COUNT(*) as freq
                       FROM relations r
                       JOIN entities e ON r.target_id = e.id
                       WHERE r.source_id = ?
                       GROUP BY r.type_id, r.target_id""",
                    (sibling["id"],)
                ).fetchall()
                
                for rel in sibling_relations:
                    # 如果当前实体还没有这种关系，标记为潜在关系
                    existing = conn.execute(
                        "SELECT 1 FROM relations WHERE source_id=? AND type_id=? AND target_id=?",
                        (eid, rel["type_id"], rel["target_id"])
                    ).fetchone()
                    
                    if not existing:
                        results.append(InferenceResult(
                            rule_name=self.name,
                            inference_type="dependency",
                            source_entity_id=eid,
                            target_entity_id=rel["target_id"],
                            relation_type=rel["type_id"],
                            evidence=f"兄弟实体 {sibling['name']}({sibling['id']}) 具有 {rel['type_id']}->{rel['target_name']} 关系，当前实体可能也有 (类型继承推理)",
                            confidence=0.3,  # 低置信度，仅作为提示
                            depth=1,
                        ))
        
        conn.close()
        return results
```

---

### 规则 4：逆关系推理（Inverse Relation）

**本体语义**：某些关系天然有逆关系，如 A implements B → B is_implemented_by A。

```python
# engine/rules/inverse.py

# 逆关系映射表
INVERSE_MAP = {
    "implements": "is_implemented_by",   # 不新建关系类型，仅推理时标注
    "contains": "is_contained_in",
    "depends_on": "is_depended_by",
    "refines": "is_refined_by",
    "derived_from": "derives",
    "causes": "is_caused_by",
    "constrains": "is_constrained_by",
    "impacts": "is_impacted_by",
}

class InverseRelationRule(Rule):
    name = "inverse_relation"
    description = "从正向关系推导逆向关系"
    
    def apply(self, entity_ids, db_path=None):
        conn = get_connection(db_path)
        results = []
        placeholders = ",".join("?" * len(entity_ids))
        
        for forward, inverse in INVERSE_MAP.items():
            rows = conn.execute(
                f"""SELECT r.source_id, r.target_id, r.confidence,
                           e1.name AS source_name, e2.name AS target_name
                    FROM relations r
                    JOIN entities e1 ON r.source_id = e1.id
                    JOIN entities e2 ON r.target_id = e2.id
                    WHERE r.type_id = ?
                    AND (r.source_id IN ({placeholders}) OR r.target_id IN ({placeholders}))""",
                [forward] + entity_ids + entity_ids
            ).fetchall()
            
            for row in rows:
                # 当我们从 target 实体的视角看，它的逆关系
                if row["target_id"] in entity_ids:
                    results.append(InferenceResult(
                        rule_name=self.name,
                        inference_type="dependency",
                        source_entity_id=row["target_id"],
                        target_entity_id=row["source_id"],
                        relation_type=inverse,
                        evidence=f"{row['target_name']} {inverse} {row['source_name']} (逆关系推理: 原关系 {row['source_name']} {forward} {row['target_name']})",
                        confidence=row["confidence"],
                        depth=1,
                    ))
        
        conn.close()
        return results
```

---

### 规则 5：约束传播（Constraint Propagation）

**本体语义**：如果 A constrains B，且 B contains C，则 A 的约束传播到 C。

```python
# engine/rules/constraint.py

class ConstraintPropagationRule(Rule):
    name = "constraint_propagation"
    description = "沿包含关系传播约束"
    
    def apply(self, entity_ids, db_path=None):
        conn = get_connection(db_path)
        results = []
        
        for eid in entity_ids:
            # 找到直接约束当前实体的所有约束
            constraints = conn.execute(
                """SELECT r.source_id AS constraint_source, r.confidence,
                          e1.name AS constraint_name,
                          e2.name AS constrained_name
                   FROM relations r
                   JOIN entities e1 ON r.source_id = e1.id
                   JOIN entities e2 ON r.target_id = e2.id
                   WHERE r.target_id = ? AND r.type_id = 'constrains'""",
                (eid,)
            ).fetchall()
            
            # 找到当前实体包含的子实体
            children = conn.execute(
                """WITH RECURSIVE containment AS (
                    SELECT target_id FROM relations
                    WHERE source_id = ? AND type_id = 'contains'
                    UNION ALL
                    SELECT r.target_id FROM relations r
                    JOIN containment c ON r.source_id = c.target_id
                    WHERE r.type_id = 'contains'
                )
                SELECT c.target_id, e.name AS child_name
                FROM containment c
                JOIN entities e ON c.target_id = e.id""",
                (eid,)
            ).fetchall()
            
            # 约束传播：父实体的约束传递给子实体
            for constraint in constraints:
                for child in children:
                    # 检查子实体是否已有此约束
                    existing = conn.execute(
                        "SELECT 1 FROM relations WHERE source_id=? AND target_id=? AND type_id='constrains'",
                        (constraint["constraint_source"], child["target_id"])
                    ).fetchone()
                    
                    if not existing:
                        results.append(InferenceResult(
                            rule_name=self.name,
                            inference_type="constraint",
                            source_entity_id=constraint["constraint_source"],
                            target_entity_id=child["target_id"],
                            relation_type="constrains",
                            evidence=f"{constraint['constraint_name']} constrains {child['child_name']} (约束传播: 父实体 {constraint['constrained_name']} 被 {constraint['constraint_name']} 约束，子实体继承约束)",
                            confidence=constraint["confidence"] * 0.9,  # 传播衰减
                            depth=2,
                        ))
        
        conn.close()
        return results
```

---

### 规则 6：冲突检测（Conflict Detection）

**本体语义**：检测本体中的矛盾关系。

```python
# engine/rules/conflict.py

class ConflictDetectionRule(Rule):
    name = "conflict_detection"
    description = "检测本体中的矛盾关系"
    
    # 冲突规则定义
    CONFLICT_PATTERNS = [
        # 模式1: A depends_on B 且 A conflicts_with B
        ("depends_on", "conflicts_with", "依赖且冲突"),
        # 模式2: A contains B 且 B contains A（循环包含）
        ("contains", "contains", "循环包含"),
        # 模式3: A implements B 且 A conflicts_with B
        ("implements", "conflicts_with", "实现且冲突"),
    ]
    
    def apply(self, entity_ids, db_path=None):
        conn = get_connection(db_path)
        results = []
        
        for eid in entity_ids:
            # 模式1: 依赖且冲突
            deps = conn.execute(
                """SELECT r.target_id, e.name AS target_name
                   FROM relations r JOIN entities e ON r.target_id = e.id
                   WHERE r.source_id = ? AND r.type_id = 'depends_on'""",
                (eid,)
            ).fetchall()
            
            conflicts = conn.execute(
                """SELECT r.target_id, e.name AS target_name
                   FROM relations r JOIN entities e ON r.target_id = e.id
                   WHERE (r.source_id = ? OR r.target_id = ?) AND r.type_id = 'conflicts_with'""",
                (eid, eid)
            ).fetchall()
            
            conflict_targets = {c["target_id"] for c in conflicts}
            for dep in deps:
                if dep["target_id"] in conflict_targets:
                    entity_name = conn.execute(
                        "SELECT name FROM entities WHERE id=?", (eid,)
                    ).fetchone()["name"]
                    results.append(InferenceResult(
                        rule_name=self.name,
                        inference_type="conflict",
                        source_entity_id=eid,
                        target_entity_id=dep["target_id"],
                        relation_type="conflict",
                        evidence=f"{entity_name} 同时 depends_on 且 conflicts_with {dep['target_name']} (矛盾检测)",
                        confidence=1.0,
                        depth=1,
                    ))
            
            # 模式2: 循环包含检测
            cycle = conn.execute(
                """WITH RECURSIVE chain AS (
                    SELECT target_id, 1 AS depth FROM relations
                    WHERE source_id = ? AND type_id = 'contains'
                    UNION ALL
                    SELECT r.target_id, c.depth + 1 FROM relations r
                    JOIN chain c ON r.source_id = c.target_id
                    WHERE r.type_id = 'contains' AND c.depth < 20
                )
                SELECT target_id FROM chain WHERE target_id = ? LIMIT 1""",
                (eid, eid)
            ).fetchone()
            
            if cycle:
                entity_name = conn.execute(
                    "SELECT name FROM entities WHERE id=?", (eid,)
                ).fetchone()["name"]
                results.append(InferenceResult(
                    rule_name=self.name,
                    inference_type="conflict",
                    source_entity_id=eid,
                    target_entity_id=eid,
                    relation_type="circular_contains",
                    evidence=f"{entity_name} 存在循环包含 (矛盾检测)",
                    confidence=1.0,
                    depth=0,
                ))
        
        conn.close()
        return results
```

---

### 规则 7：影响分析（Impact Analysis）

**本体语义**：修改某个实体后，通过关系网络推导受影响的所有实体。

```python
# engine/rules/impact.py

class ImpactAnalysisRule(Rule):
    name = "impact_analysis"
    description = "BFS遍历关系网络，分析变更影响范围"
    
    # 正向关系（source 变更影响 target）
    FORWARD_TYPES = ["causes", "contains", "impacts", "relates_to"]
    # 反向关系（target 变更影响 source）
    REVERSE_TYPES = ["depends_on", "implements", "refines", "constrains"]
    # 双向关系
    BIDIRECTIONAL_TYPES = ["conflicts_with"]
    
    def apply(self, entity_ids, db_path=None):
        conn = get_connection(db_path)
        results = []
        
        for eid in entity_ids:
            visited = {eid}
            queue = [(eid, 0, [])]
            
            while queue:
                current, depth, path = queue.pop(0)
                
                if depth >= 5:  # 最大深度
                    continue
                
                # 正向遍历
                rows = conn.execute(
                    """SELECT r.target_id, r.type_id, r.confidence,
                              e.name AS target_name, e.type_id AS target_type
                       FROM relations r
                       JOIN entities e ON r.target_id = e.id
                       WHERE r.source_id = ? AND r.type_id IN ({})""".format(
                           ",".join(f"'{t}'" for t in self.FORWARD_TYPES)
                       ),
                    (current,)
                ).fetchall()
                
                for row in rows:
                    if row["target_id"] not in visited:
                        visited.add(row["target_id"])
                        new_path = path + [f"{current} --{row['type_id']}--> {row['target_id']}"]
                        results.append(InferenceResult(
                            rule_name=self.name,
                            inference_type="impact",
                            source_entity_id=eid,
                            target_entity_id=row["target_id"],
                            relation_type=row["type_id"],
                            evidence=" -> ".join(new_path) + " (影响分析-BFS)",
                            confidence=row["confidence"] * (0.8 ** depth),
                            depth=depth + 1,
                        ))
                        queue.append((row["target_id"], depth + 1, new_path))
                
                # 反向遍历
                rows = conn.execute(
                    """SELECT r.source_id, r.type_id, r.confidence,
                              e.name AS source_name, e.type_id AS source_type
                       FROM relations r
                       JOIN entities e ON r.source_id = e.id
                       WHERE r.target_id = ? AND r.type_id IN ({})""".format(
                           ",".join(f"'{t}'" for t in self.REVERSE_TYPES)
                       ),
                    (current,)
                ).fetchall()
                
                for row in rows:
                    if row["source_id"] not in visited:
                        visited.add(row["source_id"])
                        new_path = path + [f"{current} <--{row['type_id']}-- {row['source_id']}"]
                        results.append(InferenceResult(
                            rule_name=self.name,
                            inference_type="impact",
                            source_entity_id=eid,
                            target_entity_id=row["source_id"],
                            relation_type=row["type_id"],
                            evidence=" -> ".join(new_path) + " (影响分析-BFS反向)",
                            confidence=row["confidence"] * (0.8 ** depth),
                            depth=depth + 1,
                        ))
                        queue.append((row["source_id"], depth + 1, new_path))
        
        conn.close()
        return results
```

---

## 四、一致性检查器

```python
# engine/checker.py

class ConsistencyChecker:
    """本体一致性检查器"""
    
    def check(self, entity_ids, inferences, db_path=None):
        """检查推理结果和已有数据的一致性"""
        issues = []
        
        # 检查1: 类型兼容性
        issues.extend(self._check_type_compatibility(entity_ids, db_path))
        
        # 检查2: 孤立实体（没有任何关系的实体）
        issues.extend(self._check_orphan_entities(entity_ids, db_path))
        
        # 检查3: 推理结果中的重复
        issues.extend(self._check_duplicate_inferences(inferences))
        
        return issues
    
    def _check_type_compatibility(self, entity_ids, db_path):
        """检查关系两端实体类型是否兼容"""
        # 如：actor 不应该 depends_on test_case
        ...
    
    def _check_orphan_entities(self, entity_ids, db_path):
        """检查孤立实体"""
        ...
    
    def _check_duplicate_inferences(self, inferences):
        """检查推理结果重复"""
        ...
```

---

## 五、改造 parse_prd 流水线

### 5.1 新流水线对比

```
当前流水线（6 次 LLM）:
  1. [LLM] 实体抽取
  2. [LLM] 语义匹配
  3. [SQL] 图搜索 (BFS)
  4a. [LLM] 依赖推理
  4b. [LLM] 约束推理
  4c. [LLM] 影响推理
  5. [LLM] PRD 融合

新流水线（2 次 LLM）:
  1. [LLM] 实体抽取 + 语义匹配（合并为一次调用）
  2. [规则引擎] 推理流水线
     ├─ 传递闭包
     ├─ 对称推理
     ├─ 逆关系推理
     ├─ 约束传播
     ├─ 影响分析 (BFS)
     ├─ 类型继承
     └─ 冲突检测
  3. [规则引擎] 一致性检查
  4. [LLM] PRD 融合（将推理结果转为增广文本）
```

### 5.2 代码改造点

```python
# parser/llm_parser.py 中的 parse_prd() 函数改造

def parse_prd(content: str, db_path=None) -> dict:
    # 阶段 1: LLM 实体抽取 + 语义匹配（保持不变，已可用）
    prd_entities = _extract_prd_entities(content)
    match_results = _semantic_match_entities(prd_entities, db_path)
    matched_ids = [m["matched_entity_id"] for m in match_results if m.get("match")]
    
    if not matched_ids:
        # 没有匹配到任何实体，直接返回原文
        return {
            "enriched_prd": content,
            "summary": {"entities_extracted": len(prd_entities), "entities_matched": 0},
            "pipeline_trace": {},
        }
    
    # 阶段 2: 规则引擎推理（替换原来的阶段 2c + 3）
    from engine.core import ReasoningEngine
    from engine.rules.transitive import TransitiveClosureRule
    from engine.rules.symmetric import SymmetricRule
    from engine.rules.inverse import InverseRelationRule
    from engine.rules.constraint import ConstraintPropagationRule
    from engine.rules.impact import ImpactAnalysisRule
    from engine.rules.inheritance import InheritanceRule
    from engine.rules.conflict import ConflictDetectionRule
    from engine.checker import ConsistencyChecker
    
    engine = ReasoningEngine(db_path=db_path)
    engine.register_rule(TransitiveClosureRule())
    engine.register_rule(SymmetricRule())
    engine.register_rule(InverseRelationRule())
    engine.register_rule(ConstraintPropagationRule())
    engine.register_rule(ImpactAnalysisRule())
    engine.register_rule(InheritanceRule())
    engine.register_rule(ConflictDetectionRule())
    engine.register_checker(ConsistencyChecker())
    
    reasoning_output = engine.run(matched_ids)
    
    # 阶段 3: LLM 融合（将推理结果转为增强版 PRD）
    enriched_prd = _fuse_prd_from_reasoning(
        content, reasoning_output
    )
    
    return {
        "enriched_prd": enriched_prd,
        "summary": {
            "entities_extracted": len(prd_entities),
            "entities_matched": len(matched_ids),
            "inferences": reasoning_output.stats,
            "pipeline_stages": ["llm_extract_match", "rule_engine", "llm_fuse"],
        },
        "pipeline_trace": {
            "stage1_entities": prd_entities,
            "stage1_matches": match_results,
            "stage2_reasoning": reasoning_output,
        },
    }


def _fuse_prd_from_reasoning(content: str, reasoning: ReasoningOutput) -> str:
    """将规则引擎的推理结果融合为增强版 PRD（LLM 调用）"""
    # 构建结构化的推理结果文本
    reasoning_text = reasoning.to_llm_format()
    
    system_prompt = """你是 PRD 增广优化专家。基于规则推理引擎提供的推理结果，将用户 PRD 原文增广为完善的需求文档。

推理结果中的每条推理都有明确的规则名称、证据和置信度，请忠实呈现，不要添加未在推理结果中出现的信息。

[输出格式同当前 _fuse_prd 的格式要求]"""
    
    user_prompt = f"""## 用户输入的 PRD 原文
{content}

## 规则引擎推理结果
{reasoning_text}

请按照规定的 Markdown 结构输出增强版 PRD。"""
    
    resp = _call_llm_with_retry(system_prompt, user_prompt, ...)
    return resp
```

---

## 六、开发计划

### Phase 1: 基础设施（1-2 天）

| 序号 | 任务 | 产出 | 依赖 |
|------|------|------|------|
| 1.1 | 创建 `engine/` 目录结构 | 目录 + `__init__.py` | 无 |
| 1.2 | 实现 `engine/result.py` | `InferenceResult` + `ReasoningOutput` 数据结构 | 1.1 |
| 1.3 | 实现 `engine/core.py` | `Rule` 基类 + `ReasoningEngine` 核心引擎 | 1.2 |
| 1.4 | 实现单元测试框架 | `tests/test_engine_core.py` | 1.3 |

### Phase 2: 核心规则实现（2-3 天）

| 序号 | 任务 | 产出 | 依赖 |
|------|------|------|------|
| 2.1 | 传递闭包规则 | `engine/rules/transitive.py` | Phase 1 |
| 2.2 | 对称推理规则 | `engine/rules/symmetric.py` | Phase 1 |
| 2.3 | 逆关系规则 | `engine/rules/inverse.py` | Phase 1 |
| 2.4 | 影响分析规则 | `engine/rules/impact.py`（从 PRDOntology 迁移 + 增强） | Phase 1 |
| 2.5 | 冲突检测规则 | `engine/rules/conflict.py` | Phase 1 |
| 2.6 | 约束传播规则 | `engine/rules/constraint.py` | Phase 1 |
| 2.7 | 类型继承规则 | `engine/rules/inheritance.py` | Phase 1 |
| 2.8 | 每个规则的单元测试 | `tests/test_rules_*.py` | 2.1-2.7 |

### Phase 3: 一致性检查 + 集成（1-2 天）

| 序号 | 任务 | 产出 | 依赖 |
|------|------|------|------|
| 3.1 | 一致性检查器 | `engine/checker.py` | Phase 2 |
| 3.2 | 改造 `parse_prd` 流水线 | 修改 `parser/llm_parser.py` | Phase 2 |
| 3.3 | 改造 `_fuse_prd` → `_fuse_prd_from_reasoning` | 修改 `parser/llm_parser.py` | 3.2 |
| 3.4 | 端到端测试 | `tests/test_parse_prd_with_engine.py` | 3.2, 3.3 |

### Phase 4: 增强 query_ontology 工具（1 天）

| 序号 | 任务 | 产出 | 依赖 |
|------|------|------|------|
| 4.1 | 在 `query_ontology` 中增加推理查询 | 推理结果可作为查询结果返回 | Phase 3 |
| 4.2 | 新增 `reason_ontology` MCP 工具 | 直接暴露推理引擎给 Agent | Phase 3 |

### Phase 5: 推理结果缓存与增量（未来）

| 序号 | 任务 | 产出 | 依赖 |
|------|------|------|------|
| 5.1 | 推理结果缓存 | `engine/cache.py` | Phase 3 |
| 5.2 | 增量更新通知 | 当本体数据变更时标记缓存失效 | 5.1 |

---

## 七、测试策略

### 7.1 规则单元测试

每个规则独立测试，使用预构造的测试数据：

```python
# tests/test_rules_transitive.py

def test_transitive_closure():
    """A depends_on B, B depends_on C → 推理出 A depends_on C"""
    # 1. 准备测试数据库
    # 2. 插入 A, B, C 实体
    # 3. 插入 A->B, B->C 的 depends_on 关系
    # 4. 运行 TransitiveClosureRule
    # 5. 断言结果包含 A->C 的推理
```

### 7.2 端到端测试

使用现有的测试数据（CCB + Anthropic），验证完整流水线：

```python
# tests/test_parse_prd_with_engine.py

def test_parse_prd_with_rule_engine():
    """端到端测试：PRD → LLM抽取 → 规则推理 → LLM融合 → 增强版PRD"""
    # 1. 准备测试 PRD
    # 2. 调用 parse_prd()
    # 3. 验证推理结果包含规则推理（而非 LLM 猜测）
    # 4. 验证增强版 PRD 包含推理证据
```

### 7.3 对比测试

对比改造前后的输出质量：

```
同一份 PRD，分别用旧流水线（6次LLM）和新流水线（2次LLM+规则引擎）解析，
对比：
- 推理结果的准确性（规则推理 vs LLM猜测）
- 推理结果的可复现性（多次运行一致性）
- LLM 调用成本（6次 vs 2次）
- 响应时间
```

---

## 八、风险与应对

| 风险 | 等级 | 应对 |
|------|------|------|
| 规则引擎覆盖面不足，遗漏 LLM 能发现的推理 | 🟡 中 | 保留 LLM 推理作为可选的"补充推理"模式 |
| 类型继承规则产生过多低置信度噪声 | 🟡 中 | 设置置信度阈值，低置信度结果不展示 |
| 改造期间影响现有功能 | 🔴 高 | 新增 `parse_prd_v2` 函数，不修改原 `parse_prd`，灰度切换 |
| 递归 CTE 在大数据量下性能问题 | 🟢 低 | 设置 max_depth 限制 + 结果缓存 |

---

## 九、验收标准

1. **LLM 调用从 6 次降到 2 次**（实体抽取+匹配 + PRD融合）
2. **推理结果可复现**：同一输入，多次运行结果完全一致
3. **推理结果有据可依**：每条推理结果包含 `rule_name` 和 `evidence`
4. **冲突检测能力**：能发现本体中的矛盾关系
5. **传递闭包**：能正确推导多跳间接依赖
6. **影响分析**：能通过 BFS 找出变更影响范围
7. **单元测试覆盖**：每个规则有独立测试，整体覆盖 >80%
8. **端到端可用**：通过 `parse_prd` 完整跑通新流水线

---

## 十、文件清单

### 新增文件

```
engine/
├── __init__.py              # 引擎包初始化
├── core.py                   # Rule 基类 + ReasoningEngine
├── result.py                 # InferenceResult + ReasoningOutput
├── checker.py                # ConsistencyChecker
├── rules/
│   ├── __init__.py
│   ├── transitive.py         # 传递闭包
│   ├── symmetric.py          # 对称推理
│   ├── inheritance.py        # 类型继承
│   ├── inverse.py            # 逆关系
│   ├── constraint.py         # 约束传播
│   ├── conflict.py           # 冲突检测
│   └── impact.py             # 影响分析

tests/
├── test_engine_core.py       # 引擎核心测试
├── test_rules_transitive.py  # 传递闭包测试
├── test_rules_symmetric.py   # 对称推理测试
├── test_rules_impact.py      # 影响分析测试
├── test_rules_conflict.py    # 冲突检测测试
└── test_parse_prd_with_engine.py  # 端到端测试
```

### 修改文件

```
parser/llm_parser.py          # 改造 parse_prd() 函数，接入规则引擎
tools/parse_prd.py            # 无需修改（接口不变）
tools/query_ontology.py       # 增加推理查询能力
server.py                     # 可能新增 reason_ontology 工具注册
```

### 不变文件

```
models/schema.py              # 表结构不变
models/entity.py              # Entity CRUD 不变
models/relation.py            # Relation CRUD 不变（但 get_transitive_relations 将被规则引擎封装）
```
