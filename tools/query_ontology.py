"""
MCP Tool: query_ontology

查询 Ontology 中的实体和关系。
支持三种查询方式：
A. 按实体名称查询
B. 按实体 ID 查询
C. 关键词搜索

Phase 4 增强：支持推理查询（include_inferences=true 时返回推理结果）
"""

from typing import Optional, List

from models.entity import get_entity, get_entity_by_name, search_entities
from models.relation import get_entity_relations, get_transitive_relations


def register(mcp):
    """注册 query_ontology 工具到 MCP 服务器。"""

    @mcp.tool()
    def query_ontology(
        entity_name: Optional[str] = None,
        entity_id: Optional[str] = None,
        query: Optional[str] = None,
        relation_types: Optional[List[str]] = None,
        limit: int = 10,
        include_inferences: bool = False,
        inference_rules: Optional[List[str]] = None,
        include_future: bool = False,
        include_expired: bool = False,
        db_path: Optional[str] = None,
    ) -> dict:
        """查询 Ontology 中的实体和关系，可选附加推理结果。

        支持三种查询方式：
        A. 按实体名称查询（entity_name）
        B. 按实体 ID 查询（entity_id）
        C. 关键词搜索（query）

        ## Phase 4 新增：推理查询

        当 `include_inferences=true` 时，对查询到的实体运行推理引擎，
        返回额外的隐含依赖、约束、影响范围和冲突检测结果。
        适用于 Agent 需要了解实体完整关系网络的场景。

        ## 时间关系过滤（Phase 5）

        默认只返回当前已生效的实关系。如需包含未来/过期关系，设置：
        - `include_future=true`：包含未来生效的虚关系
        - `include_expired=true`：包含已过期关系

        ## Args

        - `entity_name`: 按实体名称查询
        - `entity_id`: 按实体 ID 查询（优先于 entity_name）
        - `query`: 关键词搜索
        - `relation_types`: 可选，筛选关系类型，如 ["depends_on", "impacts"]
        - `limit`: 搜索结果的条数限制（仅对方案 C 有效）
        - `include_inferences`: 是否附加推理引擎结果（默认 false）
        - `inference_rules`: 指定推理规则子集（如 ["transitive_closure", "impact_analysis"]），
          仅 include_inferences=true 时有效，默认执行全部规则
        - `include_future`: 是否包含未来生效的虚关系（默认 false）
        - `include_expired`: 是否包含已过期关系（默认 false）
        - `db_path`: 可选，Ontology SQLite 数据库路径

        ## Returns

        - `entity`: 实体信息（方案 A/B）
        - `relations`: 直接关联关系列表
        - `search_results`: 搜索匹配的实体列表（仅方案 C）
        - `inferences`: 推理结果列表（include_inferences=true 时返回）
        - `inference_summary`: 推理结果可读摘要（include_inferences=true 时返回）
        """
        entity = None
        relations = []
        search_results = []
        target_entity_ids = []

        if entity_id:
            # 方案 B：按 ID 查询
            entity = get_entity(entity_id, db_path)
            if entity:
                relations = get_entity_relations(entity_id, relation_types, db_path, include_future, include_expired)
                target_entity_ids = [entity_id]

        elif entity_name:
            # 方案 A：按名称查询
            entity = get_entity_by_name(entity_name, db_path)
            if entity:
                relations = get_entity_relations(
                    entity["id"], relation_types, db_path, include_future, include_expired
                )
                target_entity_ids = [entity["id"]]

        elif query:
            # 方案 C：关键词搜索
            matches = search_entities(query, limit, db_path)
            for e in matches:
                entity_relations = get_entity_relations(
                    e["id"], relation_types, db_path, include_future, include_expired
                )
                search_results.append(
                    {
                        "entity": e,
                        "relations": entity_relations,
                    }
                )
            target_entity_ids = [e["id"] for e in matches]

        result = {
            "entity": entity,
            "relations": relations,
            "search_results": search_results,
        }

        # Phase 4: 附加推理结果
        if include_inferences and target_entity_ids:
            from tools.reason_ontology import _build_engine

            engine = _build_engine(db_path, inference_rules, include_future=include_future, include_expired=include_expired)
            output = engine.run(target_entity_ids)

            result["inferences"] = [r.to_dict() for r in output.inferences]
            result["inference_conflicts"] = [r.to_dict() for r in output.conflicts]
            result["inference_summary"] = output.to_llm_format()
            result["inference_stats"] = output.stats

        return result

    return query_ontology
