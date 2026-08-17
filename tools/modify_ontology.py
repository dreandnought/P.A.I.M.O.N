"""
MCP Tool: modify_ontology

根据自然语言描述修改已有 Ontology。
默认 dry_run=true，先返回变更计划；dry_run=false 时执行写入。
"""

from typing import Optional

from parser.llm_parser import plan_ontology_changes
from models.entity import (
    create_entity,
    delete_entity,
    entity_exists,
    find_or_create_entity,
    get_entity,
    update_entity,
    compute_relation_time_from_endpoints,
)
from models.relation import (
    create_relation,
    delete_relation,
    get_entity_relations,
    get_relation_by_entities,
    update_relation,
)


def _build_target_context(target_entity_id, db_path):
    """构建目标实体及其关系的上下文字符串。"""
    entity = get_entity(target_entity_id, db_path)
    if not entity:
        return None
    relations = get_entity_relations(target_entity_id, db_path=db_path)
    lines = [
        f"实体: {entity['id']} | {entity['name']} ({entity.get('type_name', entity.get('type_id', ''))})",
        f"描述: {entity.get('description', '')}",
    ]
    if relations:
        lines.append("关系:")
        for r in relations:
            direction = "→" if r["direction"] == "outgoing" else "←"
            lines.append(
                f"  {r['relation_type']} {direction} {r['related_entity_id']} ({r['related_entity_name']})"
            )
    return "\n".join(lines)


def _validate_plan(plan, db_path):
    """校验变更计划中的实体/关系 ID 是否有效，返回校验结果和错误列表。"""
    errors = []
    for e in plan["entities"]["update"]:
        eid = e.get("entity_id")
        if eid and not entity_exists(eid, db_path):
            errors.append(f"update 实体不存在: {eid}")
    for e in plan["entities"]["delete"]:
        eid = e.get("entity_id")
        if eid and not entity_exists(eid, db_path):
            errors.append(f"delete 实体不存在: {eid}")

    for r in plan["relations"]["update"]:
        rid = r.get("relation_id")
        if rid:
            # relation_id 格式：type_id:source_id->target_id
            parts = rid.split(":", 1)
            if len(parts) == 2:
                type_id, rest = parts
                if "->" in rest:
                    source_id, target_id = rest.split("->", 1)
                    if not get_relation_by_entities(source_id, target_id, type_id, db_path):
                        errors.append(f"update 关系不存在: {rid}")
    for r in plan["relations"]["delete"]:
        rid = r.get("relation_id")
        if rid:
            parts = rid.split(":", 1)
            if len(parts) == 2:
                type_id, rest = parts
                if "->" in rest:
                    source_id, target_id = rest.split("->", 1)
                    if not get_relation_by_entities(source_id, target_id, type_id, db_path):
                        errors.append(f"delete 关系不存在: {rid}")

    return len(errors) == 0, errors


def _execute_modify_plan(plan, db_path):
    """执行 modify_ontology 的变更计划。"""
    created_entities = []
    updated_entities = []
    deleted_entities = []
    created_relations = []
    updated_relations = []
    deleted_relations = []
    failed_items = []

    # 实体创建
    for e in plan["entities"]["create"]:
        try:
            entity, created = find_or_create_entity(
                name=e["name"],
                type_id=e["type"],
                description=e.get("description", ""),
                suggested_id=e.get("suggested_id") or e.get("entity_id"),
                confidence=e.get("confidence", 0.8),
                source="nlp_modify",
                available_from=e.get("available_from"),
                db_path=db_path,
            )
            if created:
                created_entities.append(entity)
            else:
                updated_entities.append(entity)
        except Exception as ex:
            failed_items.append({"type": "entity.create", "data": e, "error": str(ex)})

    # 实体更新
    for e in plan["entities"]["update"]:
        eid = e.get("entity_id")
        if not eid or not entity_exists(eid, db_path):
            failed_items.append({"type": "entity.update", "data": e, "error": "实体不存在"})
            continue
        try:
            updates = {k: v for k, v in e.items() if k != "entity_id" and v is not None}
            if updates:
                update_entity(eid, updates, db_path=db_path)
            updated_entities.append(get_entity(eid, db_path))
        except Exception as ex:
            failed_items.append({"type": "entity.update", "data": e, "error": str(ex)})

    # 实体删除
    for e in plan["entities"]["delete"]:
        eid = e.get("entity_id")
        if not eid or not entity_exists(eid, db_path):
            failed_items.append({"type": "entity.delete", "data": e, "error": "实体不存在"})
            continue
        try:
            delete_entity(eid, db_path=db_path)
            deleted_entities.append({"entity_id": eid})
        except Exception as ex:
            failed_items.append({"type": "entity.delete", "data": e, "error": str(ex)})

    # 关系创建
    for r in plan["relations"]["create"]:
        source_id = r.get("source_id")
        target_id = r.get("target_id")
        type_id = r.get("relation_type")
        if not all([source_id, target_id, type_id]):
            failed_items.append({"type": "relation.create", "data": r, "error": "缺少 source_id/target_id/relation_type"})
            continue
        if not (entity_exists(source_id, db_path) and entity_exists(target_id, db_path)):
            failed_items.append({"type": "relation.create", "data": r, "error": "源或目标实体不存在"})
            continue
        try:
            # 根据端点实体的 available_from 计算虚关系化
            valid_from = r.get("valid_from")
            valid_until = r.get("valid_until")
            metadata = {"description": r.get("description", "")}

            if not valid_from:
                computed_from, caused_by = compute_relation_time_from_endpoints(
                    source_id, target_id, db_path
                )
                if computed_from:
                    valid_from = computed_from
                    metadata["time_caused_by_entities"] = caused_by

            relation = create_relation(
                type_id=type_id,
                source_id=source_id,
                target_id=target_id,
                confidence=r.get("confidence", 0.8),
                source="nlp_modify",
                metadata=metadata,
                db_path=db_path,
                valid_from=valid_from,
                valid_until=valid_until,
            )
            created_relations.append(relation)
        except Exception as ex:
            failed_items.append({"type": "relation.create", "data": r, "error": str(ex)})

    # 关系更新
    for r in plan["relations"]["update"]:
        rid = r.get("relation_id")
        if not rid:
            failed_items.append({"type": "relation.update", "data": r, "error": "缺少 relation_id"})
            continue
        parts = rid.split(":", 1)
        if len(parts) != 2 or "->" not in parts[1]:
            failed_items.append({"type": "relation.update", "data": r, "error": "relation_id 格式错误"})
            continue
        type_id, rest = parts
        source_id, target_id = rest.split("->", 1)
        relation = get_relation_by_entities(source_id, target_id, type_id, db_path)
        if not relation:
            failed_items.append({"type": "relation.update", "data": r, "error": "关系不存在"})
            continue
        try:
            updates = {k: v for k, v in r.items() if k != "relation_id" and v is not None}
            # description 不是独立列，合并进 metadata（与 create 分支行为一致）
            if "description" in updates:
                import json as _json

                desc = updates.pop("description")
                existing = relation.get("metadata")
                if isinstance(existing, str) and existing:
                    try:
                        meta = _json.loads(existing)
                    except ValueError:
                        meta = {}
                elif isinstance(existing, dict):
                    meta = dict(existing)
                else:
                    meta = {}
                if not isinstance(meta, dict):
                    meta = {}
                meta["description"] = desc
                updates["metadata"] = _json.dumps(meta, ensure_ascii=False)
            if updates:
                update_relation(relation["id"], updates, db_path=db_path)
            updated_relations.append(get_relation_by_entities(source_id, target_id, type_id, db_path))
        except Exception as ex:
            failed_items.append({"type": "relation.update", "data": r, "error": str(ex)})

    # 关系删除
    for r in plan["relations"]["delete"]:
        rid = r.get("relation_id")
        if not rid:
            failed_items.append({"type": "relation.delete", "data": r, "error": "缺少 relation_id"})
            continue
        parts = rid.split(":", 1)
        if len(parts) != 2 or "->" not in parts[1]:
            failed_items.append({"type": "relation.delete", "data": r, "error": "relation_id 格式错误"})
            continue
        type_id, rest = parts
        source_id, target_id = rest.split("->", 1)
        relation = get_relation_by_entities(source_id, target_id, type_id, db_path)
        if not relation:
            failed_items.append({"type": "relation.delete", "data": r, "error": "关系不存在"})
            continue
        try:
            delete_relation(relation["id"], db_path=db_path)
            deleted_relations.append({"relation_id": rid})
        except Exception as ex:
            failed_items.append({"type": "relation.delete", "data": r, "error": str(ex)})

    return {
        "created_entities": created_entities,
        "updated_entities": updated_entities,
        "deleted_entities": deleted_entities,
        "created_relations": created_relations,
        "updated_relations": updated_relations,
        "deleted_relations": deleted_relations,
        "failed_items": failed_items,
    }


def register(mcp):
    """注册 modify_ontology 工具到 MCP 服务器。"""

    @mcp.tool()
    def modify_ontology(
        description: str = "",
        target_entity_id: Optional[str] = None,
        dry_run: bool = True,
        plan: Optional[dict] = None,
        db_path: Optional[str] = None,
    ) -> dict:
        """根据自然语言描述修改 Ontology。

        Args:
            description: 自然语言描述，例如"把用户登录的描述更新为支持微信扫码"
            target_entity_id: 可选，优先修改的目标实体 ID
            dry_run: 为 True 时只返回变更计划，不写入数据库；为 False 时执行写入
            plan: 可选，传入上一次 dry_run 返回的 plan，dry_run=false 时直接执行该 plan，避免 LLM 两次调用结果不一致
            db_path: 可选，Ontology SQLite 数据库路径

        Returns:
            dry_run=true: {"dry_run": true, "plan": {...}}
            dry_run=false: {"dry_run": false, "created_entities": [...], "updated_entities": [...], ...}
        """
        if plan is None:
            target_context = None
            if target_entity_id:
                target_context = _build_target_context(target_entity_id, db_path)

            plan = plan_ontology_changes(
                description=description,
                target_entity_id=target_entity_id,
                target_entity_context=target_context,
                db_path=db_path,
            )

        valid, errors = _validate_plan(plan, db_path)

        if dry_run:
            return {
                "dry_run": True,
                "plan": plan,
                "validation": {"valid": valid, "errors": errors},
            }

        if not valid:
            return {
                "dry_run": False,
                "success": False,
                "errors": errors,
            }

        result = _execute_modify_plan(plan, db_path)
        result["dry_run"] = False
        result["success"] = len(result.get("failed_items", [])) == 0
        return result

    return modify_ontology
