"""
MCP Tool: reason_ontology

直接暴露推理引擎给 Coding Agent，支持：
- 对指定实体集合运行完整推理流水线
- 按推理类型筛选结果
- 按规则名称筛选结果
- 指定单条规则执行
- 获取一致性检查报告

这是 Phase 4 的核心新工具，让 Agent 可以在不解析 PRD 的情况下
直接查询本体的推理结果，用于代码审查、影响分析、冲突排查等场景。
"""

from typing import Optional, List

from engine.core import ReasoningEngine
from engine.rules.transitive import TransitiveClosureRule
from engine.rules.symmetric import SymmetricRule
from engine.rules.inverse import InverseRelationRule
from engine.rules.constraint import ConstraintPropagationRule
from engine.rules.impact import ImpactAnalysisRule
from engine.rules.inheritance import InheritanceRule
from engine.rules.conflict import ConflictDetectionRule
from engine.checker import ConsistencyChecker
from models.entity import get_entity, get_entity_by_name, search_entities


# 规则注册表（名称 -> 类）
RULE_REGISTRY = {
    "transitive_closure": TransitiveClosureRule,
    "symmetric_inference": SymmetricRule,
    "inverse_relation": InverseRelationRule,
    "constraint_propagation": ConstraintPropagationRule,
    "impact_analysis": ImpactAnalysisRule,
    "type_inheritance": InheritanceRule,
    "conflict_detection": ConflictDetectionRule,
}


def _build_engine(db_path=None, rules_only: Optional[List[str]] = None):
    """构建推理引擎，可选择只注册部分规则。"""
    engine = ReasoningEngine(db_path=db_path)

    if rules_only:
        for rule_name in rules_only:
            rule_cls = RULE_REGISTRY.get(rule_name)
            if rule_cls:
                engine.register_rule(rule_cls())
    else:
        engine.register_rule(TransitiveClosureRule())
        engine.register_rule(SymmetricRule())
        engine.register_rule(InverseRelationRule())
        engine.register_rule(ConstraintPropagationRule())
        engine.register_rule(ImpactAnalysisRule())
        engine.register_rule(InheritanceRule())
        engine.register_rule(ConflictDetectionRule())

    engine.register_checker(ConsistencyChecker())
    return engine


def _resolve_entity_ids(
    entity_ids: Optional[List[str]] = None,
    entity_names: Optional[List[str]] = None,
    query: Optional[str] = None,
    db_path=None,
) -> list:
    """将多种输入方式统一解析为实体 ID 列表。"""
    resolved = []

    if entity_ids:
        for eid in entity_ids:
            entity = get_entity(eid, db_path)
            if entity:
                resolved.append(eid)

    if entity_names:
        for name in entity_names:
            entity = get_entity_by_name(name, db_path)
            if entity:
                resolved.append(entity["id"])

    if query and not resolved:
        # 用关键词搜索，取前 5 个匹配实体作为入口
        matches = search_entities(query, limit=5, db_path=db_path)
        for m in matches:
            resolved.append(m["id"])

    return resolved


def register(mcp):
    """注册 reason_ontology 工具到 MCP 服务器。"""

    @mcp.tool()
    def reason_ontology(
        entity_ids: Optional[List[str]] = None,
        entity_names: Optional[List[str]] = None,
        query: Optional[str] = None,
        rules_only: Optional[List[str]] = None,
        inference_type: Optional[str] = None,
        include_conflicts: bool = True,
        include_subgraph: bool = False,
        max_depth: int = 5,
        db_path: Optional[str] = None,
    ) -> dict:
        """**本体推理引擎工具**：对指定实体运行规则推理，返回隐含的依赖/约束/影响/冲突。

        本工具直接暴露推理引擎给 Agent，无需通过 PRD 解析即可获取推理结果。
        适用于代码审查、变更影响分析、冲突排查、依赖梳理等场景。

        ## 推理规则（7 大规则）

        | 规则名称 | 说明 |
        |---------|------|
        | `transitive_closure` | 传递闭包（A->B->C 推导 A->C） |
        | `symmetric_inference` | 对称关系自动推导反向 |
        | `inverse_relation` | 逆关系推理（A depends_on B -> B is_depended_by A） |
        | `constraint_propagation` | 约束沿包含关系传播 |
        | `impact_analysis` | BFS 遍历影响范围 |
        | `type_inheritance` | 兄弟实体共性关系推导 |
        | `conflict_detection` | 4 种矛盾模式检测 |

        ## 输入方式（三选一）

        1. `entity_ids`: 直接传入实体 ID 列表（如 `["func:user_login", "mod:auth_module"]`）
        2. `entity_names`: 传入实体名称列表（如 `["用户登录", "认证模块"]`）
        3. `query`: 关键词搜索，自动匹配前 5 个实体作为推理入口

        ## Args

        - `entity_ids`: 实体 ID 列表
        - `entity_names`: 实体名称列表（与 entity_ids 二选一，可组合使用）
        - `query`: 关键词搜索（当 entity_ids 和 entity_names 都为空时使用）
        - `rules_only`: 只执行指定规则（如 `["transitive_closure", "impact_analysis"]`）
        - `inference_type`: 筛选推理类型（`dependency` / `constraint` / `impact` / `conflict`）
        - `include_conflicts`: 是否包含一致性检查结果（默认 true）
        - `include_subgraph`: 是否返回推理子图（默认 false，减少输出体积）
        - `max_depth`: 推理最大深度（默认 5，影响 BFS 遍历深度）
        - `db_path`: 可选，Ontology SQLite 数据库路径

        ## Returns

        - `entity_ids`: 实际参与推理的实体 ID 列表
        - `inferences`: 推理结果列表（每条含 rule_name, inference_type, source, target, evidence, confidence, depth）
        - `conflicts`: 冲突检测结果（include_conflicts=true 时返回）
        - `stats`: 推理统计（按规则/类型分组）
        - `subgraph`: 推理子图（include_subgraph=true 时返回）
        - `llm_summary`: 推理结果的可读文本摘要（可直接用于 Agent 上下文）
        """
        # 解析实体 IDs
        resolved_ids = _resolve_entity_ids(entity_ids, entity_names, query, db_path)

        if not resolved_ids:
            return {
                "entity_ids": [],
                "inferences": [],
                "conflicts": [],
                "stats": {"rules_executed": 0, "inferences_count": 0, "conflicts_count": 0},
                "llm_summary": "未找到匹配的实体，无法执行推理。",
            }

        # 构建引擎
        engine = _build_engine(db_path, rules_only)

        # 执行推理
        output = engine.run(resolved_ids)

        # 按推理类型筛选
        inferences = output.inferences
        if inference_type:
            inferences = [r for r in inferences if r.inference_type == inference_type]

        # 构建结果
        result = {
            "entity_ids": resolved_ids,
            "inferences": [r.to_dict() for r in inferences],
            "conflicts": [r.to_dict() for r in output.conflicts] if include_conflicts else [],
            "stats": {
                **output.stats,
                "filtered_inferences_count": len(inferences),
            },
            "llm_summary": output.to_llm_format(),
        }

        if include_subgraph:
            result["subgraph"] = output.subgraph

        return result

    return reason_ontology
